"""建立可分享的 redacted support bundle。

職責：輸出不含 DB、profile、cookies、secrets、完整貼文內容或完整 webhook 的
診斷 zip，供使用者在需要協助時提供環境、runtime、掃描與通知狀態摘要。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
import logging
import os
from pathlib import Path
from typing import BinaryIO
from typing import Callable
from typing import Literal
from typing import Any
import uuid
import zipfile

from facebook_monitor.core.defaults import PYTHON_DIAGNOSTICS_RUNTIME_DEFAULTS
from facebook_monitor.core.models import utc_now
from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.diagnostics._support_bundle_constants import SUPPORT_BUNDLE_FILENAME_PREFIX
from facebook_monitor.diagnostics._support_bundle_constants import SUPPORT_BUNDLE_FILENAME_SUFFIX
from facebook_monitor.diagnostics._support_bundle_constants import SUPPORT_BUNDLE_SCHEMA_VERSION
from facebook_monitor.diagnostics._support_bundle_database_collectors import _database_health_payload
from facebook_monitor.diagnostics._support_bundle_database_collectors import _database_summary_payload
from facebook_monitor.diagnostics._support_bundle_dedupe_collectors import _dedupe_summary_payload
from facebook_monitor.diagnostics._support_bundle_notification_collectors import _notification_diagnostics_payload
from facebook_monitor.diagnostics._support_bundle_scan_collectors import _latest_scan_debug_summary_payload
from facebook_monitor.diagnostics._support_bundle_scan_collectors import _scan_summaries_payload
from facebook_monitor.diagnostics._support_bundle_target_collectors import _cover_image_hosts_payload
from facebook_monitor.diagnostics._support_bundle_target_collectors import _target_inventory_payload
from facebook_monitor.diagnostics._support_bundle_target_collectors import _target_runtime_states_payload
from facebook_monitor.diagnostics._support_bundle_redaction import _SupportBundleAliases
from facebook_monitor.diagnostics._support_bundle_redaction import _runtime_diagnostics_text
from facebook_monitor.diagnostics._support_bundle_redaction import _sanitize_app_metadata
from facebook_monitor.diagnostics._support_bundle_retention import prune_old_support_bundles as _prune_old_support_bundles
from facebook_monitor.diagnostics._support_bundle_runtime_collectors import _log_tail_payload
from facebook_monitor.diagnostics._support_bundle_runtime_collectors import _maintenance_update_summary_payload
from facebook_monitor.diagnostics._support_bundle_runtime_collectors import _profile_session_payload
from facebook_monitor.diagnostics._support_bundle_runtime_collectors import _redacted_runtime_paths
from facebook_monitor.diagnostics._support_bundle_runtime_collectors import _scheduler_state_payload
from facebook_monitor.diagnostics._support_bundle_writers import _BundleSectionStatus
from facebook_monitor.diagnostics._support_bundle_writers import _write_json
from facebook_monitor.diagnostics._support_bundle_writers import _write_json_section
from facebook_monitor.diagnostics._support_bundle_writers import _write_text
from facebook_monitor.diagnostics._support_bundle_writers import _write_text_section_from_collect


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SupportBundleResult:
    """建立支援包後回傳的檔案資訊。"""

    path: Path
    filename: str


@dataclass(frozen=True)
class _SupportBundleContext:
    """保存 registry collector 需要的支援包上下文。"""

    paths: RuntimePaths
    generated_at: datetime
    runtime_diagnostics_text: str
    scheduler_state: dict[str, object]
    aliases: _SupportBundleAliases


@dataclass(frozen=True)
class _SupportBundleSection:
    """宣告一個 support bundle optional section。"""

    name: str
    filename: str
    kind: Literal["json", "text"]
    collect: Callable[[_SupportBundleContext], Any]


_SUPPORT_BUNDLE_SECTIONS: tuple[_SupportBundleSection, ...] = (
    _SupportBundleSection(
        name="runtime_diagnostics",
        filename="runtime_diagnostics.txt",
        kind="text",
        collect=lambda context: _runtime_diagnostics_text(
            context.runtime_diagnostics_text,
            context.aliases,
        ),
    ),
    _SupportBundleSection(
        name="runtime_paths",
        filename="runtime_paths.json",
        kind="json",
        collect=lambda context: _redacted_runtime_paths(context.paths),
    ),
    _SupportBundleSection(
        name="database_summary",
        filename="database_summary.json",
        kind="json",
        collect=lambda context: _database_summary_payload(context.paths.db_path),
    ),
    _SupportBundleSection(
        name="database_health",
        filename="database_health.json",
        kind="json",
        collect=lambda context: _database_health_payload(context.paths),
    ),
    _SupportBundleSection(
        name="target_inventory",
        filename="target_inventory.json",
        kind="json",
        collect=lambda context: _target_inventory_payload(
            context.paths.db_path,
            context.aliases,
        ),
    ),
    _SupportBundleSection(
        name="cover_image_hosts",
        filename="cover_image_hosts.json",
        kind="json",
        collect=lambda context: _cover_image_hosts_payload(context.paths.db_path),
    ),
    _SupportBundleSection(
        name="target_runtime_states",
        filename="target_runtime_states.json",
        kind="json",
        collect=lambda context: _target_runtime_states_payload(
            context.paths.db_path,
            context.aliases,
            now=context.generated_at,
        ),
    ),
    _SupportBundleSection(
        name="scan_summaries",
        filename="scan_summaries.json",
        kind="json",
        collect=lambda context: _scan_summaries_payload(
            context.paths.db_path,
            context.aliases,
        ),
    ),
    _SupportBundleSection(
        name="latest_scan_debug_summary",
        filename="latest_scan_debug_summary.json",
        kind="json",
        collect=lambda context: _latest_scan_debug_summary_payload(
            context.paths.db_path,
            context.aliases,
        ),
    ),
    _SupportBundleSection(
        name="notification_diagnostics",
        filename="notification_diagnostics.json",
        kind="json",
        collect=lambda context: _notification_diagnostics_payload(
            context.paths.db_path,
            context.aliases,
        ),
    ),
    _SupportBundleSection(
        name="dedupe_summary",
        filename="dedupe_summary.json",
        kind="json",
        collect=lambda context: _dedupe_summary_payload(
            context.paths.db_path,
            context.aliases,
        ),
    ),
    _SupportBundleSection(
        name="profile_session",
        filename="profile_session.json",
        kind="json",
        collect=lambda context: _profile_session_payload(context.paths),
    ),
    _SupportBundleSection(
        name="maintenance_update_summary",
        filename="maintenance_update_summary.json",
        kind="json",
        collect=lambda context: _maintenance_update_summary_payload(
            context.paths,
            context.aliases,
        ),
    ),
    _SupportBundleSection(
        name="scheduler_state",
        filename="scheduler_state.json",
        kind="json",
        collect=lambda context: _scheduler_state_payload(
            context.scheduler_state,
            context.aliases,
        ),
    ),
    _SupportBundleSection(
        name="log_tail",
        filename="log_tail.json",
        kind="json",
        collect=lambda context: _log_tail_payload(
            context.paths.logs_dir,
            context.aliases,
        ),
    ),
)


def create_support_bundle(
    *,
    paths: RuntimePaths,
    runtime_diagnostics_text: str,
    app_metadata: dict[str, str],
    scheduler_state: dict[str, object] | None = None,
) -> SupportBundleResult:
    """建立 redacted support bundle zip。"""

    generated_at = utc_now().astimezone(timezone.utc)
    timestamp = generated_at.strftime("%Y%m%d-%H%M%S")
    filename = f"{SUPPORT_BUNDLE_FILENAME_PREFIX}{timestamp}{SUPPORT_BUNDLE_FILENAME_SUFFIX}"
    bundle_dir = paths.exports_dir / "support-bundles"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = bundle_dir / filename
    temp_path = bundle_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
    aliases = _SupportBundleAliases()
    sections: list[_BundleSectionStatus] = []
    context = _SupportBundleContext(
        paths=paths,
        generated_at=generated_at,
        runtime_diagnostics_text=runtime_diagnostics_text,
        scheduler_state=scheduler_state or {},
        aliases=aliases,
    )
    try:
        with _open_private_bundle_temp(temp_path) as raw_archive:
            with zipfile.ZipFile(raw_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                _write_text(
                    archive,
                    "README.txt",
                    "\n".join(
                        [
                            "Facebook Monitor support bundle",
                            "This bundle intentionally excludes the SQLite DB, browser profile, cookies, secrets, full logs, and full post/comment text.",
                            "It includes bounded redacted log tails, runtime snapshots, scan summaries, notification summaries, cover image host histograms, and database health checks.",
                            "Paths, URLs, IDs, errors, and secret-like values are redacted or aliased before writing on a best-effort basis.",
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
                        "support_bundle_schema_version": SUPPORT_BUNDLE_SCHEMA_VERSION,
                        **_sanitize_app_metadata(app_metadata),
                    },
                )
                _write_registered_sections(archive, sections, context)
                _write_json(
                    archive,
                    "bundle_manifest.json",
                    {
                        "schema_version": SUPPORT_BUNDLE_SCHEMA_VERSION,
                        "generated_at": generated_at.isoformat(),
                        "sections": [section.to_json() for section in sections],
                        "alias_counts": aliases.aliases_by_namespace(),
                    },
                )
        temp_path.replace(bundle_path)
        _apply_private_bundle_permissions(bundle_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    _prune_old_support_bundles(
        bundle_dir,
        max_age_days=PYTHON_DIAGNOSTICS_RUNTIME_DEFAULTS.support_bundle_retention_days,
        max_files=PYTHON_DIAGNOSTICS_RUNTIME_DEFAULTS.support_bundle_max_files,
        preserve=(bundle_path,),
    )
    return SupportBundleResult(path=bundle_path, filename=filename)


def _write_registered_sections(
    archive: zipfile.ZipFile,
    sections: list[_BundleSectionStatus],
    context: _SupportBundleContext,
) -> None:
    """依 registry 順序寫入 optional sections，保留既有 failure isolation。"""

    for section in _SUPPORT_BUNDLE_SECTIONS:
        if section.kind == "text":
            _write_text_section_from_collect(
                archive,
                sections,
                name=section.name,
                filename=section.filename,
                collect=lambda section=section: section.collect(context),
            )
            continue
        _write_json_section(
            archive,
            sections,
            name=section.name,
            filename=section.filename,
            collect=lambda section=section: section.collect(context),
        )


def _apply_private_bundle_permissions(path: Path) -> None:
    """Best-effort 將 support bundle artifact 設成僅 owner 可讀寫。"""

    try:
        path.chmod(0o600)
    except OSError:
        logger.debug("failed to apply private support bundle permissions", exc_info=True)


def _open_private_bundle_temp(path: Path) -> BinaryIO:
    """以 private mode exclusive-create 開啟 temp bundle artifact。"""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(path, flags, 0o600)
    try:
        return os.fdopen(fd, "wb")
    except Exception:
        os.close(fd)
        raise
