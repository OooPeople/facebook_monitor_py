"""SQLite repository for logical item identity and aliases."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from facebook_monitor.core.models import SeenAliasMarkResult
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.repositories.dedupe_state import DedupeStateRepository
from facebook_monitor.persistence.sqlite_codec import encode_datetime


class LogicalItemRepository:
    """保存一篇貼文/留言與其多組 Facebook identity aliases。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.dedupe_state = DedupeStateRepository(connection)

    def mark_seen_aliases(
        self,
        *,
        target_id: str,
        item: SeenItem,
        item_keys: Iterable[str],
    ) -> SeenAliasMarkResult:
        """標記 logical item aliases 已看過，並回傳 logical 去重結果。"""

        keys = tuple(dict.fromkeys(key.strip() for key in item_keys if key.strip()))
        if not target_id.strip() or not keys:
            return SeenAliasMarkResult(
                is_new=False,
                logical_item_id=0,
                canonical_item_key=item.item_key,
                alias_keys=(),
            )

        epoch = self.dedupe_state.peek_current_epoch(target_id)
        hit_ids = self._find_logical_item_ids_for_aliases(
            target_id=target_id,
            scope_id=item.scope_id,
            dedupe_epoch=epoch,
            alias_keys=keys,
        )
        is_new = not hit_ids
        logical_item_id = hit_ids[0] if hit_ids else 0
        if logical_item_id == 0:
            logical_item_id = self._find_existing_comment_identity(
                target_id=target_id,
                dedupe_epoch=epoch,
                item=item,
            )
            is_new = logical_item_id == 0
        if logical_item_id == 0:
            logical_item_id = self._create_logical_item(
                target_id=target_id,
                dedupe_epoch=epoch,
                item=item,
                canonical_item_key=keys[0],
            )
        self._touch_logical_item(logical_item_id=logical_item_id, item=item)
        for alias_key in keys:
            self._upsert_alias(
                logical_item_id=logical_item_id,
                target_id=target_id,
                scope_id=item.scope_id,
                dedupe_epoch=epoch,
                alias_key=alias_key,
                item=item,
            )
        return SeenAliasMarkResult(
            is_new=is_new,
            logical_item_id=logical_item_id,
            canonical_item_key=keys[0],
            alias_keys=keys,
        )

    def has_seen_any(
        self,
        *,
        target_id: str,
        scope_id: str,
        item_keys: Iterable[str],
    ) -> bool:
        """檢查目前 dedupe epoch 中任一 alias 是否已看過。"""

        keys = tuple(dict.fromkeys(key.strip() for key in item_keys if key.strip()))
        if not target_id.strip() or not scope_id.strip() or not keys:
            return False
        epoch = self.dedupe_state.peek_current_epoch(target_id)
        placeholders = ", ".join("?" for _ in keys)
        row = self.connection.execute(
            f"""
            SELECT 1
            FROM logical_item_aliases
            WHERE target_id = ?
              AND scope_id = ?
              AND dedupe_epoch = ?
              AND alias_key IN ({placeholders})
            LIMIT 1
            """,
            (target_id, scope_id, epoch, *keys),
        ).fetchone()
        return row is not None

    def clear_target_scope_current_epoch(self, *, target_id: str, scope_id: str) -> int:
        """刪除 target 目前 epoch 的 logical seen rows，供明確 reset 使用。"""

        if not target_id.strip() or not scope_id.strip():
            return 0
        epoch = self.dedupe_state.current_epoch(target_id)
        alias_count = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM logical_item_aliases
            WHERE target_id = ?
              AND scope_id = ?
              AND dedupe_epoch = ?
            """,
            (target_id, scope_id, epoch),
        ).fetchone()[0]
        self.connection.execute(
            """
            DELETE FROM logical_items
            WHERE target_id = ?
              AND scope_id = ?
              AND dedupe_epoch = ?
            """,
            (target_id, scope_id, epoch),
        )
        return int(alias_count or 0)

    def _find_logical_item_ids_for_aliases(
        self,
        *,
        target_id: str,
        scope_id: str,
        dedupe_epoch: int,
        alias_keys: tuple[str, ...],
    ) -> list[int]:
        """依 aliases 找出既有 logical item ids。"""

        placeholders = ", ".join("?" for _ in alias_keys)
        rows = self.connection.execute(
            f"""
            SELECT logical_item_id
            FROM logical_item_aliases
            WHERE target_id = ?
              AND scope_id = ?
              AND dedupe_epoch = ?
              AND alias_key IN ({placeholders})
            GROUP BY logical_item_id
            ORDER BY MIN(id)
            """,
            (target_id, scope_id, dedupe_epoch, *alias_keys),
        ).fetchall()
        return [int(row["logical_item_id"]) for row in rows]

    def _find_existing_comment_identity(
        self,
        *,
        target_id: str,
        dedupe_epoch: int,
        item: SeenItem,
    ) -> int:
        """comments target 用 parent/comment id 作安全 identity fallback。"""

        if item.item_kind.value != "comment" or not item.comment_id:
            return 0
        row = self.connection.execute(
            """
            SELECT id
            FROM logical_items
            WHERE target_id = ?
              AND dedupe_epoch = ?
              AND item_kind = ?
              AND parent_post_id = ?
              AND comment_id = ?
            ORDER BY id
            LIMIT 1
            """,
            (
                target_id,
                dedupe_epoch,
                item.item_kind.value,
                item.parent_post_id,
                item.comment_id,
            ),
        ).fetchone()
        return int(row["id"]) if row is not None else 0

    def _create_logical_item(
        self,
        *,
        target_id: str,
        dedupe_epoch: int,
        item: SeenItem,
        canonical_item_key: str,
    ) -> int:
        """新增 logical item row。"""

        now_text = encode_datetime(utc_now())
        self.connection.execute(
            """
            INSERT INTO logical_items (
                target_id, scope_id, dedupe_epoch, item_kind, canonical_item_key,
                parent_post_id, comment_id, first_seen_at, last_seen_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_id,
                item.scope_id,
                dedupe_epoch,
                item.item_kind.value,
                canonical_item_key,
                item.parent_post_id,
                item.comment_id,
                encode_datetime(item.first_seen_at),
                encode_datetime(item.last_seen_at),
                now_text,
                now_text,
            ),
        )
        return int(self.connection.execute("SELECT last_insert_rowid()").fetchone()[0])

    def _touch_logical_item(self, *, logical_item_id: int, item: SeenItem) -> None:
        """更新 logical item 最近一次看見時間與目前可觀察 metadata。"""

        self.connection.execute(
            """
            UPDATE logical_items
            SET last_seen_at = ?,
                item_kind = ?,
                parent_post_id = ?,
                comment_id = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                encode_datetime(item.last_seen_at),
                item.item_kind.value,
                item.parent_post_id,
                item.comment_id,
                encode_datetime(utc_now()),
                logical_item_id,
            ),
        )

    def _upsert_alias(
        self,
        *,
        logical_item_id: int,
        target_id: str,
        scope_id: str,
        dedupe_epoch: int,
        alias_key: str,
        item: SeenItem,
    ) -> None:
        """新增 alias；若 alias 已屬於其他 logical item，保守不強制合併。"""

        now_text = encode_datetime(utc_now())
        self.connection.execute(
            """
            INSERT OR IGNORE INTO logical_item_aliases (
                logical_item_id, target_id, scope_id, dedupe_epoch, alias_key,
                first_seen_at, last_seen_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                logical_item_id,
                target_id,
                scope_id,
                dedupe_epoch,
                alias_key,
                encode_datetime(item.first_seen_at),
                encode_datetime(item.last_seen_at),
                now_text,
                now_text,
            ),
        )
        self.connection.execute(
            """
            UPDATE logical_item_aliases
            SET last_seen_at = ?,
                updated_at = ?
            WHERE target_id = ?
              AND dedupe_epoch = ?
              AND alias_key = ?
              AND logical_item_id = ?
            """,
            (
                encode_datetime(item.last_seen_at),
                now_text,
                target_id,
                dedupe_epoch,
                alias_key,
                logical_item_id,
            ),
        )
