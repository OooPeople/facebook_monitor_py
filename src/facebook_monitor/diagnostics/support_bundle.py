"""建立可分享的 redacted support bundle。

職責：輸出不含 DB、profile、cookies、secrets、完整貼文內容或完整 webhook 的
診斷 zip，供使用者在需要協助時提供環境、runtime、掃描與通知狀態摘要。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
import uuid
import zipfile

from facebook_monitor.core.defaults import PYTHON_DIAGNOSTICS_RUNTIME_DEFAULTS
from facebook_monitor.core.models import utc_now
from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.diagnostics._support_bundle_constants import SUPPORT_BUNDLE_FILENAME_PREFIX
from facebook_monitor.diagnostics._support_bundle_constants import SUPPORT_BUNDLE_FILENAME_SUFFIX
from facebook_monitor.diagnostics._support_bundle_constants import SUPPORT_BUNDLE_SCHEMA_VERSION
from facebook_monitor.diagnostics._support_bundle_db_collectors import _database_health_payload
from facebook_monitor.diagnostics._support_bundle_db_collectors import _database_summary_payload
from facebook_monitor.diagnostics._support_bundle_db_collectors import _dedupe_summary_payload
from facebook_monitor.diagnostics._support_bundle_db_collectors import _latest_scan_debug_summary_payload
from facebook_monitor.diagnostics._support_bundle_db_collectors import _notification_diagnostics_payload
from facebook_monitor.diagnostics._support_bundle_db_collectors import _scan_summaries_payload
from facebook_monitor.diagnostics._support_bundle_db_collectors import _target_inventory_payload
from facebook_monitor.diagnostics._support_bundle_db_collectors import _target_runtime_states_payload
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
from facebook_monitor.diagnostics._support_bundle_writers import _write_text_section


@dataclass(frozen=True)
class SupportBundleResult:
    """建立支援包後回傳的檔案資訊。"""

    path: Path
    filename: str


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
    try:
        with temp_path.open("xb") as raw_archive:
            with zipfile.ZipFile(raw_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                _write_text(
                    archive,
                    "README.txt",
                    "\n".join(
                        [
                            "Facebook Monitor support bundle",
                            "This bundle intentionally excludes the SQLite DB, browser profile, cookies, secrets, full logs, and full post/comment text.",
                            "It includes bounded redacted log tails, runtime snapshots, scan summaries, notification summaries, and database health checks.",
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
                _write_text_section(
                    archive,
                    sections,
                    name="runtime_diagnostics",
                    filename="runtime_diagnostics.txt",
                    content=_runtime_diagnostics_text(runtime_diagnostics_text, aliases),
                )
                _write_json_section(
                    archive,
                    sections,
                    name="runtime_paths",
                    filename="runtime_paths.json",
                    collect=lambda: _redacted_runtime_paths(paths),
                )
                _write_json_section(
                    archive,
                    sections,
                    name="database_summary",
                    filename="database_summary.json",
                    collect=lambda: _database_summary_payload(paths.db_path),
                )
                _write_json_section(
                    archive,
                    sections,
                    name="database_health",
                    filename="database_health.json",
                    collect=lambda: _database_health_payload(paths),
                )
                _write_json_section(
                    archive,
                    sections,
                    name="target_inventory",
                    filename="target_inventory.json",
                    collect=lambda: _target_inventory_payload(paths.db_path, aliases),
                )
                _write_json_section(
                    archive,
                    sections,
                    name="target_runtime_states",
                    filename="target_runtime_states.json",
                    collect=lambda: _target_runtime_states_payload(
                        paths.db_path,
                        aliases,
                        now=generated_at,
                    ),
                )
                _write_json_section(
                    archive,
                    sections,
                    name="scan_summaries",
                    filename="scan_summaries.json",
                    collect=lambda: _scan_summaries_payload(paths.db_path, aliases),
                )
                _write_json_section(
                    archive,
                    sections,
                    name="latest_scan_debug_summary",
                    filename="latest_scan_debug_summary.json",
                    collect=lambda: _latest_scan_debug_summary_payload(
                        paths.db_path,
                        aliases,
                    ),
                )
                _write_json_section(
                    archive,
                    sections,
                    name="notification_diagnostics",
                    filename="notification_diagnostics.json",
                    collect=lambda: _notification_diagnostics_payload(
                        paths.db_path,
                        aliases,
                    ),
                )
                _write_json_section(
                    archive,
                    sections,
                    name="dedupe_summary",
                    filename="dedupe_summary.json",
                    collect=lambda: _dedupe_summary_payload(paths.db_path, aliases),
                )
                _write_json_section(
                    archive,
                    sections,
                    name="profile_session",
                    filename="profile_session.json",
                    collect=lambda: _profile_session_payload(paths),
                )
                _write_json_section(
                    archive,
                    sections,
                    name="maintenance_update_summary",
                    filename="maintenance_update_summary.json",
                    collect=lambda: _maintenance_update_summary_payload(paths, aliases),
                )
                _write_json_section(
                    archive,
                    sections,
                    name="scheduler_state",
                    filename="scheduler_state.json",
                    collect=lambda: _scheduler_state_payload(scheduler_state or {}, aliases),
                )
                _write_json_section(
                    archive,
                    sections,
                    name="log_tail",
                    filename="log_tail.json",
                    collect=lambda: _log_tail_payload(paths.logs_dir, aliases),
                )
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
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    prune_old_support_bundles(
        bundle_dir,
        max_age_days=PYTHON_DIAGNOSTICS_RUNTIME_DEFAULTS.support_bundle_retention_days,
        max_files=PYTHON_DIAGNOSTICS_RUNTIME_DEFAULTS.support_bundle_max_files,
        preserve=(bundle_path,),
    )
    return SupportBundleResult(path=bundle_path, filename=filename)


def prune_old_support_bundles(
    bundle_dir: Path,
    *,
    max_age_days: int,
    max_files: int,
    now: datetime | None = None,
    preserve: tuple[Path, ...] = (),
) -> int:
    """刪除過期或超出數量上限的 support bundle。"""

    return _prune_old_support_bundles(
        bundle_dir,
        max_age_days=max_age_days,
        max_files=max_files,
        now=now,
        preserve=preserve,
    )
