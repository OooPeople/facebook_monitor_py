"""SQLite repository implementation。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.row_mappers import match_history_from_row
from facebook_monitor.persistence.repositories.sqlite_ids import require_lastrowid
from facebook_monitor.persistence.sqlite_codec import encode_datetime

MATCH_HISTORY_GLOBAL_LIMIT = 10


class MatchHistoryRepository:
    """保存 keyword match 歷史。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, entry: MatchHistoryEntry) -> int:
        """新增或刷新 match history，對齊 JS 版最近 10 筆通知歷史語義。"""

        notified_at = entry.notified_at or utc_now()
        created_at = entry.created_at or notified_at
        if entry.item_key:
            self.connection.execute(
                """
                DELETE FROM match_history
                WHERE target_id = ?
                  AND item_key = ?
                """,
                (entry.target_id, entry.item_key),
            )

        cursor = self.connection.execute(
            """
            INSERT INTO match_history (
                target_id, group_id, group_name, item_kind, parent_post_id,
                comment_id, item_key, author, text, permalink, include_rule,
                timestamp_text, notified_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.target_id,
                entry.group_id,
                entry.group_name,
                entry.item_kind.value,
                entry.parent_post_id,
                entry.comment_id,
                entry.item_key,
                entry.author,
                entry.text,
                entry.permalink,
                entry.include_rule,
                entry.timestamp_text,
                encode_datetime(notified_at),
                encode_datetime(created_at),
            ),
        )
        self.prune_global_limit()
        return require_lastrowid(cursor.lastrowid, table_name="match_history")

    def prune_global_limit(self, limit: int = MATCH_HISTORY_GLOBAL_LIMIT) -> int:
        """裁切全域 match history，只保留最近 N 筆，對齊 userscript。"""

        bounded_limit = max(int(limit), 1)
        cursor = self.connection.execute(
            """
            DELETE FROM match_history
            WHERE id NOT IN (
                SELECT id
                FROM match_history
                ORDER BY
                    CASE WHEN notified_at = '' THEN 1 ELSE 0 END,
                    notified_at DESC,
                    id DESC
                LIMIT ?
            )
            """,
            (bounded_limit,),
        )
        return int(cursor.rowcount)

    def list_by_target(
        self,
        target_id: str,
        limit: int = 50,
        *,
        offset: int = 0,
        notified_since: datetime | None = None,
    ) -> list[MatchHistoryEntry]:
        """依 target id 查詢最近 match history。"""

        bounded_limit = max(int(limit), 1)
        bounded_offset = max(int(offset), 0)
        notified_since_filter = ""
        params: list[object] = [target_id]
        if notified_since is not None:
            notified_since_filter = "AND match_history.notified_at >= ?"
            params.append(encode_datetime(notified_since))
        params.extend([bounded_limit, bounded_offset])
        rows = self.connection.execute(
            f"""
            SELECT match_history.*
            FROM match_history
            LEFT JOIN latest_scan_items
              ON latest_scan_items.target_id = match_history.target_id
             AND latest_scan_items.item_key = match_history.item_key
            WHERE match_history.target_id = ?
              {notified_since_filter}
            ORDER BY
                CASE WHEN latest_scan_items.item_index IS NULL THEN 1 ELSE 0 END,
                latest_scan_items.item_index ASC,
                match_history.notified_at DESC,
                match_history.id DESC
            LIMIT ?
            OFFSET ?
            """,
            tuple(params),
        ).fetchall()
        return [match_history_from_row(row) for row in rows]

    def list_by_targets(
        self,
        target_ids: list[str],
        *,
        limit_per_target: int = 50,
        notified_since: datetime | None = None,
    ) -> dict[str, list[MatchHistoryEntry]]:
        """一次查詢多個 target 的最近 match history。"""

        unique_target_ids = list(dict.fromkeys(target_id for target_id in target_ids if target_id))
        if not unique_target_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_target_ids)
        notified_since_filter = ""
        params: list[object] = [*unique_target_ids]
        if notified_since is not None:
            notified_since_filter = "AND match_history.notified_at >= ?"
            params.append(encode_datetime(notified_since))
        params.append(max(int(limit_per_target), 1))
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM (
                SELECT match_history.*,
                       latest_scan_items.item_index AS latest_item_index,
                       ROW_NUMBER() OVER (
                           PARTITION BY match_history.target_id
                           ORDER BY
                               CASE
                                   WHEN latest_scan_items.item_index IS NULL THEN 1
                                   ELSE 0
                               END,
                               latest_scan_items.item_index ASC,
                               match_history.id DESC
                       ) AS row_number
                FROM match_history
                LEFT JOIN latest_scan_items
                 ON latest_scan_items.target_id = match_history.target_id
                 AND latest_scan_items.item_key = match_history.item_key
                WHERE match_history.target_id IN ({placeholders})
                  {notified_since_filter}
            )
            WHERE row_number <= ?
            ORDER BY
                target_id,
                CASE WHEN latest_item_index IS NULL THEN 1 ELSE 0 END,
                latest_item_index ASC,
                notified_at DESC,
                id DESC
            """,
            tuple(params),
        ).fetchall()
        entries_by_target: dict[str, list[MatchHistoryEntry]] = {
            target_id: [] for target_id in unique_target_ids
        }
        for row in rows:
            entry = match_history_from_row(row)
            entries_by_target.setdefault(entry.target_id, []).append(entry)
        return entries_by_target

    def count_by_target(
        self,
        target_id: str,
        *,
        notified_since: datetime | None = None,
    ) -> int:
        """計算單一 target 的 match history 筆數。"""

        notified_since_filter = ""
        params: list[object] = [target_id]
        if notified_since is not None:
            notified_since_filter = "AND notified_at >= ?"
            params.append(encode_datetime(notified_since))
        row = self.connection.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM match_history
            WHERE target_id = ?
              {notified_since_filter}
            """,
            tuple(params),
        ).fetchone()
        return int(row["count"] if row else 0)

    def count_by_targets(
        self,
        target_ids: list[str],
        *,
        notified_since: datetime | None = None,
    ) -> dict[str, int]:
        """一次計算多個 target 的 match history 筆數。"""

        unique_target_ids = list(dict.fromkeys(target_id for target_id in target_ids if target_id))
        if not unique_target_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_target_ids)
        notified_since_filter = ""
        params: list[object] = [*unique_target_ids]
        if notified_since is not None:
            notified_since_filter = "AND notified_at >= ?"
            params.append(encode_datetime(notified_since))
        rows = self.connection.execute(
            f"""
            SELECT target_id, COUNT(*) AS count
            FROM match_history
            WHERE target_id IN ({placeholders})
              {notified_since_filter}
            GROUP BY target_id
            """,
            tuple(params),
        ).fetchall()
        counts = {target_id: 0 for target_id in unique_target_ids}
        for row in rows:
            counts[str(row["target_id"])] = int(row["count"])
        return counts

    def clear_by_target(self, target_id: str) -> int:
        """清空單一 target 的 match history 並回傳刪除筆數。"""

        cursor = self.connection.execute(
            """
            DELETE FROM match_history
            WHERE target_id = ?
            """,
            (target_id,),
        )
        return int(cursor.rowcount)

