"""SQLite repository for notification dedupe ledger."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationDedupeStatus
from facebook_monitor.core.models import NotificationEventKind
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.repositories.dedupe_state import DedupeStateRepository
from facebook_monitor.persistence.sqlite_codec import encode_datetime


@dataclass(frozen=True)
class NotificationDedupeReservation:
    """保存一次 notification dedupe reservation 結果。"""

    dedupe_id: int
    created: bool


class NotificationDedupeRepository:
    """保存通知防重複 tombstone / ledger。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.dedupe_state = DedupeStateRepository(connection)

    def reserve_match(
        self,
        *,
        target_id: str,
        logical_item_id: int,
        item_key: str,
        item_kind: ItemKind,
        channel: NotificationChannel,
    ) -> NotificationDedupeReservation:
        """為 match notification 建立 dedupe reservation。"""

        if logical_item_id <= 0:
            raise ValueError("logical_item_id is required for match notification dedupe")
        return self._reserve(
            target_id=target_id,
            event_kind=NotificationEventKind.MATCH,
            channel=channel,
            subject_key=f"logical:{logical_item_id}",
            logical_item_id=logical_item_id,
            item_key=item_key,
            item_kind=item_kind,
        )

    def reserve_runtime_failure(
        self,
        *,
        target_id: str,
        scan_run_id: int,
        item_key: str,
        item_kind: ItemKind,
        channel: NotificationChannel,
        failure_reason: str,
        failure_count: int,
    ) -> NotificationDedupeReservation:
        """為 runtime failure notification 建立 dedupe reservation。"""

        if scan_run_id <= 0:
            raise ValueError("scan_run_id is required for runtime failure dedupe")
        return self._reserve(
            target_id=target_id,
            event_kind=NotificationEventKind.RUNTIME_FAILURE,
            channel=channel,
            subject_key=f"runtime-failure:{scan_run_id}",
            logical_item_id=None,
            item_key=item_key,
            item_kind=item_kind,
            failure_reason=failure_reason,
            failure_count=max(int(failure_count), 1),
        )

    def _reserve(
        self,
        *,
        target_id: str,
        event_kind: NotificationEventKind,
        channel: NotificationChannel,
        subject_key: str,
        logical_item_id: int | None,
        item_key: str,
        item_kind: ItemKind,
        failure_reason: str = "",
        failure_count: int = 0,
    ) -> NotificationDedupeReservation:
        """建立或讀取 dedupe reservation。"""

        epoch = self.dedupe_state.current_epoch(target_id)
        now_text = encode_datetime(utc_now())
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO notification_dedupe (
                target_id, dedupe_epoch, event_kind, channel, subject_key,
                logical_item_id, item_key, item_kind, status,
                failure_reason, failure_count, first_queued_at, last_deduped_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_id,
                epoch,
                event_kind.value,
                channel.value,
                subject_key,
                logical_item_id,
                item_key,
                item_kind.value,
                NotificationDedupeStatus.QUEUED.value,
                failure_reason,
                max(int(failure_count), 0),
                now_text,
                now_text,
                now_text,
                now_text,
            ),
        )
        created = cursor.rowcount == 1
        if not created:
            if event_kind == NotificationEventKind.RUNTIME_FAILURE:
                self.connection.execute(
                    """
                    UPDATE notification_dedupe
                    SET last_deduped_at = ?,
                        failure_reason = ?,
                        failure_count = ?,
                        updated_at = ?
                    WHERE target_id = ?
                      AND dedupe_epoch = ?
                      AND event_kind = ?
                      AND channel = ?
                      AND subject_key = ?
                    """,
                    (
                        now_text,
                        failure_reason,
                        max(int(failure_count), 0),
                        now_text,
                        target_id,
                        epoch,
                        event_kind.value,
                        channel.value,
                        subject_key,
                    ),
                )
            else:
                self.connection.execute(
                    """
                    UPDATE notification_dedupe
                    SET last_deduped_at = ?,
                        updated_at = ?
                    WHERE target_id = ?
                      AND dedupe_epoch = ?
                      AND event_kind = ?
                      AND channel = ?
                      AND subject_key = ?
                    """,
                    (
                        now_text,
                        now_text,
                        target_id,
                        epoch,
                        event_kind.value,
                        channel.value,
                        subject_key,
                    ),
                )
        row = self.connection.execute(
            """
            SELECT id
            FROM notification_dedupe
            WHERE target_id = ?
              AND dedupe_epoch = ?
              AND event_kind = ?
              AND channel = ?
              AND subject_key = ?
            """,
            (target_id, epoch, event_kind.value, channel.value, subject_key),
        ).fetchone()
        if row is None:
            raise RuntimeError("notification dedupe reservation failed")
        return NotificationDedupeReservation(dedupe_id=int(row["id"]), created=created)
