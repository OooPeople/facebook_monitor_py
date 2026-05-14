"""SQLite repository implementation。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import replace

from facebook_monitor.core.keyword_rules import format_keyword_rules
from facebook_monitor.core.keyword_rules import split_keyword_rule_text
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.persistence.row_mappers import latest_scan_item_from_row
from facebook_monitor.persistence.sqlite_codec import encode_datetime

class LatestScanItemRepository:
    """保存每個 target 最近一輪掃描到的貼文候選。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def replace_for_target(self, target_id: str, items: Iterable[LatestScanItem]) -> None:
        """覆蓋單一 target 的最近掃描貼文清單。"""

        item_list = list(items)
        self.connection.execute(
            "DELETE FROM latest_scan_item_matches WHERE target_id = ?",
            (target_id,),
        )
        self.connection.execute("DELETE FROM latest_scan_items WHERE target_id = ?", (target_id,))
        self.connection.executemany(
            """
            INSERT INTO latest_scan_items (
                target_id, scan_run_id, item_kind, item_key, item_index,
                author, text, permalink, matched_keyword, debug_metadata, scanned_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.target_id,
                    item.scan_run_id,
                    item.item_kind.value,
                    item.item_key,
                    item.item_index,
                    item.author,
                    item.text,
                    item.permalink,
                    item.matched_keyword,
                    json.dumps(item.debug_metadata, ensure_ascii=False),
                    encode_datetime(item.scanned_at),
                )
                for item in item_list
            ],
        )
        self.connection.executemany(
            """
            INSERT INTO latest_scan_item_matches (
                target_id, item_key, match_order, rule
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                (item.target_id, item.item_key, index, rule)
                for item in item_list
                for index, rule in enumerate(_matched_rules_for_item(item))
            ],
        )

    def list_by_target(self, target_id: str, limit: int = 50) -> list[LatestScanItem]:
        """依 target id 查詢最近一輪掃描到的貼文候選。"""

        rows = self.connection.execute(
            """
            SELECT * FROM latest_scan_items
            WHERE target_id = ?
            ORDER BY item_index
            LIMIT ?
            """,
            (target_id, limit),
        ).fetchall()
        return self._enrich_items_with_matches([latest_scan_item_from_row(row) for row in rows])

    def list_by_targets(
        self,
        target_ids: list[str],
        *,
        limit_per_target: int = 50,
    ) -> dict[str, list[LatestScanItem]]:
        """一次查詢多個 target 的最近掃描項目。"""

        unique_target_ids = list(dict.fromkeys(target_id for target_id in target_ids if target_id))
        if not unique_target_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_target_ids)
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM (
                SELECT latest_scan_items.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY target_id
                           ORDER BY item_index
                       ) AS row_number
                FROM latest_scan_items
                WHERE target_id IN ({placeholders})
            )
            WHERE row_number <= ?
            ORDER BY target_id, item_index
            """,
            (*unique_target_ids, max(int(limit_per_target), 1)),
        ).fetchall()
        items_by_target: dict[str, list[LatestScanItem]] = {
            target_id: [] for target_id in unique_target_ids
        }
        for item in self._enrich_items_with_matches(
            [latest_scan_item_from_row(row) for row in rows]
        ):
            items_by_target.setdefault(item.target_id, []).append(item)
        return items_by_target

    def _enrich_items_with_matches(self, items: list[LatestScanItem]) -> list[LatestScanItem]:
        """將正規化 match 子表資料併回 LatestScanItem。"""

        if not items:
            return []
        rules_by_key = self._load_match_rules(
            [(item.target_id, item.item_key) for item in items],
        )
        enriched_items: list[LatestScanItem] = []
        for item in items:
            rules = rules_by_key.get((item.target_id, item.item_key), item.matched_keywords)
            enriched_items.append(
                replace(
                    item,
                    matched_keyword=format_keyword_rules(rules) if rules else item.matched_keyword,
                    matched_keywords=rules,
                )
            )
        return enriched_items

    def _load_match_rules(
        self,
        item_keys: list[tuple[str, str]],
    ) -> dict[tuple[str, str], tuple[str, ...]]:
        """批次讀取 latest scan item 的多命中規則。"""

        unique_keys = list(dict.fromkeys(item_keys))
        if not unique_keys:
            return {}
        clauses = " OR ".join("(target_id = ? AND item_key = ?)" for _ in unique_keys)
        params = [value for key in unique_keys for value in key]
        rows = self.connection.execute(
            f"""
            SELECT target_id, item_key, rule
            FROM latest_scan_item_matches
            WHERE {clauses}
            ORDER BY target_id, item_key, match_order
            """,
            tuple(params),
        ).fetchall()
        rules_by_key: dict[tuple[str, str], list[str]] = {}
        for row in rows:
            rules_by_key.setdefault((row["target_id"], row["item_key"]), []).append(row["rule"])
        return {key: tuple(rules) for key, rules in rules_by_key.items()}


def _matched_rules_for_item(item: LatestScanItem) -> tuple[str, ...]:
    """回傳 item 的正規化多命中規則，保留舊欄位相容。"""

    return item.matched_keywords or split_keyword_rule_text(item.matched_keyword)

