"""SQLite repository implementation。"""

from __future__ import annotations

import sqlite3

from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.sqlite_codec import encode_datetime


class ScanScopeStateRepository:
    """保存 target scope 是否啟用通知抑制 baseline。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def is_initialized(self, scope_id: str) -> bool:
        """回傳指定 scan scope 是否已離開 baseline 抑制狀態。"""

        normalized_scope_id = scope_id.strip()
        if not normalized_scope_id:
            return False
        row = self.connection.execute(
            "SELECT initialized FROM scan_scope_state WHERE scope_id = ?",
            (normalized_scope_id,),
        ).fetchone()
        if row is None:
            return True
        return bool(row["initialized"])

    def mark_initialized(self, scope_id: str) -> None:
        """標記指定 scan scope 已離開 baseline 抑制狀態。"""

        normalized_scope_id = scope_id.strip()
        if not normalized_scope_id:
            return
        now = encode_datetime(utc_now())
        self.connection.execute(
            """
            INSERT INTO scan_scope_state (scope_id, initialized, updated_at)
            VALUES (?, 1, ?)
            ON CONFLICT(scope_id) DO UPDATE SET
                initialized = 1,
                updated_at = excluded.updated_at
            """,
            (normalized_scope_id, now),
        )

    def clear_scope(self, scope_id: str) -> int:
        """重置指定 scan scope 的 baseline state，供非使用者 start 的安全清理使用。"""

        normalized_scope_id = scope_id.strip()
        if not normalized_scope_id:
            return 0
        now = encode_datetime(utc_now())
        self.connection.execute(
            """
            INSERT INTO scan_scope_state (scope_id, initialized, updated_at)
            VALUES (?, 0, ?)
            ON CONFLICT(scope_id) DO UPDATE SET
                initialized = 0,
                updated_at = excluded.updated_at
            """,
            (normalized_scope_id, now),
        )
        return 1

    def clear_all(self) -> int:
        """將所有已知 scan scope 重置為 baseline 抑制狀態。"""

        now = encode_datetime(utc_now())
        cursor = self.connection.execute(
            """
            UPDATE scan_scope_state
            SET initialized = 0,
                updated_at = ?
            """,
            (now,),
        )
        return int(cursor.rowcount or 0)
