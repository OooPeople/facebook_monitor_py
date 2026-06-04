"""SQLite runtime data maintenance helpers。

職責：集中清理可重建的 runtime / debug 資料，避免 Web UI 長期執行後 DB 無限制增長。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta

from facebook_monitor.core.defaults import PYTHON_PERSISTENCE_RETENTION_DEFAULTS
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.sqlite_codec import encode_datetime


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
            + self.scan_scope_state
        )


@dataclass(frozen=True)
class BoundedRetentionPruneResult:
    """保存一次 bounded retention 清理的刪除筆數。"""

    terminal_outbox: int = 0
    notification_dedupe: int = 0
    logical_items: int = 0
    legacy_seen_items: int = 0

    @property
    def total_deleted(self) -> int:
        """回傳本次總刪除筆數。"""

        return (
            self.terminal_outbox
            + self.notification_dedupe
            + self.logical_items
            + self.legacy_seen_items
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

    def prune_bounded_retention(
        self,
        *,
        now: datetime | None = None,
        logical_dedupe_horizon_days: int = (
            PYTHON_PERSISTENCE_RETENTION_DEFAULTS.logical_dedupe_horizon_days
        ),
        terminal_outbox_retention_days: int = (
            PYTHON_PERSISTENCE_RETENTION_DEFAULTS.terminal_outbox_retention_days
        ),
        failed_outbox_retention_days: int = (
            PYTHON_PERSISTENCE_RETENTION_DEFAULTS.failed_outbox_retention_days
        ),
    ) -> BoundedRetentionPruneResult:
        """清理 bounded retention horizon 外的內部去重與 terminal outbox 資料。"""

        reference_time = now or utc_now()
        logical_cutoff = encode_datetime(
            reference_time - timedelta(days=max(logical_dedupe_horizon_days, 0))
        )
        outbox_cutoff = encode_datetime(
            reference_time - timedelta(days=max(terminal_outbox_retention_days, 0))
        )
        failed_outbox_cutoff = encode_datetime(
            reference_time - timedelta(days=max(failed_outbox_retention_days, 0))
        )
        terminal_outbox = self._delete_terminal_outbox_before(
            outbox_cutoff,
            failed_outbox_cutoff=failed_outbox_cutoff,
        )
        notification_dedupe = self._delete_notification_dedupe_before(logical_cutoff)
        logical_items = self._delete_logical_items_before(logical_cutoff)
        legacy_seen_items = self._delete_legacy_seen_items_before(logical_cutoff)
        return BoundedRetentionPruneResult(
            terminal_outbox=terminal_outbox,
            notification_dedupe=notification_dedupe,
            logical_items=logical_items,
            legacy_seen_items=legacy_seen_items,
        )

    def _delete_terminal_outbox_before(
        self,
        cutoff: str,
        *,
        failed_outbox_cutoff: str,
    ) -> int:
        """短留 terminal outbox，保留 pending/processing 活資料。"""

        cursor = self.connection.execute(
            """
            DELETE FROM notification_outbox
            WHERE (
                    status IN ('sent', 'skipped')
                    AND updated_at < ?
                )
               OR (
                    status IN ('failed', 'processing_failed')
                    AND updated_at < ?
                )
            """,
            (cutoff, failed_outbox_cutoff),
        )
        return int(cursor.rowcount or 0)

    def _delete_notification_dedupe_before(self, cutoff: str) -> int:
        """刪除 60 天 horizon 外且沒有 active outbox 引用的 dedupe rows。"""

        cursor = self.connection.execute(
            """
            DELETE FROM notification_dedupe
            WHERE last_deduped_at < ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM notification_outbox
                  WHERE notification_outbox.dedupe_id = notification_dedupe.id
                    AND notification_outbox.status IN (
                        'pending',
                        'processing_pending',
                        'failed',
                        'processing_failed'
                    )
              )
            """,
            (cutoff,),
        )
        return int(cursor.rowcount or 0)

    def _delete_logical_items_before(self, cutoff: str) -> int:
        """刪除 horizon 外且無 dedupe/latest/active outbox 依賴的 logical items。"""

        cursor = self.connection.execute(
            """
            DELETE FROM logical_items
            WHERE last_seen_at < ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM notification_dedupe
                  WHERE notification_dedupe.logical_item_id = logical_items.id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM latest_scan_items
                  JOIN logical_item_aliases
                    ON logical_item_aliases.target_id = latest_scan_items.target_id
                   AND logical_item_aliases.alias_key = latest_scan_items.item_key
                  WHERE logical_item_aliases.logical_item_id = logical_items.id
              )
            """,
            (cutoff,),
        )
        return int(cursor.rowcount or 0)

    def _delete_legacy_seen_items_before(self, cutoff: str) -> int:
        """清理舊 `seen_items` mirror，避免相容寫入長期累積。"""

        cursor = self.connection.execute(
            """
            DELETE FROM seen_items
            WHERE last_seen_at < ?
            """,
            (cutoff,),
        )
        return int(cursor.rowcount or 0)
