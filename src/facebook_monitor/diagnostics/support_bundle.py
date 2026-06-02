"""建立可分享的 redacted support bundle。

職責：輸出不含 DB、profile、cookies、secrets、完整貼文內容或完整 webhook 的
診斷 zip，供使用者在需要協助時提供環境與狀態摘要。
"""

from __future__ import annotations

from contextlib import closing
import hashlib
import json
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
import logging
import os
from pathlib import Path
import sqlite3
import zipfile

from facebook_monitor.core.defaults import PYTHON_DIAGNOSTICS_RUNTIME_DEFAULTS
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.redaction import redact_sensitive_text
from facebook_monitor.persistence.invariants import validate_database_invariants
from facebook_monitor.persistence.repositories.notification_outbox import NotificationOutboxRepository
from facebook_monitor.persistence.secret_storage import PlaintextSecretCodec
from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.updates.validation import is_reparse_or_symlink


logger = logging.getLogger(__name__)
SUPPORT_BUNDLE_FILENAME_PREFIX = "facebook-monitor-support-"
SUPPORT_BUNDLE_FILENAME_SUFFIX = ".zip"


@dataclass(frozen=True)
class SupportBundleResult:
    """保存 support bundle 產物位置。"""

    path: Path
    filename: str


def create_support_bundle(
    *,
    paths: RuntimePaths,
    runtime_diagnostics_text: str,
    app_metadata: dict[str, str],
) -> SupportBundleResult:
    """建立 redacted support bundle zip。"""

    generated_at = utc_now().astimezone(timezone.utc)
    filename = f"facebook-monitor-support-{generated_at.strftime('%Y%m%dT%H%M%SZ')}.zip"
    bundle_dir = paths.exports_dir / "support-bundles"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = bundle_dir / filename
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_text(
            archive,
            "README.txt",
            "\n".join(
                [
                    "Facebook Monitor support bundle",
                    "This bundle intentionally excludes the SQLite DB, browser profile, cookies, secrets, logs, and full post/comment text.",
                    "Paths and secret-like values are redacted before writing on a best-effort basis.",
                    "Please review the extracted files before sharing this bundle.",
                    "",
                ]
            ),
        )
        _write_json(
            archive,
            "metadata.json",
            {
                "generated_at": generated_at.isoformat(),
                **app_metadata,
            },
        )
        _write_text(
            archive,
            "runtime_diagnostics.txt",
            redact_sensitive_text(runtime_diagnostics_text),
        )
        _write_json(
            archive,
            "runtime_paths.json",
            _redacted_runtime_paths(paths),
        )
        _write_database_summary(archive, db_path=paths.db_path)
    prune_old_support_bundles(
        bundle_dir,
        max_age_days=PYTHON_DIAGNOSTICS_RUNTIME_DEFAULTS.support_bundle_retention_days,
        max_files=PYTHON_DIAGNOSTICS_RUNTIME_DEFAULTS.support_bundle_max_files,
        now=generated_at,
        preserve=(bundle_path,),
    )
    return SupportBundleResult(path=bundle_path, filename=filename)


