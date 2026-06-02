"""Support bundle runtime collectors。

職責：整理 profile/session、maintenance update、scheduler snapshot、runtime
paths 與 bounded log tail；log/freeform 只輸出 redacted summary。
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.updates.validation import is_reparse_or_symlink
from facebook_monitor.diagnostics._support_bundle_constants import LOG_TAIL_FILE_NAMES
from facebook_monitor.diagnostics._support_bundle_constants import LOG_TAIL_MAX_BYTES
from facebook_monitor.diagnostics._support_bundle_constants import LOG_TAIL_MAX_LINES
from facebook_monitor.diagnostics._support_bundle_redaction import _SupportBundleAliases
from facebook_monitor.diagnostics._support_bundle_redaction import _freeform_summary
from facebook_monitor.diagnostics._support_bundle_redaction import _log_line_summary
from facebook_monitor.diagnostics._support_bundle_redaction import _redacted_truncated
from facebook_monitor.diagnostics._support_bundle_redaction import _safe_reason_code
from facebook_monitor.diagnostics._support_bundle_redaction import _safe_exception_summary
from facebook_monitor.diagnostics._support_bundle_utils import _json_dict
from facebook_monitor.diagnostics._support_bundle_utils import _number_or_zero
from facebook_monitor.diagnostics._support_bundle_utils import _readonly_connection
from facebook_monitor.diagnostics._support_bundle_utils import _table_names
from facebook_monitor.diagnostics._support_bundle_utils import _tupleish


def _profile_session_payload(paths: RuntimePaths) -> dict[str, object]:
    """建立 profile session 與 profile lease 摘要，不讀取 cookie 值。"""

    profile_dir = paths.profile_dir
    payload: dict[str, object] = {
        "profile_dir_exists": profile_dir.is_dir(),
        "profile_lock_exists": (profile_dir / ".facebook_monitor_profile.lock").exists(),
        "profile_lock_owner": {},
        "profile_session_status": {"state": "unknown"},
    }
    lock_path = profile_dir / ".facebook_monitor_profile.lock"
    if lock_path.is_file() and not is_reparse_or_symlink(lock_path):
        try:
            payload["profile_lock_owner"] = _freeform_summary(
                lock_path.read_text(encoding="utf-8", errors="replace")
            )
        except OSError as exc:
            payload["profile_lock_owner"] = _safe_exception_summary(exc)
    if not paths.db_path.is_file():
        return payload | {"database_available": False}
    with _readonly_connection(paths.db_path) as connection:
        if "app_settings" not in _table_names(connection):
            return payload | {"database_available": True}
        row = connection.execute(
            "SELECT value, updated_at FROM app_settings WHERE key = ?",
            ("profile_session_status",),
        ).fetchone()
    if not row:
        return payload | {"database_available": True}
    status = _json_dict(row["value"])
    payload["profile_session_status"] = {
        "state": _redacted_truncated(str(status.get("state") or "unknown")),
        "reason": _safe_reason_code(str(status.get("reason") or "")),
        "source": _safe_reason_code(str(status.get("source") or "")),
        "updated_at": _safe_reason_code(str(status.get("updated_at") or "")),
        "row_updated_at": str(row["updated_at"] or ""),
    }
    return payload | {"database_available": True}


def _maintenance_update_summary_payload(
    paths: RuntimePaths,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """建立 maintenance、cover image 與 pending update 摘要。"""

    payload: dict[str, object] = {
        "pending_update": _pending_update_summary(paths.runtime_dir / "pending_update.json"),
        "cover_image_refresh": {"available": False, "states": []},
    }
    if not paths.db_path.is_file():
        return payload | {"database_available": False}
    with _readonly_connection(paths.db_path) as connection:
        if "target_cover_image_refresh_state" in _table_names(connection):
            rows = connection.execute(
                """
                SELECT *
                FROM target_cover_image_refresh_state
                ORDER BY updated_at DESC
                LIMIT 100
                """
            ).fetchall()
            status_counts = Counter(str(row["status"] or "") for row in rows)
            payload["cover_image_refresh"] = {
                "available": True,
                "status_counts": dict(sorted(status_counts.items())),
                "states": [
                    {
                        "target": aliases.alias("target", row["target_id"]),
                        "status": str(row["status"] or ""),
                        "requested_at": str(row["requested_at"] or ""),
                        "last_attempted_at": str(row["last_attempted_at"] or ""),
                        "last_succeeded_at": str(row["last_succeeded_at"] or ""),
                        "last_failed_at": str(row["last_failed_at"] or ""),
                        "last_result": str(row["last_result"] or ""),
                        "changed": bool(row["changed"]),
                        "error": _freeform_summary(str(row["error"] or "")),
                        "has_reported_url": bool(str(row["last_reported_url"] or "")),
                        "has_resolved_url": bool(str(row["last_resolved_url"] or "")),
                        "updated_at": str(row["updated_at"] or ""),
                    }
                    for row in rows
                ],
            }
    return payload | {"database_available": True}


def _scheduler_state_payload(
    state: dict[str, object],
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """整理 scheduler manager state，避免輸出 raw target ids。"""

    if not state:
        return {"available": False, "reason": "scheduler_state_unavailable"}
    queued_target_ids = _tupleish(state.get("queued_target_ids"))
    worker_ids = _tupleish(state.get("worker_ids"))
    return {
        "available": True,
        "running": bool(state.get("running", False)),
        "interval_seconds": _number_or_zero(state.get("interval_seconds")),
        "lifecycle_state": str(state.get("lifecycle_state") or ""),
        "last_cycle_at": str(state.get("last_cycle_at") or ""),
        "last_error": _freeform_summary(
            str(state.get("last_error") or ""),
            aliases=aliases,
        ),
        "max_concurrent_scans": int(_number_or_zero(state.get("max_concurrent_scans"))),
        "current_running_count": int(_number_or_zero(state.get("current_running_count"))),
        "current_queued_count": int(_number_or_zero(state.get("current_queued_count"))),
        "queue_length": int(_number_or_zero(state.get("queue_length"))),
        "queued_targets": [aliases.alias("target", target_id) for target_id in queued_target_ids],
        "workers": [aliases.alias("worker", worker_id) for worker_id in worker_ids],
        "page_pool_size": int(_number_or_zero(state.get("page_pool_size"))),
        "last_opened_page_count": int(_number_or_zero(state.get("last_opened_page_count"))),
        "last_reused_page_count": int(_number_or_zero(state.get("last_reused_page_count"))),
        "last_closed_page_count": int(_number_or_zero(state.get("last_closed_page_count"))),
        "resident_browser_alive": bool(state.get("resident_browser_alive", False)),
        "recovered_runtime_count": int(_number_or_zero(state.get("recovered_runtime_count"))),
        "notification_dispatch_count": int(
            _number_or_zero(state.get("notification_dispatch_count"))
        ),
        "worker_health_ok": bool(state.get("worker_health_ok", True)),
    }


def _log_tail_payload(
    logs_dir: Path,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """讀取固定檔名的 redacted log tail。"""

    files = []
    for file_name in LOG_TAIL_FILE_NAMES:
        path = logs_dir / file_name
        file_payload: dict[str, object] = {
            "file": file_name,
            "exists": False,
            "truncated_bytes": False,
            "truncated_lines": False,
            "lines": [],
        }
        if not path.is_file() or is_reparse_or_symlink(path):
            files.append(file_payload)
            continue
        try:
            stat = path.stat()
            with path.open("rb") as file:
                file.seek(max(stat.st_size - LOG_TAIL_MAX_BYTES, 0))
                data = file.read(LOG_TAIL_MAX_BYTES)
        except OSError as exc:
            file_payload["error"] = _safe_exception_summary(exc)
            files.append(file_payload)
            continue
        file_payload["exists"] = True
        file_payload["size_bytes"] = stat.st_size
        file_payload["truncated_bytes"] = stat.st_size > len(data)
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        selected_lines = lines[-LOG_TAIL_MAX_LINES:]
        file_payload["line_count"] = len(lines)
        file_payload["truncated_lines"] = len(lines) > len(selected_lines)
        file_payload["lines"] = [
            _log_line_summary(line, aliases=aliases)
            for line in selected_lines
        ]
        files.append(file_payload)
    return {
        "available": True,
        "max_bytes_per_file": LOG_TAIL_MAX_BYTES,
        "max_lines_per_file": LOG_TAIL_MAX_LINES,
        "files": files,
    }


def _pending_update_summary(path: Path) -> dict[str, object]:
    """讀取 pending update handoff 的安全摘要。"""

    payload: dict[str, object] = {"exists": False}
    if not path.is_file() or is_reparse_or_symlink(path):
        return payload
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": True, "error": _safe_exception_summary(exc)}
    if not isinstance(raw, dict):
        return {"exists": True, "error": "pending_update_not_object"}
    return {
        "exists": True,
        "version": _redacted_truncated(str(raw.get("version") or "")),
        "repository": _redacted_truncated(str(raw.get("repository") or "")),
        "platform": _redacted_truncated(str(raw.get("platform") or "")),
        "zip_path_present": bool(raw.get("zip_path")),
        "manifest_path_present": bool(raw.get("manifest_path")),
        "signature_path_present": bool(raw.get("manifest_signature_path")),
        "sha256_prefix": _redacted_truncated(str(raw.get("sha256") or "")[:12]),
    }



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
        key: _redacted_truncated(str(value))
        for key, value in values.items()
    }
