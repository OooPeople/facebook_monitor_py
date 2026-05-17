"""SQLite repository implementation。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from facebook_monitor.core.models import SeenItem
from facebook_monitor.persistence.sqlite_codec import encode_datetime

class SeenItemRepository:
    """保存 seen item 去重狀態。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def mark_seen(self, item: SeenItem) -> bool:
        """標記 item 已看過；回傳是否為第一次看見。"""

        return self.mark_seen_aliases(item, (item.item_key,))

    def mark_seen_aliases(self, item: SeenItem, item_keys: Iterable[str]) -> bool:
        """標記 item 與所有等價 aliases 已看過；回傳 aliases 是否全新。"""

        keys = tuple(dict.fromkeys(key.strip() for key in item_keys if key.strip()))
        if not keys:
            return False

        is_new = not self.has_seen_any(item.scope_id, keys)
        for item_key in keys:
            self._upsert_seen_item(
                SeenItem(
                    scope_id=item.scope_id,
                    item_key=item_key,
                    item_kind=item.item_kind,
                    parent_post_id=item.parent_post_id,
                    comment_id=item.comment_id,
                    first_seen_at=item.first_seen_at,
                    last_seen_at=item.last_seen_at,
                )
            )
        return is_new

    def _upsert_seen_item(self, item: SeenItem) -> None:
        """新增或更新單一 seen item key。"""

        existing = self.connection.execute(
            "SELECT first_seen_at FROM seen_items WHERE scope_id = ? AND item_key = ?",
            (item.scope_id, item.item_key),
        ).fetchone()
        if existing:
            self.connection.execute(
                """
                UPDATE seen_items
                SET last_seen_at = ?, item_kind = ?, parent_post_id = ?, comment_id = ?
                WHERE scope_id = ? AND item_key = ?
                """,
                (
                    encode_datetime(item.last_seen_at),
                    item.item_kind.value,
                    item.parent_post_id,
                    item.comment_id,
                    item.scope_id,
                    item.item_key,
                ),
            )
            return

        self.connection.execute(
            """
            INSERT INTO seen_items (
                scope_id, item_key, item_kind, parent_post_id, comment_id,
                first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.scope_id,
                item.item_key,
                item.item_kind.value,
                item.parent_post_id,
                item.comment_id,
                encode_datetime(item.first_seen_at),
                encode_datetime(item.last_seen_at),
            ),
        )

    def has_seen(self, scope_id: str, item_key: str) -> bool:
        """檢查 item 是否已看過。"""

        return self.has_seen_any(scope_id, (item_key,))

    def has_seen_any(self, scope_id: str, item_keys: Iterable[str]) -> bool:
        """檢查任一 item key alias 是否已看過。"""

        keys = tuple(dict.fromkeys(key.strip() for key in item_keys if key.strip()))
        if not keys:
            return False
        placeholders = ", ".join("?" for _ in keys)
        row = self.connection.execute(
            f"""
            SELECT 1 FROM seen_items
            WHERE scope_id = ? AND item_key IN ({placeholders})
            LIMIT 1
            """,
            (scope_id, *keys),
        ).fetchone()
        return row is not None

    def clear_scope(self, scope_id: str) -> int:
        """清空指定 scan scope 的 seen item，支援開始監控語義。"""

        normalized_scope_id = scope_id.strip()
        if not normalized_scope_id:
            return 0
        cursor = self.connection.execute(
            "DELETE FROM seen_items WHERE scope_id = ?",
            (normalized_scope_id,),
        )
        return cursor.rowcount

