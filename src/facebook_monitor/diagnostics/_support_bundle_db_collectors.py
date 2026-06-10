"""Support bundle database-backed collectors。

職責：從 SQLite 唯讀整理 DB health、target/runtime、scan、latest item、
notification 與 dedupe 摘要；所有 raw text/id 必須經 redaction helpers。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
import sqlite3

from facebook_monitor.persistence.invariants import DatabaseInvariantViolation
from facebook_monitor.persistence.invariants import validate_database_invariants
from facebook_monitor.persistence.repositories.notification_outbox import NotificationOutboxRepository
from facebook_monitor.persistence.schema import MIN_SUPPORTED_SCHEMA_VERSION
from facebook_monitor.persistence.schema import SCHEMA_VERSION
from facebook_monitor.persistence.secret_storage import PlaintextSecretCodec
from facebook_monitor.persistence.sqlite_codec import read_schema_version
from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.updates.validation import is_reparse_or_symlink
from facebook_monitor.diagnostics._support_bundle_constants import LATEST_ITEM_SAMPLE_LIMIT_PER_TARGET
from facebook_monitor.diagnostics._support_bundle_constants import RECENT_NOTIFICATION_SAMPLE_LIMIT
from facebook_monitor.diagnostics._support_bundle_constants import RECENT_SCAN_LIMIT_PER_TARGET
from facebook_monitor.diagnostics._support_bundle_redaction import _SupportBundleAliases
from facebook_monitor.diagnostics._support_bundle_redaction import _failure_reason_from_error
from facebook_monitor.diagnostics._support_bundle_redaction import _freeform_summary
from facebook_monitor.diagnostics._support_bundle_redaction import _merge_debug_metadata_counts
from facebook_monitor.diagnostics._support_bundle_redaction import _metadata_text
from facebook_monitor.diagnostics._support_bundle_redaction import _reason_count_bucket
from facebook_monitor.diagnostics._support_bundle_redaction import _safe_exception_summary
from facebook_monitor.diagnostics._support_bundle_redaction import _safe_metadata_key_label
from facebook_monitor.diagnostics._support_bundle_redaction import _sanitize_metadata
from facebook_monitor.diagnostics._support_bundle_redaction import _support_row_id_hash
from facebook_monitor.diagnostics._support_bundle_utils import _age_seconds
from facebook_monitor.diagnostics._support_bundle_utils import _isoformat_or_empty
from facebook_monitor.diagnostics._support_bundle_utils import _json_dict
from facebook_monitor.diagnostics._support_bundle_utils import _json_list
from facebook_monitor.diagnostics._support_bundle_utils import _json_list_length
from facebook_monitor.diagnostics._support_bundle_utils import _optional_bool
from facebook_monitor.diagnostics._support_bundle_utils import _optional_int
from facebook_monitor.diagnostics._support_bundle_utils import _readonly_connection
from facebook_monitor.diagnostics._support_bundle_utils import _table_columns
from facebook_monitor.diagnostics._support_bundle_utils import _table_count
from facebook_monitor.diagnostics._support_bundle_utils import _table_names


@dataclass(frozen=True)
class _ScanRunBundleRow:
    """scan_runs row 與衍生彙總 key，避免 payload builder 重複解析 metadata。"""

    row: sqlite3.Row
    metadata: dict[str, object]
    status: str
    stop_reason: str
    failure_reason: str


def _database_summary_payload(db_path: Path) -> dict[str, object]:
    """建立只含 counts 與 invariant 結果的 DB 摘要。"""

    if not db_path.is_file():
        return {
            "available": False,
            "reason": "database_missing",
            "table_counts": {table_name: 0 for table_name in _SUPPORT_COUNT_TABLES},
            "notification_outbox": _empty_outbox_summary(),
            "invariant_violation_count": 0,
            "invariant_violations": [],
        }
    with _readonly_connection(db_path) as connection:
        connection.row_factory = sqlite3.Row
        table_names = _table_names(connection)
        table_counts = {
            table_name: _table_count(connection, table_name) if table_name in table_names else 0
            for table_name in _SUPPORT_COUNT_TABLES
        }
        outbox_summary = (
            NotificationOutboxRepository(
                connection,
                secret_codec=PlaintextSecretCodec(),
            ).summarize_all()
            if "notification_outbox" in table_names
            else None
        )
        violations: tuple[DatabaseInvariantViolation, ...]
        try:
            violations = validate_database_invariants(connection)
            invariant_error = ""
        except Exception as exc:
            violations = ()
            invariant_error = _safe_exception_summary(exc)
    return {
        "available": True,
        "table_counts": table_counts,
        "notification_outbox": (
            {
                "pending": outbox_summary.pending_count,
                "processing": outbox_summary.processing_count,
                "failed": outbox_summary.failed_count,
                "terminal": outbox_summary.terminal_count,
                "oldest_pending_updated_at": _isoformat_or_empty(
                    outbox_summary.oldest_pending_updated_at
                ),
                "max_attempts": outbox_summary.max_attempts,
            }
            if outbox_summary is not None
            else _empty_outbox_summary()
        ),
        "invariant_violation_count": len(violations),
        "invariant_error": invariant_error,
        "invariant_violations": [
            {
                "table": violation.table,
                "row_id_hash": _support_row_id_hash(
                    table=violation.table,
                    row_id=violation.row_id,
                ),
                "field": violation.field,
                "message": _freeform_summary(violation.message),
            }
            for violation in violations[:100]
        ],
    }


def _database_health_payload(paths: RuntimePaths) -> dict[str, object]:
    """建立 SQLite schema 與檔案健康摘要。"""

    payload: dict[str, object] = {
        "available": paths.db_path.is_file(),
        "schema_version": 0,
        "supported_schema_version": SCHEMA_VERSION,
        "min_supported_schema_version": MIN_SUPPORTED_SCHEMA_VERSION,
        "files": _database_file_summaries(paths.db_path),
        "quick_check": "not_run",
        "tables": {},
        "dashboard_revision": None,
    }
    if not paths.db_path.is_file():
        return payload | {"reason": "database_missing"}
    with _readonly_connection(paths.db_path) as connection:
        table_names = _table_names(connection)
        payload["schema_version"] = read_schema_version(connection)
        payload["tables"] = {
            table_name: {
                "exists": table_name in table_names,
                "columns": _table_columns(connection, table_name)
                if table_name in table_names
                else [],
            }
            for table_name in _SUPPORT_COUNT_TABLES
        }
        try:
            row = connection.execute("PRAGMA quick_check(1)").fetchone()
            payload["quick_check"] = str(row[0] if row else "")
        except sqlite3.DatabaseError as exc:
            payload["quick_check"] = _safe_exception_summary(exc)
        if "dashboard_revision" in table_names:
            row = connection.execute(
                "SELECT revision, updated_at FROM dashboard_revision WHERE id = 1"
            ).fetchone()
            if row:
                payload["dashboard_revision"] = {
                    "revision": str(row["revision"]),
                    "updated_at": str(row["updated_at"] or ""),
                }
    return payload


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
                "has_cover_image_url": bool(str(row["group_cover_image_url"] or "")),
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


def _scan_summaries_payload(
    db_path: Path,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """建立最近 scan runs 的 redacted 摘要。"""

    if not db_path.is_file():
        return {"available": False, "reason": "database_missing", "runs": []}
    with _readonly_connection(db_path) as connection:
        if "scan_runs" not in _table_names(connection):
            return {"available": False, "reason": "scan_runs_table_missing", "runs": []}
        rows = connection.execute(
            """
            SELECT *
            FROM (
                SELECT scan_runs.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY target_id
                           ORDER BY id DESC
                       ) AS row_number
                FROM scan_runs
            )
            WHERE row_number <= ?
            ORDER BY target_id, id DESC
            """,
            (RECENT_SCAN_LIMIT_PER_TARGET,),
        ).fetchall()
    scan_rows = [_scan_run_bundle_row(row) for row in rows]
    status_counts, stop_reason_counts, failure_reason_counts = _scan_run_counts(scan_rows)
    return {
        "available": True,
        "limit_per_target": RECENT_SCAN_LIMIT_PER_TARGET,
        "status_counts": dict(sorted(status_counts.items())),
        "stop_reason_counts": dict(sorted(stop_reason_counts.items())),
        "failure_reason_counts": dict(sorted(failure_reason_counts.items())),
        "runs": [
            _scan_run_payload(scan_row, aliases)
            for scan_row in scan_rows
        ],
    }


def _scan_run_bundle_row(row: sqlite3.Row) -> _ScanRunBundleRow:
    """整理單筆 scan_runs row 與後續 count 需要的 reason bucket。"""

    metadata = _json_dict(row["metadata"])
    return _ScanRunBundleRow(
        row=row,
        metadata=metadata,
        status=_row_text(row, "status"),
        stop_reason=_reason_count_bucket(
            _metadata_text(metadata, "stop_reason")
            or _metadata_text(metadata, "skip_reason")
        ),
        failure_reason=_reason_count_bucket(
            _metadata_text(metadata, "reason")
            or _failure_reason_from_error(_row_text(row, "error_message"))
        ),
    )


def _scan_run_counts(
    scan_rows: list[_ScanRunBundleRow],
) -> tuple[Counter[str], Counter[str], Counter[str]]:
    """彙總最近 scan runs 的狀態、停止原因與失敗原因。"""

    status_counts: Counter[str] = Counter()
    stop_reason_counts: Counter[str] = Counter()
    failure_reason_counts: Counter[str] = Counter()
    for scan_row in scan_rows:
        status_counts[scan_row.status] += 1
        if scan_row.stop_reason:
            stop_reason_counts[scan_row.stop_reason] += 1
        if scan_row.failure_reason:
            failure_reason_counts[scan_row.failure_reason] += 1
    return status_counts, stop_reason_counts, failure_reason_counts


def _scan_run_payload(
    scan_row: _ScanRunBundleRow,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """整理單筆 scan_runs support bundle payload。"""

    row = scan_row.row
    return {
        "scan_run": aliases.alias("scan_run", row["id"]),
        "target": aliases.alias("target", row["target_id"]),
        "started_at": _row_text(row, "started_at"),
        "finished_at": _row_text(row, "finished_at"),
        "status": scan_row.status,
        "item_count": _row_int(row, "item_count"),
        "matched_count": _row_int(row, "matched_count"),
        "worker_mode": _row_text(row, "worker_mode"),
        "error_message": _row_freeform_summary(row, "error_message", aliases),
        "metadata": _sanitize_metadata(scan_row.metadata),
    }


def _latest_scan_debug_summary_payload(
    db_path: Path,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """彙整 latest scan items 的 debug metadata，不輸出內容文字。"""

    if not db_path.is_file():
        return {"available": False, "reason": "database_missing", "targets": []}
    with _readonly_connection(db_path) as connection:
        if "latest_scan_items" not in _table_names(connection):
            return {
                "available": False,
                "reason": "latest_scan_items_table_missing",
                "targets": [],
            }
        rows = connection.execute(
            """
            SELECT *
            FROM (
                SELECT latest_scan_items.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY target_id
                           ORDER BY item_index
                       ) AS row_number
                FROM latest_scan_items
            )
            WHERE row_number <= ?
            ORDER BY target_id, item_index
            """,
            (LATEST_ITEM_SAMPLE_LIMIT_PER_TARGET,),
        ).fetchall()
        aggregate_rows = connection.execute(
            """
            SELECT
                target_id,
                COUNT(*) AS item_count,
                SUM(CASE WHEN TRIM(text) = '' THEN 1 ELSE 0 END) AS empty_text_count,
                SUM(CASE WHEN TRIM(matched_keyword) != '' THEN 1 ELSE 0 END)
                    AS matched_item_count
            FROM latest_scan_items
            GROUP BY target_id
            ORDER BY target_id
            """
        ).fetchall()
    targets = _latest_scan_targets_from_aggregates(aggregate_rows, aliases)
    for row in rows:
        _append_latest_scan_sample(targets, row, aliases)
    return {
        "available": True,
        "sample_limit_per_target": LATEST_ITEM_SAMPLE_LIMIT_PER_TARGET,
        "targets": list(targets.values()),
    }


def _latest_scan_targets_from_aggregates(
    rows: list[sqlite3.Row],
    aliases: _SupportBundleAliases,
) -> dict[str, dict[str, object]]:
    """依 target 建立 latest scan debug aggregate payload。"""

    return {
        _row_text(row, "target_id"): _latest_scan_target_payload(row, aliases)
        for row in rows
    }


def _latest_scan_target_payload(
    row: sqlite3.Row,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """整理單一 target 的 latest_scan_items aggregate row。"""

    return {
        "target": aliases.alias("target", row["target_id"]),
        "item_count": _row_int(row, "item_count"),
        "empty_text_count": _row_int(row, "empty_text_count"),
        "matched_item_count": _row_int(row, "matched_item_count"),
        "debug_key_counts": {},
        "debug_value_counts": {},
        "samples": [],
    }


def _empty_latest_scan_target_payload(
    target_id: str,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """建立沒有 aggregate row 時的 latest scan target payload。"""

    return {
        "target": aliases.alias("target", target_id),
        "item_count": 0,
        "empty_text_count": 0,
        "matched_item_count": 0,
        "debug_key_counts": {},
        "debug_value_counts": {},
        "samples": [],
    }


def _append_latest_scan_sample(
    targets: dict[str, dict[str, object]],
    row: sqlite3.Row,
    aliases: _SupportBundleAliases,
) -> None:
    """把單筆 latest_scan_items sample 併入對應 target payload。"""

    target_id = _row_text(row, "target_id")
    target_payload = targets.setdefault(
        target_id,
        _empty_latest_scan_target_payload(target_id, aliases),
    )
    metadata = _json_dict(row["debug_metadata"])
    _merge_debug_metadata_counts(target_payload, metadata)
    samples = target_payload["samples"]
    if isinstance(samples, list):
        samples.append(_latest_scan_sample_payload(row, aliases, metadata))


def _latest_scan_sample_payload(
    row: sqlite3.Row,
    aliases: _SupportBundleAliases,
    metadata: dict[str, object],
) -> dict[str, object]:
    """整理單筆 latest_scan_items sample，不輸出 raw text/id/url。"""

    return {
        "item": aliases.alias("item", row["item_key"]),
        "scan_run": aliases.alias("scan_run", row["scan_run_id"]),
        "kind": _row_text(row, "item_kind"),
        "item_index": _row_int(row, "item_index"),
        "text_length": len(_row_text(row, "text")),
        "has_author": bool(_row_text(row, "author")),
        "has_permalink": bool(_row_text(row, "permalink")),
        "matched": bool(_row_text(row, "matched_keyword").strip()),
        "debug_keys": sorted(
            _safe_metadata_key_label(key) for key in metadata.keys()
        )[:20],
        "scanned_at": _row_text(row, "scanned_at"),
    }


def _notification_diagnostics_payload(
    db_path: Path,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """建立 notification outbox/events 的安全摘要。"""

    if not db_path.is_file():
        return {"available": False, "reason": "database_missing"}
    with _readonly_connection(db_path) as connection:
        table_names = _table_names(connection)
        payload: dict[str, object] = {"available": True}
        if "notification_outbox" in table_names:
            payload["outbox_counts"] = _notification_outbox_counts(connection, aliases)
            payload["failed_outbox_samples"] = _failed_outbox_samples(connection, aliases)
        else:
            payload["outbox_counts"] = []
            payload["failed_outbox_samples"] = []
        if "notification_events" in table_names:
            payload["event_counts"] = _notification_event_counts(connection, aliases)
            payload["recent_events"] = _recent_notification_events(connection, aliases)
        else:
            payload["event_counts"] = []
            payload["recent_events"] = []
    return payload


def _dedupe_summary_payload(
    db_path: Path,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """建立 baseline / dedupe 相關狀態摘要。"""

    if not db_path.is_file():
        return {"available": False, "reason": "database_missing"}
    with _readonly_connection(db_path) as connection:
        table_names = _table_names(connection)
        payload: dict[str, object] = {"available": True}
        if "scan_scope_state" in table_names:
            rows = connection.execute(
                """
                SELECT scope_id, initialized, updated_at
                FROM scan_scope_state
                ORDER BY updated_at DESC
                LIMIT 100
                """
            ).fetchall()
            payload["scan_scope_state"] = [
                {
                    "scope": aliases.alias("scope", row["scope_id"]),
                    "initialized": bool(row["initialized"]),
                    "updated_at": str(row["updated_at"] or ""),
                }
                for row in rows
            ]
        else:
            payload["scan_scope_state"] = []
        payload["target_dedupe_state"] = _target_dedupe_state_summary(connection, aliases)
        payload["logical_item_counts"] = _logical_item_counts(connection, aliases)
        payload["seen_item_counts"] = _seen_item_counts(connection, aliases)
        payload["notification_dedupe_counts"] = _notification_dedupe_counts(
            connection,
            aliases,
        )
    return payload


def _database_file_summaries(db_path: Path) -> list[dict[str, object]]:
    """回傳 DB / WAL / SHM 檔案大小與 mtime 摘要。"""

    paths = (
        ("app.db", db_path),
        ("app.db-wal", db_path.with_name(f"{db_path.name}-wal")),
        ("app.db-shm", db_path.with_name(f"{db_path.name}-shm")),
    )
    summaries = []
    for label, path in paths:
        summary: dict[str, object] = {"file": label, "exists": False}
        try:
            if path.is_file() and not is_reparse_or_symlink(path):
                stat = path.stat()
                summary.update(
                    {
                        "exists": True,
                        "size_bytes": stat.st_size,
                        "mtime": datetime.fromtimestamp(
                            stat.st_mtime,
                            tz=timezone.utc,
                        ).isoformat(),
                    }
                )
        except OSError as exc:
            summary["error"] = _safe_exception_summary(exc)
        summaries.append(summary)
    return summaries


def _row_text(row: sqlite3.Row, column: str) -> str:
    """從 SQLite row 取出空值安全字串。"""

    return str(row[column] or "")


def _row_int(row: sqlite3.Row, column: str) -> int:
    """從 SQLite row 取出空值安全整數。"""

    return int(row[column] or 0)


def _row_freeform_summary(
    row: sqlite3.Row,
    column: str,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """從 SQLite row 取出 freeform 欄位並套用 support bundle 摘要。"""

    return _freeform_summary(_row_text(row, column), aliases=aliases)


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


def _notification_outbox_counts(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """依 target/channel/event kind/status 彙總 outbox。"""

    rows = connection.execute(
        """
        SELECT
            target_id, channel, event_kind, status,
            COUNT(*) AS count,
            MIN(updated_at) AS oldest_updated_at,
            MAX(attempts) AS max_attempts
        FROM notification_outbox
        GROUP BY target_id, channel, event_kind, status
        ORDER BY target_id, channel, event_kind, status
        """
    ).fetchall()
    return [
        {
            "target": aliases.alias("target", row["target_id"]),
            "channel": str(row["channel"] or ""),
            "event_kind": str(row["event_kind"] or ""),
            "status": str(row["status"] or ""),
            "count": int(row["count"] or 0),
            "oldest_updated_at": str(row["oldest_updated_at"] or ""),
            "max_attempts": int(row["max_attempts"] or 0),
        }
        for row in rows
    ]


def _failed_outbox_samples(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """回傳最近 failed outbox sample，不輸出 endpoint/title/message/permalink。"""

    rows = connection.execute(
        """
        SELECT id, target_id, item_key, channel, event_kind, status, attempts,
               failure_reason, failure_count, last_error, source_scan_run_id,
               created_at, updated_at
        FROM notification_outbox
        WHERE status IN ('failed', 'processing_failed')
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (RECENT_NOTIFICATION_SAMPLE_LIMIT,),
    ).fetchall()
    return [
        {
            "outbox": aliases.alias("outbox", row["id"]),
            "target": aliases.alias("target", row["target_id"]),
            "item": aliases.alias("item", row["item_key"]),
            "channel": str(row["channel"] or ""),
            "event_kind": str(row["event_kind"] or ""),
            "status": str(row["status"] or ""),
            "attempts": int(row["attempts"] or 0),
            "failure_reason": _freeform_summary(
                str(row["failure_reason"] or ""),
                aliases=aliases,
            ),
            "failure_count": int(row["failure_count"] or 0),
            "last_error": _freeform_summary(
                str(row["last_error"] or ""),
                aliases=aliases,
            ),
            "source_scan_run": aliases.alias("scan_run", row["source_scan_run_id"]),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }
        for row in rows
    ]