@dataclass(frozen=True)
class _SupportBundleCandidate:
    """保存可被 retention 清理的 support bundle 檔案。"""

    path: Path
    mtime: float
    sort_key: tuple[float, str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sort_key", (self.mtime, self.path.name))


def prune_old_support_bundles(
    bundle_dir: Path,
    *,
    max_age_days: int,
    max_files: int,
    now: datetime,
    preserve: tuple[Path, ...] = (),
) -> int:
    """清理舊 support bundle；只處理 allowlisted zip 檔名。"""

    candidates = _list_support_bundle_candidates(bundle_dir)
    candidates.sort(key=lambda candidate: candidate.sort_key, reverse=True)
    preserved_paths = {_support_bundle_path_identity(path) for path in preserve}
    keep_count = max(1, int(max_files))
    keep_paths = {
        _support_bundle_path_identity(candidate.path)
        for candidate in candidates[:keep_count]
    }
    cutoff = now.timestamp() - max(0, int(max_age_days)) * 86400
    deleted_count = 0
    for candidate in candidates:
        path = candidate.path
        path_identity = _support_bundle_path_identity(path)
        if path_identity in preserved_paths:
            continue
        should_delete_for_count = path_identity not in keep_paths
        should_delete_for_age = candidate.mtime < cutoff
        if not (should_delete_for_count or should_delete_for_age):
            continue
        try:
            path.unlink()
            deleted_count += 1
        except OSError:
            logger.warning("Failed to prune old support bundle: %s", path, exc_info=True)
    return deleted_count


def _list_support_bundle_candidates(bundle_dir: Path) -> list[_SupportBundleCandidate]:
    """列出符合 support bundle retention 規則的 regular files。"""

    candidates: list[_SupportBundleCandidate] = []
    for path in bundle_dir.glob(f"{SUPPORT_BUNDLE_FILENAME_PREFIX}*{SUPPORT_BUNDLE_FILENAME_SUFFIX}"):
        try:
            if is_reparse_or_symlink(path) or not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        candidates.append(_SupportBundleCandidate(path=path, mtime=stat.st_mtime))
    return candidates


def _support_bundle_path_identity(path: Path) -> str:
    """回傳 retention 比對用路徑字串，不跟隨 symlink / junction。"""

    identity = str(path.absolute())
    if os.name == "nt":
        return os.path.normcase(identity)
    return identity


def _write_database_summary(archive: zipfile.ZipFile, *, db_path: Path) -> None:
    """寫入只含 counts 與 invariant 結果的 DB 摘要。"""

    if not db_path.is_file():
        _write_json(
            archive,
            "database_summary.json",
            {
                "table_counts": {table_name: 0 for table_name in _SUPPORT_COUNT_TABLES},
                "notification_outbox": _empty_outbox_summary(),
                "invariant_violation_count": 0,
                "invariant_violations": [],
            },
        )
        return
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        table_counts = {
            table_name: int(
                connection.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()[
                    "count"
                ]
            )
            for table_name in _SUPPORT_COUNT_TABLES
        }
        outbox_summary = NotificationOutboxRepository(
            connection,
            secret_codec=PlaintextSecretCodec(),
        ).summarize_all()
        violations = validate_database_invariants(connection)
    _write_json(
        archive,
        "database_summary.json",
        {
            "table_counts": table_counts,
            "notification_outbox": {
                "pending": outbox_summary.pending_count,
                "processing": outbox_summary.processing_count,
                "failed": outbox_summary.failed_count,
                "terminal": outbox_summary.terminal_count,
                "max_attempts": outbox_summary.max_attempts,
            },
            "invariant_violation_count": len(violations),
            "invariant_violations": [
                {
                    "table": violation.table,
                    "row_id_hash": _support_row_id_hash(
                        table=violation.table,
                        row_id=violation.row_id,
                    ),
                    "field": violation.field,
                    "message": violation.message,
                }
                for violation in violations[:100]
            ],
        },
    )


def _empty_outbox_summary() -> dict[str, int]:
    """回傳空 DB 時的 notification outbox 摘要。"""

    return {
        "pending": 0,
        "processing": 0,
        "failed": 0,
        "terminal": 0,
        "max_attempts": 0,
    }


def _support_row_id_hash(*, table: str, row_id: str) -> str:
    """支援包內只輸出 row id 穩定短雜湊，避免外洩 target/item identifiers。"""

    digest = hashlib.sha256(f"{table}:{row_id}".encode("utf-8")).hexdigest()
    return digest[:12]


def _redacted_runtime_paths(paths: RuntimePaths) -> dict[str, str]:
    """整理 runtime paths；所有值先經 redaction。"""

    values = {
        "app_base_dir": paths.app_base_dir,
        "data_dir": paths.data_dir,
        "db_path": paths.db_path,
        "profiles_dir": paths.profiles_dir,
        "profile_dir": paths.profile_dir,
        "logs_dir": paths.logs_dir,
        "runtime_dir": paths.runtime_dir,
        "exports_dir": paths.exports_dir,
        "updates_dir": paths.updates_dir,
    }
    return {
        key: redact_sensitive_text(str(value))
        for key, value in values.items()
    }


def _write_json(
    archive: zipfile.ZipFile,
    name: str,
    payload: object,
) -> None:
    """以穩定 UTF-8 JSON 寫入 zip。"""

    _write_text(
        archive,
        name,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _write_text(archive: zipfile.ZipFile, name: str, content: str) -> None:
    """將文字內容寫入 zip，避免呼叫端重複處理 encoding。"""

    archive.writestr(name, content.encode("utf-8"))


_SUPPORT_COUNT_TABLES = (
    "targets",
    "scan_runs",
    "latest_scan_items",
    "match_history",
    "notification_events",
    "notification_outbox",
    "target_runtime_state",
)
