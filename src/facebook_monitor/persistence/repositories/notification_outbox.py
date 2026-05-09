"""SQLite repository implementation。"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.row_mappers import notification_outbox_from_row
from facebook_monitor.persistence.sqlite_codec import encode_datetime


class NotificationOutboxRepository:
    """保存 commit 後才發送的通知 outbox event。

    claim/recovery methods 會自行提交短交易，僅供 commit 後 outbox dispatcher
    使用；scan transaction 內不得直接呼叫 dispatch，只能 enqueue 並註冊
    after-commit hook。
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def enqueue(self, entry: NotificationOutboxEntry) -> NotificationOutboxEntry:
        """新增待送通知；idempotency key 已存在時回傳既有 row。"""

        self.connection.execute(
            """
            INSERT OR IGNORE INTO notification_outbox (
                idempotency_key, target_id, item_key, item_kind, channel, status,
                title, message, endpoint, permalink, attempts, last_error,
                notification_event_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.idempotency_key,
                entry.target_id,
                entry.item_key,
                entry.item_kind.value,
                entry.channel.value,
                entry.status.value,
                entry.title,
                entry.message,
                entry.endpoint,
                entry.permalink,
                entry.attempts,
                entry.last_error,
                entry.notification_event_id,
                encode_datetime(entry.created_at),
                encode_datetime(entry.updated_at),
            ),
        )
        loaded = self.get_by_idempotency_key(entry.idempotency_key)
        if loaded is None:
            raise RuntimeError("notification outbox enqueue failed")
        return loaded

    def get_by_idempotency_key(self, idempotency_key: str) -> NotificationOutboxEntry | None:
        """依 idempotency key 查詢 outbox event。"""

        row = self.connection.execute(
            "SELECT * FROM notification_outbox WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return notification_outbox_from_row(row) if row else None

    def list_pending(self, limit: int = 50) -> list[NotificationOutboxEntry]:
        """列出尚未 claim 的 pending events，僅供檢視與測試。"""

        rows = self.connection.execute(
            """
            SELECT * FROM notification_outbox
            WHERE status = ?
            ORDER BY id
            LIMIT ?
            """,
            (
                NotificationOutboxStatus.PENDING.value,
                limit,
            ),
        ).fetchall()
        return [notification_outbox_from_row(row) for row in rows]

    def claim_pending(self, limit: int = 50) -> list[NotificationOutboxEntry]:
        """原子 claim pending rows，外部 I/O 只能處理 claim 成功的 events。"""

        return self._claim_status(
            NotificationOutboxStatus.PENDING,
            processing_status=NotificationOutboxStatus.PROCESSING_PENDING,
            limit=limit,
        )

    def claim_failed(self, limit: int = 50) -> list[NotificationOutboxEntry]:
        """原子 claim failed rows，供明確 retry API 使用。"""

        return self._claim_status(
            NotificationOutboxStatus.FAILED,
            processing_status=NotificationOutboxStatus.PROCESSING_FAILED,
            limit=limit,
        )

    def list_failed(self, limit: int = 50) -> list[NotificationOutboxEntry]:
        """列出 failed outbox events，供明確 retry command 使用。"""

        rows = self.connection.execute(
            """
            SELECT * FROM notification_outbox
            WHERE status = ?
            ORDER BY updated_at, id
            LIMIT ?
            """,
            (NotificationOutboxStatus.FAILED.value, limit),
        ).fetchall()
        return [notification_outbox_from_row(row) for row in rows]

    def recover_stale_processing(self, *, older_than_seconds: float) -> int:
        """將過期 processing rows 放回來源狀態，避免 dispatcher 崩潰後永久卡住。"""

        threshold = utc_now() - timedelta(seconds=max(older_than_seconds, 0))
        recovered_count = 0
        recovered_at = encode_datetime(utc_now())
        threshold_text = encode_datetime(threshold)
        for processing_status, restored_status in (
            (
                NotificationOutboxStatus.PROCESSING_PENDING,
                NotificationOutboxStatus.PENDING,
            ),
            (
                NotificationOutboxStatus.PROCESSING_FAILED,
                NotificationOutboxStatus.FAILED,
            ),
        ):
            cursor = self.connection.execute(
                """
                UPDATE notification_outbox
                SET status = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE status = ?
                  AND updated_at < ?
                """,
                (
                    restored_status.value,
                    "processing_recovered",
                    recovered_at,
                    processing_status.value,
                    threshold_text,
                ),
            )
            recovered_count += int(cursor.rowcount or 0)
        if recovered_count:
            self.connection.commit()
        return recovered_count

    def mark_result(
        self,
        *,
        entry_id: int,
        status: NotificationOutboxStatus,
        attempts: int,
        message: str = "",
        notification_event_id: int | None = None,
    ) -> None:
        """寫回 outbox 發送結果。"""

        self.connection.execute(
            """
            UPDATE notification_outbox
            SET status = ?,
                attempts = ?,
                last_error = ?,
                notification_event_id = COALESCE(?, notification_event_id),
                updated_at = ?
            WHERE id = ?
            """,
            (
                status.value,
                attempts,
                message,
                notification_event_id,
                encode_datetime(utc_now()),
                entry_id,
            ),
        )

    def _claim_status(
        self,
        status: NotificationOutboxStatus,
        *,
        processing_status: NotificationOutboxStatus,
        limit: int,
    ) -> list[NotificationOutboxEntry]:
        """以逐筆 conditional update claim rows，確保跨 connection 不會重複取得。"""

        if limit <= 0:
            return []
        rows = self.connection.execute(
            """
            SELECT id FROM notification_outbox
            WHERE status = ?
            ORDER BY updated_at, id
            LIMIT ?
            """,
            (status.value, limit),
        ).fetchall()
        claimed_ids: list[int] = []
        claimed_at = encode_datetime(utc_now())
        for row in rows:
            entry_id = int(row["id"])
            cursor = self.connection.execute(
                """
                UPDATE notification_outbox
                SET status = ?,
                    updated_at = ?
                WHERE id = ?
                  AND status = ?
                """,
                (
                    processing_status.value,
                    claimed_at,
                    entry_id,
                    status.value,
                ),
            )
            if cursor.rowcount == 1:
                claimed_ids.append(entry_id)
        if not claimed_ids:
            return []
        self.connection.commit()
        placeholders = ",".join("?" for _ in claimed_ids)
        claimed_rows = self.connection.execute(
            f"""
            SELECT * FROM notification_outbox
            WHERE id IN ({placeholders})
            ORDER BY id
            """,
            tuple(claimed_ids),
        ).fetchall()
        return [notification_outbox_from_row(row) for row in claimed_rows]