def _notification_event_counts(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """依 target/channel/event kind/status 彙總 notification events。"""

    rows = connection.execute(
        """
        SELECT target_id, channel, event_kind, status,
               COUNT(*) AS count, MAX(created_at) AS latest_created_at
        FROM notification_events
        GROUP BY target_id, channel, event_kind, status
        ORDER BY target_id, channel, event_kind, status
        """
    ).fetchall()
    return [
        {
            "target": aliases.alias("target", row["target_id"]),
            "channel": str(row["channel"] or ""),
            "event_kind": str(row["event_kind"] or ""),
            "status": str(row["status"] or ""),
            "count": int(row["count"] or 0),
            "latest_created_at": str(row["latest_created_at"] or ""),
        }
        for row in rows
    ]


def _recent_notification_events(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """回傳最近通知事件摘要，不輸出 message 原文。"""

    rows = connection.execute(
        """
        SELECT id, target_id, item_key, channel, event_kind, status,
               source_scan_run_id, failure_reason, failure_count, created_at
        FROM notification_events
        ORDER BY id DESC
        LIMIT ?
        """,
        (RECENT_NOTIFICATION_SAMPLE_LIMIT,),
    ).fetchall()
    return [
        {
            "event": aliases.alias("notification_event", row["id"]),
            "target": aliases.alias("target", row["target_id"]),
            "item": aliases.alias("item", row["item_key"]),
            "channel": str(row["channel"] or ""),
            "event_kind": str(row["event_kind"] or ""),
            "status": str(row["status"] or ""),
            "source_scan_run": aliases.alias("scan_run", row["source_scan_run_id"]),
            "failure_reason": _freeform_summary(
                str(row["failure_reason"] or ""),
                aliases=aliases,
            ),
            "failure_count": int(row["failure_count"] or 0),
            "created_at": str(row["created_at"] or ""),
        }
        for row in rows
    ]


def _target_dedupe_state_summary(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """回傳 target dedupe epoch 摘要。"""

    if "target_dedupe_state" not in _table_names(connection):
        return []
    rows = connection.execute(
        "SELECT target_id, dedupe_epoch, updated_at FROM target_dedupe_state ORDER BY target_id"
    ).fetchall()
    return [
        {
            "target": aliases.alias("target", row["target_id"]),
            "dedupe_epoch": int(row["dedupe_epoch"] or 0),
            "updated_at": str(row["updated_at"] or ""),
        }
        for row in rows
    ]


def _logical_item_counts(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """回傳 logical item / alias count 摘要。"""

    table_names = _table_names(connection)
    if "logical_items" not in table_names:
        return []
    rows = connection.execute(
        """
        SELECT target_id, item_kind, COUNT(*) AS count,
               MIN(first_seen_at) AS oldest_first_seen_at,
               MAX(last_seen_at) AS newest_last_seen_at
        FROM logical_items
        GROUP BY target_id, item_kind
        ORDER BY target_id, item_kind
        """
    ).fetchall()
    alias_counts: dict[str, int] = {}
    if "logical_item_aliases" in table_names:
        for row in connection.execute(
            """
            SELECT target_id, COUNT(*) AS count
            FROM logical_item_aliases
            GROUP BY target_id
            """
        ).fetchall():
            alias_counts[str(row["target_id"])] = int(row["count"] or 0)
    return [
        {
            "target": aliases.alias("target", row["target_id"]),
            "item_kind": str(row["item_kind"] or ""),
            "count": int(row["count"] or 0),
            "alias_count_for_target": alias_counts.get(str(row["target_id"]), 0),
            "oldest_first_seen_at": str(row["oldest_first_seen_at"] or ""),
            "newest_last_seen_at": str(row["newest_last_seen_at"] or ""),
        }
        for row in rows
    ]


def _notification_dedupe_counts(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """回傳 notification dedupe ledger count 摘要。"""

    if "notification_dedupe" not in _table_names(connection):
        return []
    rows = connection.execute(
        """
        SELECT target_id, event_kind, channel, item_kind, status,
               COUNT(*) AS count, MAX(last_deduped_at) AS latest_deduped_at
        FROM notification_dedupe
        GROUP BY target_id, event_kind, channel, item_kind, status
        ORDER BY target_id, event_kind, channel, item_kind, status
        """
    ).fetchall()
    return [
        {
            "target": aliases.alias("target", row["target_id"]),
            "event_kind": str(row["event_kind"] or ""),
            "channel": str(row["channel"] or ""),
            "item_kind": str(row["item_kind"] or ""),
            "status": str(row["status"] or ""),
            "count": int(row["count"] or 0),
            "latest_deduped_at": str(row["latest_deduped_at"] or ""),
        }
        for row in rows
    ]


def _seen_item_counts(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """回傳 legacy seen item count 摘要。"""

    if "seen_items" not in _table_names(connection):
        return []
    rows = connection.execute(
        """
        SELECT scope_id, item_kind, COUNT(*) AS count,
               MIN(first_seen_at) AS oldest_first_seen_at,
               MAX(last_seen_at) AS newest_last_seen_at
        FROM seen_items
        GROUP BY scope_id, item_kind
        ORDER BY scope_id, item_kind
        """
    ).fetchall()
    return [
        {
            "scope": aliases.alias("scope", row["scope_id"]),
            "item_kind": str(row["item_kind"] or ""),
            "count": int(row["count"] or 0),
            "oldest_first_seen_at": str(row["oldest_first_seen_at"] or ""),
            "newest_last_seen_at": str(row["newest_last_seen_at"] or ""),
        }
        for row in rows
    ]


def _include_keyword_group_summary(value: str) -> dict[str, int]:
    """計算 include keyword groups 數量，不輸出 keyword。"""

    groups = _json_list(value)
    group_count = 0
    nonempty_group_count = 0
    keyword_count = 0
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_count += 1
        keywords = group.get("keywords", [])
        if isinstance(keywords, list):
            normalized_keywords = [item for item in keywords if str(item).strip()]
            keyword_count += len(normalized_keywords)
            if normalized_keywords:
                nonempty_group_count += 1
    return {
        "group_count": group_count,
        "nonempty_group_count": nonempty_group_count,
        "keyword_count": keyword_count,
    }


def _empty_outbox_summary() -> dict[str, object]:
    """回傳空 DB 時的 notification outbox 摘要。"""

    return {
        "pending": 0,
        "processing": 0,
        "failed": 0,
        "terminal": 0,
        "oldest_pending_updated_at": "",
        "max_attempts": 0,
    }


_SUPPORT_COUNT_TABLES = (
    "schema_metadata",
    "targets",
    "target_configs",
    "seen_items",
    "target_dedupe_state",
    "logical_items",
    "logical_item_aliases",
    "scan_scope_state",
    "scan_runs",
    "latest_scan_items",
    "latest_scan_item_matches",
    "match_history",
    "match_history_matches",
    "notification_events",
    "notification_dedupe",
    "notification_outbox",
    "target_runtime_state",
    "target_cover_image_refresh_state",
    "global_notification_settings",
    "app_settings",
    "sidebar_groups",
    "sidebar_target_placements",
    "sidebar_group_config_templates",
    "dashboard_revision",
)
