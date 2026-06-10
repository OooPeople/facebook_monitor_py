"""SQLite repository for target-scoped dedupe epochs."""

from __future__ import annotations

import sqlite3

from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.sqlite_codec import encode_datetime


class DedupeStateRepository:
    """保存 target-scoped dedupe epoch，供 reset notification state 使用。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def current_epoch(self, target_id: str) -> int:
        """取得 target 目前 dedupe epoch；缺 row 時補 epoch 0。"""

        normalized_target_id = target_id.strip()
        if not normalized_target_id:
            return 0
        row = self.connection.execute(
            """
            SELECT dedupe_epoch
            FROM target_dedupe_state
            WHERE target_id = ?
            """,
            (normalized_target_id,),
        ).fetchone()
        if row is not None:
            return int(row["dedupe_epoch"])
        self.connection.execute(
            """
            INSERT OR IGNORE INTO target_dedupe_state (
                target_id, dedupe_epoch, updated_at
            )
            VALUES (?, 0, ?)
            """,
            (normalized_target_id, encode_datetime(utc_now())),
        )
        return 0

    def peek_current_epoch(self, target_id: str) -> int:
        """唯讀取得 target 目前 dedupe epoch；缺 row 時視為 epoch 0。"""

        normalized_target_id = target_id.strip()
        if not normalized_target_id:
            return 0
        row = self.connection.execute(
            """
            SELECT dedupe_epoch
            FROM target_dedupe_state
            WHERE target_id = ?
            """,
            (normalized_target_id,),
        ).fetchone()
        return int(row["dedupe_epoch"]) if row is not None else 0

    def advance_epoch(self, target_id: str) -> int:
        """遞增 target dedupe epoch，讓舊 logical/dedupe rows 立即失效。"""

        normalized_target_id = target_id.strip()
        if not normalized_target_id:
            return 0
        self.connection.execute(
            """
            INSERT INTO target_dedupe_state (
                target_id, dedupe_epoch, updated_at
            )
            VALUES (?, 1, ?)
            ON CONFLICT(target_id) DO UPDATE SET
                dedupe_epoch = target_dedupe_state.dedupe_epoch + 1,
                updated_at = excluded.updated_at
            """,
            (normalized_target_id, encode_datetime(utc_now())),
        )
        row = self.connection.execute(
            """
            SELECT dedupe_epoch
            FROM target_dedupe_state
            WHERE target_id = ?
            """,
            (normalized_target_id,),
        ).fetchone()
        return int(row["dedupe_epoch"]) if row is not None else 0
