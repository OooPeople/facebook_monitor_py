"""SQLite repository implementation。"""

from __future__ import annotations

import sqlite3

from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.persistence.row_mappers import notification_event_from_row
from facebook_monitor.persistence.repositories.sqlite_ids import require_lastrowid
from facebook_monitor.persistence.sqlite_codec import encode_datetime

NOTIFICATION_EVENTS_PER_TARGET_LIMIT = 500


class NotificationEventRepository:
    """保存通知事件。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, event: NotificationEvent) -> int:
        """新增通知事件並回傳 row id。"""

        cursor = self.connection.execute(
            """
            INSERT INTO notification_events (
                target_id, item_key, channel, status, message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.target_id,
                event.item_key,
                event.channel.value,
                event.status.value,
                event.message,
                encode_datetime(event.created_at),
            ),
        )
        event_id = require_lastrowid(cursor.lastrowid, table_name="notification_events")
        self.prune_by_target(event.target_id)
        return event_id

    def prune_by_target(
        self,
        target_id: str,
        limit: int = NOTIFICATION_EVENTS_PER_TARGET_LIMIT,
    ) -> int:
        """保留單一 target 最近 N 筆 notification events，避免長跑無限制成長。"""

        bounded_limit = max(int(limit), 1)
        cursor = self.connection.execute(
            """
            DELETE FROM notification_events
            WHERE target_id = ?
              AND id NOT IN (
                  SELECT id
                  FROM notification_events
                  WHERE target_id = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (target_id, target_id, bounded_limit),
        )
        return int(cursor.rowcount or 0)

    def list_by_target(self, target_id: str, limit: int = 50) -> list[NotificationEvent]:
        """依 target id 查詢最近 notification events。"""

        rows = self.connection.execute(
            """
            SELECT * FROM notification_events
            WHERE target_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (target_id, limit),
        ).fetchall()
        return [notification_event_from_row(row) for row in rows]

    def latest_by_target(self, target_id: str) -> NotificationEvent | None:
        """查詢單一 target 最近一筆通知事件。"""

        row = self.connection.execute(
            """
            SELECT * FROM notification_events
            WHERE target_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (target_id,),
        ).fetchone()
        return notification_event_from_row(row) if row else None

    def latest_by_targets(self, target_ids: list[str]) -> dict[str, NotificationEvent]:
        """一次查詢多個 target 的最近通知事件。"""

        unique_target_ids = list(dict.fromkeys(target_id for target_id in target_ids if target_id))
        if not unique_target_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_target_ids)
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM (
                SELECT notification_events.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY target_id
                           ORDER BY id DESC
                       ) AS row_number
                FROM notification_events
                WHERE target_id IN ({placeholders})
            )
            WHERE row_number = 1
            """,
            tuple(unique_target_ids),
        ).fetchall()
        events: dict[str, NotificationEvent] = {}
        for row in rows:
            event = notification_event_from_row(row)
            events[event.target_id] = event
        return events

    def latest_by_targets_and_channels(
        self,
        target_ids: list[str],
    ) -> dict[str, dict[NotificationChannel, NotificationEvent]]:
        """一次查詢多個 target 各通知通道的最近事件。"""

        unique_target_ids = list(dict.fromkeys(target_id for target_id in target_ids if target_id))
        if not unique_target_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_target_ids)
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM (
                SELECT notification_events.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY target_id, channel
                           ORDER BY id DESC
                       ) AS row_number
                FROM notification_events
                WHERE target_id IN ({placeholders})
            )
            WHERE row_number = 1
            """,
            tuple(unique_target_ids),
        ).fetchall()
        events: dict[str, dict[NotificationChannel, NotificationEvent]] = {}
        for row in rows:
            event = notification_event_from_row(row)
            events.setdefault(event.target_id, {})[event.channel] = event
        return events

    def latest_by_target_channels(
        self,
        target_id: str,
    ) -> dict[NotificationChannel, NotificationEvent]:
        """查詢單一 target 各通知通道的最近事件。"""

        return self.latest_by_targets_and_channels([target_id]).get(target_id, {})

    def latest_sent_by_target_item_keys(
        self,
        target_id: str,
        item_keys: list[str],
    ) -> dict[str, NotificationEvent]:
        """依 target 與 item keys 查詢每個 item 最近成功通知事件。"""

        unique_keys = list(dict.fromkeys(key for key in item_keys if key))
        if not unique_keys:
            return {}
        placeholders = ",".join("?" for _ in unique_keys)
        rows = self.connection.execute(
            f"""
            SELECT * FROM notification_events
            WHERE target_id = ?
              AND status = ?
              AND item_key IN ({placeholders})
            ORDER BY id DESC
            """,
            (target_id, NotificationStatus.SENT.value, *unique_keys),
        ).fetchall()
        events: dict[str, NotificationEvent] = {}
        for row in rows:
            event = notification_event_from_row(row)
            events.setdefault(event.item_key, event)
        return events

