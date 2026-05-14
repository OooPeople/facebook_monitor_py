"""SQLite repository implementation。"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from dataclasses import replace

from facebook_monitor.core.keyword_rules import format_keyword_rules
from facebook_monitor.core.keyword_rules import split_keyword_rule_text
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
                DELETE FROM match_history_matches
                WHERE history_id IN (
                    SELECT id
                    FROM match_history
                    WHERE target_id = ?
                      AND item_key = ?
                )
                """,
                (entry.target_id, entry.item_key),
            )
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
        history_id = require_lastrowid(cursor.lastrowid, table_name="match_history")
        self._save_match_rules(history_id, _include_rules_for_entry(entry))
        self.prune_global_limit()
        return history_id

    def prune_global_limit(self, limit: int = MATCH_HISTORY_GLOBAL_LIMIT) -> int:
        """裁切全域 match history，只保留最近 N 筆，對齊 userscript。"""

        bounded_limit = max(int(limit), 1)
        keep_rows = self.connection.execute(
            """
            SELECT id
            FROM match_history
            ORDER BY
                CASE WHEN notified_at = '' THEN 1 ELSE 0 END,
                notified_at DESC,
                id DESC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
        keep_ids = [int(row["id"]) for row in keep_rows]
        if keep_ids:
            placeholders = ",".join("?" for _ in keep_ids)
            self.connection.execute(
                f"""
                DELETE FROM match_history_matches
                WHERE history_id NOT IN ({placeholders})
                """,
                tuple(keep_ids),
            )
        else:
            self.connection.execute("DELETE FROM match_history_matches")
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
        return self._enrich_entries_with_matches(rows)

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
        for entry in self._enrich_entries_with_matches(rows):
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

        self.connection.execute(
            """
            DELETE FROM match_history_matches
            WHERE history_id IN (
                SELECT id FROM match_history WHERE target_id = ?
            )
            """,
            (target_id,),
        )
        cursor = self.connection.execute(
            """
            DELETE FROM match_history
            WHERE target_id = ?
            """,
            (target_id,),
        )
        return int(cursor.rowcount)

    def _save_match_rules(self, history_id: int, rules: tuple[str, ...]) -> None:
        """保存單筆 history 的多命中規則。"""

        self.connection.executemany(
            """
            INSERT INTO match_history_matches (history_id, match_order, rule)
            VALUES (?, ?, ?)
            """,
            [(history_id, index, rule) for index, rule in enumerate(rules)],
        )

    def _enrich_entries_with_matches(self, rows: list[sqlite3.Row]) -> list[MatchHistoryEntry]:
        """將正規化 match 子表資料併回 MatchHistoryEntry。"""

        if not rows:
            return []
        entries = [match_history_from_row(row) for row in rows]
        ids = [int(row["id"]) for row in rows]
        rules_by_id = self._load_match_rules(ids)
        enriched_entries: list[MatchHistoryEntry] = []
        for entry, history_id in zip(entries, ids, strict=True):
            rules = rules_by_id.get(history_id, entry.include_rules)
            enriched_entries.append(
                replace(
                    entry,
                    include_rule=format_keyword_rules(rules) if rules else entry.include_rule,
                    include_rules=rules,
                )
            )
        return enriched_entries

    def _load_match_rules(self, history_ids: list[int]) -> dict[int, tuple[str, ...]]:
        """批次讀取 match history 的多命中規則。"""

        unique_ids = list(dict.fromkeys(history_ids))
        if not unique_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self.connection.execute(
            f"""
            SELECT history_id, rule
            FROM match_history_matches
            WHERE history_id IN ({placeholders})
            ORDER BY history_id, match_order
            """,
            tuple(unique_ids),
        ).fetchall()
        rules_by_id: dict[int, list[str]] = {}
        for row in rows:
            rules_by_id.setdefault(int(row["history_id"]), []).append(row["rule"])
        return {history_id: tuple(rules) for history_id, rules in rules_by_id.items()}


def _include_rules_for_entry(entry: MatchHistoryEntry) -> tuple[str, ...]:
    """回傳 entry 的正規化多命中規則，保留舊欄位相容。"""

    return entry.include_rules or split_keyword_rule_text(entry.include_rule)

