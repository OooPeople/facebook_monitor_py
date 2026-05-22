"""建立可分享的 redacted support bundle。

職責：輸出不含 DB、profile、cookies、secrets、完整貼文內容或完整 webhook 的
診斷 zip，供使用者在需要協助時提供環境與狀態摘要。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
import zipfile

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.redaction import redact_sensitive_text
from facebook_monitor.persistence.invariants import validate_database_invariants
from facebook_monitor.runtime.paths import RuntimePaths


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
                    "Paths and secret-like values are redacted before writing.",
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
    return SupportBundleResult(path=bundle_path, filename=filename)


def _write_database_summary(archive: zipfile.ZipFile, *, db_path: Path) -> None:
    """寫入只含 counts 與 invariant 結果的 DB 摘要。"""

    with SqliteApplicationContext(db_path) as app_context:
        connection = app_context.repositories.maintenance.connection
        table_counts = {
            table_name: int(
                connection.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()[
                    "count"
                ]
            )
            for table_name in _SUPPORT_COUNT_TABLES
        }
        outbox_summary = app_context.repositories.notification_outbox.summarize_all()
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
                    "row_id": violation.row_id,
                    "field": violation.field,
                    "message": violation.message,
                }
                for violation in violations[:100]
            ],
        },
    )


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
