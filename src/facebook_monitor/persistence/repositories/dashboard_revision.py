"""Dashboard revision read-side repository。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class DashboardRevisionSnapshot:
    """保存 dashboard polling 使用的輕量 revision。"""

    revision: str
    last_changed_at: str = ""


class DashboardRevisionRepository:
    """提供 dashboard 變更偵測用的 aggregate query。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get_dashboard_revision(self) -> DashboardRevisionSnapshot:
        """讀取單列 dashboard revision，不掃描 dashboard 相關資料表。"""

        row = self.connection.execute(
            "SELECT revision, updated_at FROM dashboard_revision WHERE id = 1"
        ).fetchone()
        if row is None:
            return DashboardRevisionSnapshot(revision="0", last_changed_at="")
        return DashboardRevisionSnapshot(
            revision=str(row["revision"]),
            last_changed_at=row["updated_at"],
        )
