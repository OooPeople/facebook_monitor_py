"""Support bundle target and runtime collectors。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
import sqlite3

from facebook_monitor.core.external_url_policy import sanitize_facebook_group_cover_image_url
from facebook_monitor.diagnostics._support_bundle_db_common import _include_keyword_group_summary
from facebook_monitor.diagnostics._support_bundle_db_common import _row_freeform_summary
from facebook_monitor.diagnostics._support_bundle_db_common import _row_int
from facebook_monitor.diagnostics._support_bundle_db_common import _row_text
from facebook_monitor.diagnostics._support_bundle_redaction import _SupportBundleAliases
from facebook_monitor.diagnostics._support_bundle_redaction import _freeform_summary
from facebook_monitor.diagnostics._support_bundle_utils import _age_seconds
from facebook_monitor.diagnostics._support_bundle_utils import _json_list_length
from facebook_monitor.diagnostics._support_bundle_utils import _optional_bool
from facebook_monitor.diagnostics._support_bundle_utils import _optional_int
from facebook_monitor.diagnostics._support_bundle_utils import _readonly_connection
from facebook_monitor.diagnostics._support_bundle_utils import _table_names
from facebook_monitor.diagnostics.cover_image_hosts import collect_cover_image_host_report


def _target_inventory_payload(
    db_path: Path,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """建立 target 與 config 的安全 inventory。"""

    if not db_path.is_file():
        return {"available": False, "reason": "database_missing", "targets": []}
    with _readonly_connection(db_path) as connection:
        table_names = _table_names(connection)
        if "targets" not in table_names:
            return {"available": False, "reason": "targets_table_missing", "targets": []}
        join_config = "target_configs" in table_names
        rows = connection.execute(
            f"""
            SELECT
                targets.id, targets.name, targets.target_kind, targets.group_name,
                targets.group_cover_image_url, targets.metadata_status,
                targets.metadata_error, targets.enabled, targets.paused,
                targets.worker_mode, targets.created_at, targets.updated_at,
                {"target_configs.include_keywords" if join_config else "''"} AS include_keywords,
                {"target_configs.include_keyword_groups" if join_config else "'[]'"} AS include_keyword_groups,
                {"target_configs.exclude_keywords" if join_config else "''"} AS exclude_keywords,
                {"target_configs.exclude_ignore_phrases" if join_config else "'[]'"} AS exclude_ignore_phrases,
                {"target_configs.min_refresh_sec" if join_config else "NULL"} AS min_refresh_sec,
                {"target_configs.max_refresh_sec" if join_config else "NULL"} AS max_refresh_sec,
                {"target_configs.jitter_enabled" if join_config else "NULL"} AS jitter_enabled,
                {"target_configs.fixed_refresh_sec" if join_config else "NULL"} AS fixed_refresh_sec,
                {"target_configs.max_items_per_scan" if join_config else "NULL"} AS max_items_per_scan,
                {"target_configs.auto_load_more" if join_config else "NULL"} AS auto_load_more,
                {"target_configs.auto_adjust_sort" if join_config else "NULL"} AS auto_adjust_sort,
                {"target_configs.enable_desktop_notification" if join_config else "NULL"} AS enable_desktop_notification,
                {"target_configs.enable_ntfy" if join_config else "NULL"} AS enable_ntfy,
                {"target_configs.ntfy_topic" if join_config else "''"} AS ntfy_topic,
                {"target_configs.enable_discord_notification" if join_config else "NULL"} AS enable_discord_notification,
                {"target_configs.discord_webhook" if join_config else "''"} AS discord_webhook
            FROM targets
            {"LEFT JOIN target_configs ON target_configs.target_id = targets.id" if join_config else ""}
            ORDER BY targets.created_at, targets.id
            """
        ).fetchall()
    targets = []
    kind_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    for row in rows:
        target_alias = aliases.alias("target", row["id"])
        kind = str(row["target_kind"] or "")
        kind_counts[kind] += 1
        status_key = f"enabled={int(row['enabled'] or 0)};paused={int(row['paused'] or 0)}"
        status_counts[status_key] += 1
        cover_image_url = str(row["group_cover_image_url"] or "")
        cover_image_result = sanitize_facebook_group_cover_image_url(cover_image_url)
        targets.append(
            {
                "target": target_alias,
                "kind": kind,
                "enabled": bool(row["enabled"]),
                "paused": bool(row["paused"]),
                "worker_mode": str(row["worker_mode"] or ""),
                "metadata_status": str(row["metadata_status"] or ""),
                "metadata_error": _freeform_summary(str(row["metadata_error"] or "")),
                "name_length": len(str(row["name"] or "")),
                "group_name_length": len(str(row["group_name"] or "")),
                "has_cover_image_url": bool(cover_image_url),
                "has_valid_cover_image_url": cover_image_result.ok,
                "cover_image_reject_reason": (
                    "" if cover_image_result.ok else cover_image_result.reason
                ),
                "created_at": str(row["created_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
                "config": _target_config_summary(row),
            }
        )
    return {
        "available": True,
        "target_count": len(targets),
        "kind_counts": dict(sorted(kind_counts.items())),
        "enabled_paused_counts": dict(sorted(status_counts.items())),
        "targets": targets,
    }


def _cover_image_hosts_payload(db_path: Path) -> dict[str, object]:
    """建立不含完整 URL 的 cover image host 統計。"""

    if not db_path.is_file():
        return {"available": False, "reason": "database_missing"}
    with _readonly_connection(db_path) as connection:
        return collect_cover_image_host_report(connection)


def _target_runtime_states_payload(
    db_path: Path,
    aliases: _SupportBundleAliases,
    *,
    now: datetime,
) -> dict[str, object]:
    """建立 target runtime ownership snapshot。"""

    if not db_path.is_file():
        return {"available": False, "reason": "database_missing", "states": []}
    with _readonly_connection(db_path) as connection:
        if "target_runtime_state" not in _table_names(connection):
            return {
                "available": False,
                "reason": "target_runtime_state_table_missing",
                "states": [],
            }
        rows = connection.execute(
            "SELECT * FROM target_runtime_state ORDER BY updated_at, target_id"
        ).fetchall()
    return {
        "available": True,
        "status_counts": dict(sorted(_runtime_status_counts(rows).items())),
        "states": [
            _target_runtime_state_payload(row, aliases, now=now)
            for row in rows
        ],
    }


def _runtime_status_counts(rows: list[sqlite3.Row]) -> Counter[str]:
    """彙總 target runtime status，不碰 redaction 或輸出 schema。"""

    return Counter(_row_text(row, "runtime_status") for row in rows)


def _target_runtime_state_payload(
    row: sqlite3.Row,
    aliases: _SupportBundleAliases,
    *,
    now: datetime,
) -> dict[str, object]:
    """整理單筆 target_runtime_state row。"""

    return {
        "target": aliases.alias("target", row["target_id"]),
        "desired_state": _row_text(row, "desired_state"),
        "runtime_status": _row_text(row, "runtime_status"),
        "scan_requested_at": _row_text(row, "scan_requested_at"),
        "last_enqueued_at": _row_text(row, "last_enqueued_at"),
        "last_started_at": _row_text(row, "last_started_at"),
        "last_finished_at": _row_text(row, "last_finished_at"),
        "last_heartbeat_at": _row_text(row, "last_heartbeat_at"),
        "last_heartbeat_age_seconds": _age_seconds(row["last_heartbeat_at"], now),
        "last_page_reloaded_at": _row_text(row, "last_page_reloaded_at"),
        "display_next_due_at": _row_text(row, "display_next_due_at"),
        "scan_guard_count": _row_int(row, "scan_guard_count"),
        "active_worker": aliases.alias("worker", row["active_worker_id"]),
        "active_page": aliases.alias("page", row["active_page_id"]),
        "enqueue_reason": _row_freeform_summary(row, "enqueue_reason", aliases),
        "last_skip_reason": _row_freeform_summary(row, "last_skip_reason", aliases),
        "last_error": _row_freeform_summary(row, "last_error", aliases),
        "consecutive_failure_reason": _row_freeform_summary(
            row,
            "consecutive_failure_reason",
            aliases,
        ),
        "consecutive_failure_count": _row_int(row, "consecutive_failure_count"),
        "consecutive_scan_skip_reason": _row_freeform_summary(
            row,
            "consecutive_scan_skip_reason",
            aliases,
        ),
        "consecutive_scan_skip_count": _row_int(row, "consecutive_scan_skip_count"),
        "updated_at": _row_text(row, "updated_at"),
    }


def _target_config_summary(row: sqlite3.Row) -> dict[str, object]:
    """整理 target config，不輸出 keyword 或 endpoint 原文。"""

    group_summary = _include_keyword_group_summary(str(row["include_keyword_groups"] or ""))
    return {
        "include_keyword_count": _json_list_length(str(row["include_keywords"] or "")),
        "include_keyword_group_count": group_summary["group_count"],
        "include_keyword_group_nonempty_count": group_summary["nonempty_group_count"],
        "include_keyword_group_keyword_count": group_summary["keyword_count"],
        "exclude_keyword_count": _json_list_length(str(row["exclude_keywords"] or "")),
        "exclude_ignore_phrase_count": _json_list_length(
            str(row["exclude_ignore_phrases"] or "")
        ),
        "min_refresh_sec": _optional_int(row["min_refresh_sec"]),
        "max_refresh_sec": _optional_int(row["max_refresh_sec"]),
        "fixed_refresh_sec": _optional_int(row["fixed_refresh_sec"]),
        "jitter_enabled": _optional_bool(row["jitter_enabled"]),
        "max_items_per_scan": _optional_int(row["max_items_per_scan"]),
        "auto_load_more": _optional_bool(row["auto_load_more"]),
        "auto_adjust_sort": _optional_bool(row["auto_adjust_sort"]),
        "enable_desktop_notification": _optional_bool(row["enable_desktop_notification"]),
        "enable_ntfy": _optional_bool(row["enable_ntfy"]),
        "ntfy_topic_present": bool(str(row["ntfy_topic"] or "")),
        "enable_discord_notification": _optional_bool(row["enable_discord_notification"]),
        "discord_webhook_present": bool(str(row["discord_webhook"] or "")),
    }


__all__ = [
    "_cover_image_hosts_payload",
    "_target_inventory_payload",
    "_target_runtime_states_payload",
]
