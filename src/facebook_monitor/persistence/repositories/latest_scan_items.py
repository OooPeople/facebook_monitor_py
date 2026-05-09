"""SQLite repository implementation。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.persistence.row_mappers import latest_scan_item_from_row
from facebook_monitor.persistence.sqlite_codec import encode_datetime

class LatestScanItemRepository:
    """保存每個 target 最近一輪掃描到的貼文候選。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def replace_for_target(self, target_id: str, items: Iterable[LatestScanItem]) -> None:
        """覆蓋單一 target 的最近掃描貼文清單。"""

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
                for item in items
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
        return [latest_scan_item_from_row(row) for row in rows]

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
        for row in rows:
            item = latest_scan_item_from_row(row)
            items_by_target.setdefault(item.target_id, []).append(item)
        return items_by_target

