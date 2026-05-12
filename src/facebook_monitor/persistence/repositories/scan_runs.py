"""SQLite repository implementation。"""

from __future__ import annotations

import json
import sqlite3

from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.persistence.row_mappers import scan_run_from_row
from facebook_monitor.persistence.repositories.sqlite_ids import require_lastrowid
from facebook_monitor.persistence.sqlite_codec import encode_datetime


class ScanRunRepository:
    """保存 scan run 結果。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, run: ScanRun) -> int:
        """新增 scan run 並回傳 row id。"""

        cursor = self.connection.execute(
            """
            INSERT INTO scan_runs (
                target_id, started_at, finished_at, status, item_count,
                matched_count, error_message, worker_mode, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.target_id,
                encode_datetime(run.started_at),
                encode_datetime(run.finished_at),
                run.status.value,
                run.item_count,
                run.matched_count,
                run.error_message,
                run.worker_mode.value,
                json.dumps(run.metadata, ensure_ascii=False),
            ),
        )
        return require_lastrowid(cursor.lastrowid, table_name="scan_runs")

    def latest_by_target(
        self,
        target_id: str,
        status: ScanStatus | None = None,
    ) -> ScanRun | None:
        """查詢單一 target 最近一筆 scan run，可依狀態過濾。"""

        if status is None:
            row = self.connection.execute(
                """
                SELECT * FROM scan_runs
                WHERE target_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (target_id,),
            ).fetchone()
        else:
            row = self.connection.execute(
                """
                SELECT * FROM scan_runs
                WHERE target_id = ? AND status = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (target_id, status.value),
            ).fetchone()
        return scan_run_from_row(row) if row else None

    def latest_by_targets(
        self,
        target_ids: list[str],
        status: ScanStatus | None = None,
    ) -> dict[str, ScanRun]:
        """一次查詢多個 target 的最近 scan run，可依狀態過濾。"""

        unique_target_ids = list(dict.fromkeys(target_id for target_id in target_ids if target_id))
        if not unique_target_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_target_ids)
        params: tuple[object, ...]
        status_filter = ""
        if status is None:
            params = tuple(unique_target_ids)
        else:
            status_filter = "AND status = ?"
            params = (*unique_target_ids, status.value)
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM (
                SELECT scan_runs.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY target_id
                           ORDER BY id DESC
                       ) AS row_number
                FROM scan_runs
                WHERE target_id IN ({placeholders})
                {status_filter}
            )
            WHERE row_number = 1
            """,
            params,
        ).fetchall()
        runs: dict[str, ScanRun] = {}
        for row in rows:
            run = scan_run_from_row(row)
            runs[run.target_id] = run
        return runs

