"""SQLite 產品資料 invariant 檢查。

職責：提供 read-only schema contract 檢查，先把 enum、boolean、range 與
runtime 狀態不變式集中成可測工具。真正 CHECK constraint / table rebuild
需另走 migration，不在本模組直接修改資料。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetCoverImageRefreshResult
from facebook_monitor.core.models import TargetCoverImageRefreshStatus
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.refresh_policy import MIN_REFRESH_SECONDS


@dataclass(frozen=True)
class DatabaseInvariantViolation:
    """描述一筆資料 invariant 違反。"""

    table: str
    row_id: str
    field: str
    message: str

    def format(self) -> str:
        """回傳 CLI 可讀格式。"""

        return f"{self.table}[{self.row_id}].{self.field}: {self.message}"


def validate_database_invariants(
    connection: sqlite3.Connection,
) -> tuple[DatabaseInvariantViolation, ...]:
    """回傳目前 DB 內所有已知 invariant 違反。"""

    violations: list[DatabaseInvariantViolation] = []
    violations.extend(_enum_violations(connection))
    violations.extend(_boolean_violations(connection))
    violations.extend(_range_violations(connection))
    violations.extend(_runtime_state_violations(connection))
    return tuple(violations)


def _enum_violations(connection: sqlite3.Connection) -> list[DatabaseInvariantViolation]:
    checks: tuple[tuple[str, str, str, set[str]], ...] = (
        ("targets", "id", "target_kind", _enum_values(TargetKind)),
        ("targets", "id", "metadata_status", _enum_values(TargetMetadataStatus)),
        ("targets", "id", "worker_mode", _enum_values(WorkerMode)),
        ("seen_items", "scope_id || ':' || item_key", "item_kind", _enum_values(ItemKind)),
        ("match_history", "id", "item_kind", _enum_values(ItemKind)),
        ("latest_scan_items", "target_id || ':' || item_key", "item_kind", _enum_values(ItemKind)),
        ("scan_runs", "id", "status", _enum_values(ScanStatus)),
        ("scan_runs", "id", "worker_mode", _enum_values(WorkerMode)),
        ("notification_events", "id", "channel", _enum_values(NotificationChannel)),
        ("notification_events", "id", "status", _enum_values(NotificationStatus)),
        ("notification_outbox", "id", "item_kind", _enum_values(ItemKind)),
        ("notification_outbox", "id", "channel", _enum_values(NotificationChannel)),
        ("notification_outbox", "id", "status", _enum_values(NotificationOutboxStatus)),
        ("target_runtime_state", "target_id", "desired_state", _enum_values(TargetDesiredState)),
        ("target_runtime_state", "target_id", "runtime_status", _enum_values(TargetRuntimeStatus)),
        (
            "target_cover_image_refresh_state",
            "target_id",
            "status",
            _enum_values(TargetCoverImageRefreshStatus),
        ),
        (
            "target_cover_image_refresh_state",
            "target_id",
            "last_result",
            _enum_values(TargetCoverImageRefreshResult) | {""},
        ),
    )
    violations: list[DatabaseInvariantViolation] = []
    for table, id_expr, field, allowed in checks:
        allowed_placeholders = ",".join("?" for _ in allowed)
        rows = connection.execute(
            f"""
            SELECT {id_expr} AS row_id, {field}
            FROM {table}
            WHERE {field} NOT IN ({allowed_placeholders})
            """,
            tuple(sorted(allowed)),
        ).fetchall()
        violations.extend(
            DatabaseInvariantViolation(
                table=table,
                row_id=str(row["row_id"]),
                field=field,
                message=f"unexpected enum value {row[field]!r}",
            )
            for row in rows
        )
    return violations


def _boolean_violations(connection: sqlite3.Connection) -> list[DatabaseInvariantViolation]:
    checks: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("targets", "id", ("enabled", "paused")),
        (
            "target_configs",
            "target_id",
            (
                "jitter_enabled",
                "auto_load_more",
                "auto_adjust_sort",
                "enable_desktop_notification",
                "enable_ntfy",
                "enable_discord_notification",
            ),
        ),
        ("scan_scope_state", "scope_id", ("initialized",)),
        (
            "global_notification_settings",
            "id",
            ("enable_desktop_notification", "enable_ntfy", "enable_discord_notification"),
        ),
        ("sidebar_groups", "id", ("collapsed",)),
        (
            "sidebar_group_config_templates",
            "sidebar_group_id",
            (
                "jitter_enabled",
                "auto_load_more",
                "auto_adjust_sort",
                "enable_desktop_notification",
                "enable_ntfy",
                "enable_discord_notification",
            ),
        ),
        ("target_cover_image_refresh_state", "target_id", ("changed",)),
    )
    violations: list[DatabaseInvariantViolation] = []
    for table, id_column, fields in checks:
        for field in fields:
            rows = connection.execute(
                f"""
                SELECT {id_column} AS row_id, {field}
                FROM {table}
                WHERE {field} NOT IN (0, 1)
                """
            ).fetchall()
            violations.extend(
                DatabaseInvariantViolation(
                    table=table,
                    row_id=str(row["row_id"]),
                    field=field,
                    message=f"expected boolean 0/1, got {row[field]!r}",
                )
                for row in rows
            )
    return violations


def _range_violations(connection: sqlite3.Connection) -> list[DatabaseInvariantViolation]:
    checks: tuple[tuple[str, str, str, str], ...] = (
        (
            "target_configs",
            "target_id",
            "refresh_range",
            (
                "min_refresh_sec < ? OR max_refresh_sec < ? "
                "OR min_refresh_sec > max_refresh_sec"
            ),
        ),
        (
            "sidebar_group_config_templates",
            "sidebar_group_id",
            "refresh_range",
            (
                "min_refresh_sec < ? OR max_refresh_sec < ? "
                "OR min_refresh_sec > max_refresh_sec"
            ),
        ),
        ("target_configs", "target_id", "max_items_per_scan", "max_items_per_scan <= 0"),
        (
            "sidebar_group_config_templates",
            "sidebar_group_id",
            "max_items_per_scan",
            "max_items_per_scan <= 0",
        ),
        ("scan_runs", "id", "item_count", "item_count < 0 OR matched_count < 0"),
        ("notification_outbox", "id", "attempts", "attempts < 0"),
        (
            "target_runtime_state",
            "target_id",
            "scan_guard_count",
            "scan_guard_count < 0 OR consecutive_failure_count < 0",
        ),
    )
    violations: list[DatabaseInvariantViolation] = []
    for table, id_column, field, where_clause in checks:
        params: tuple[object, ...] = ()
        if field == "refresh_range":
            params = (MIN_REFRESH_SECONDS, MIN_REFRESH_SECONDS)
        rows = connection.execute(
            f"SELECT {id_column} AS row_id FROM {table} WHERE {where_clause}",
            params,
        ).fetchall()
        violations.extend(
            DatabaseInvariantViolation(
                table=table,
                row_id=str(row["row_id"]),
                field=field,
                message="value is outside product range",
            )
            for row in rows
        )
    return violations


def _runtime_state_violations(
    connection: sqlite3.Connection,
) -> list[DatabaseInvariantViolation]:
    violations: list[DatabaseInvariantViolation] = []
    running_rows = connection.execute(
        """
        SELECT target_id
        FROM target_runtime_state
        WHERE runtime_status = ?
          AND (active_worker_id = '' OR last_started_at = '' OR last_heartbeat_at = '')
        """,
        (TargetRuntimeStatus.RUNNING.value,),
    ).fetchall()
    violations.extend(
        DatabaseInvariantViolation(
            table="target_runtime_state",
            row_id=str(row["target_id"]),
            field="runtime_status",
            message="running state requires active worker, started_at and heartbeat",
        )
        for row in running_rows
    )
    idle_worker_rows = connection.execute(
        """
        SELECT target_id
        FROM target_runtime_state
        WHERE runtime_status != ?
          AND (active_worker_id != '' OR active_page_id != '')
        """,
        (TargetRuntimeStatus.RUNNING.value,),
    ).fetchall()
    violations.extend(
        DatabaseInvariantViolation(
            table="target_runtime_state",
            row_id=str(row["target_id"]),
            field="active_worker_id",
            message="non-running state must not keep active worker/page ownership",
        )
        for row in idle_worker_rows
    )
    return violations


def _enum_values(enum_type: type[StrEnum]) -> set[str]:
    """回傳 StrEnum values。"""

    return {item.value for item in enum_type}
