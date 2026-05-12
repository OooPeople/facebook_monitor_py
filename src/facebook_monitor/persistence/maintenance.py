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


class RuntimeDataMaintenanceRepository:
    """清理可重建 runtime data，保留 target/config/profile 等長期設定。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def clear_runtime_data(self, *, include_seen_items: bool = True) -> RuntimeDataCleanupResult:
        """清除可重建資料，保留 target、config、profile 與持久查看紀錄。"""

        latest_scan_items = self._delete_all("latest_scan_items")
        notification_events = self._delete_all("notification_events")
        scan_runs = self._delete_all("scan_runs")
        seen_items = self._delete_all("seen_items") if include_seen_items else 0
        return RuntimeDataCleanupResult(
            scan_runs=scan_runs,
            latest_scan_items=latest_scan_items,
            match_history=0,
            notification_events=notification_events,
            notification_outbox=0,
            seen_items=seen_items,
        )

    def _delete_all(self, table_name: str) -> int:
        """刪除指定 runtime table 的全部資料並回傳刪除筆數。"""

        cursor = self.connection.execute(f"DELETE FROM {table_name}")
        return int(cursor.rowcount if cursor.rowcount is not None else 0)
