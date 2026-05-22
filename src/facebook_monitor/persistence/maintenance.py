"""SQLite runtime data maintenance helpers。

職責：集中清理可重建的 runtime / debug 資料，避免 Web UI 長期執行後 DB 無限制增長。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeDataCleanupResult:
    """保存一次 runtime data 清理的刪除筆數。"""

    scan_runs: int = 0
    latest_scan_items: int = 0
    match_history: int = 0
    notification_events: int = 0
    notification_outbox: int = 0
    seen_items: int = 0
    scan_scope_state: int = 0

    @property
    def total_deleted(self) -> int:
        """回傳本次總刪除筆數。"""

        return (
            self.scan_runs
            + self.latest_scan_items
            + self.match_history
            + self.notification_events
            + self.notification_outbox
            + self.seen_items
        )


@dataclass(frozen=True)
class TargetDataCleanupResult:
    """保存單一 target 資料清除結果。"""

    target_found: bool
    seen_items: int = 0
    scan_scope_state: int = 0
    match_history: int = 0
    notification_events: int = 0
    notification_outbox: int = 0

    @property
    def total_deleted(self) -> int:
        """回傳實際刪除的資料列總數。"""

        return (
            self.seen_items
            + self.match_history
            + self.notification_events
            + self.notification_outbox
        )


class RuntimeDataMaintenanceRepository:
    """清理可重建 runtime data，保留 target/config/profile 等長期設定。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def clear_runtime_data(self, *, include_seen_items: bool = True) -> RuntimeDataCleanupResult:
        """清除可重建資料，保留 target、config、profile、outbox 與持久查看紀錄。"""

        latest_scan_items = self._delete_all("latest_scan_items")
        notification_events = self._delete_all("notification_events")
        scan_runs = self._delete_all("scan_runs")
        seen_items = self._delete_all("seen_items") if include_seen_items else 0
        scan_scope_state = self._reset_scan_scope_state() if include_seen_items else 0
        return RuntimeDataCleanupResult(
            scan_runs=scan_runs,
            latest_scan_items=latest_scan_items,
            match_history=0,
            notification_events=notification_events,
            notification_outbox=0,
            seen_items=seen_items,
            scan_scope_state=scan_scope_state,
        )

    def clear_target_seen_baseline(self, target_id: str) -> TargetDataCleanupResult:
        """清除單一 target 的 seen baseline，下一輪會重新進入 baseline 抑制。"""

        scope_id = self._target_scope_id(target_id)
        if scope_id is None:
            return TargetDataCleanupResult(target_found=False)
        seen_items = self._delete_where("seen_items", "scope_id", scope_id)
        scan_scope_state = self._reset_scope_state(scope_id)
        return TargetDataCleanupResult(
            target_found=True,
            seen_items=seen_items,
            scan_scope_state=scan_scope_state,
        )

    def clear_target_match_history(self, target_id: str) -> TargetDataCleanupResult:
        """清除單一 target 的命中紀錄，保留 seen baseline 與設定。"""

        if not self._target_exists(target_id):
            return TargetDataCleanupResult(target_found=False)
        self.connection.execute(
            """
            DELETE FROM match_history_matches
            WHERE history_id IN (
                SELECT id FROM match_history WHERE target_id = ?
            )
            """,
            (target_id,),
        )
        match_history = self._delete_where("match_history", "target_id", target_id)
        return TargetDataCleanupResult(
            target_found=True,
            match_history=match_history,
        )

    def clear_target_notification_data(self, target_id: str) -> TargetDataCleanupResult:
        """清除單一 target 的通知事件與 outbox rows。"""

        if not self._target_exists(target_id):
            return TargetDataCleanupResult(target_found=False)
        notification_events = self._delete_where(
            "notification_events",
            "target_id",
            target_id,
        )
        notification_outbox = self._delete_where(
            "notification_outbox",
            "target_id",
            target_id,
        )
        return TargetDataCleanupResult(
            target_found=True,
            notification_events=notification_events,
            notification_outbox=notification_outbox,
        )

    def _delete_all(self, table_name: str) -> int:
        """刪除指定 runtime table 的全部資料並回傳刪除筆數。"""

        cursor = self.connection.execute(f"DELETE FROM {table_name}")
        return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def _delete_where(self, table_name: str, column_name: str, value: str) -> int:
        """刪除單一欄位符合條件的資料列。"""

        cursor = self.connection.execute(
            f"DELETE FROM {table_name} WHERE {column_name} = ?",
            (value,),
        )
        return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def _reset_scan_scope_state(self) -> int:
        """清 seen 時同步重置所有 target scope，避免舊 DB 缺 row 時被視為已初始化。"""

        cursor = self.connection.execute(
            """
            INSERT INTO scan_scope_state (scope_id, initialized, updated_at)
            SELECT DISTINCT scope_id, 0, datetime('now')
            FROM targets
            WHERE TRIM(scope_id) <> ''
            ON CONFLICT(scope_id) DO UPDATE SET
                initialized = 0,
                updated_at = excluded.updated_at
            """
        )
        return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def _reset_scope_state(self, scope_id: str) -> int:
        """重置單一 scope state，供 target-scoped 資料清除使用。"""

        cursor = self.connection.execute(
            """
            INSERT INTO scan_scope_state (scope_id, initialized, updated_at)
            VALUES (?, 0, datetime('now'))
            ON CONFLICT(scope_id) DO UPDATE SET
                initialized = 0,
                updated_at = excluded.updated_at
            """,
            (scope_id,),
        )
        return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def _target_scope_id(self, target_id: str) -> str | None:
        """讀取 target scope id；target 不存在時回 None。"""

        row = self.connection.execute(
            "SELECT scope_id FROM targets WHERE id = ?",
            (target_id,),
        ).fetchone()
        if row is None:
            return None
        return str(row["scope_id"])

    def _target_exists(self, target_id: str) -> bool:
        """確認 target 是否存在。"""

        row = self.connection.execute(
            "SELECT 1 FROM targets WHERE id = ?",
            (target_id,),
        ).fetchone()
        return row is not None
