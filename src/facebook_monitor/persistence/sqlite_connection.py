"""SQLite connection boundary。"""

from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from pathlib import Path


class SqliteConnection(AbstractContextManager["SqliteConnection"]):
    """管理 SQLite 連線與 schema 初始化。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> SqliteConnection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA foreign_keys = ON")
        self.connection = connection
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if not self.connection:
            return
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        self.connection.close()
        self.connection = None

    def require_connection(self) -> sqlite3.Connection:
        """取得已開啟連線，未開啟時回報明確錯誤。"""

        if self.connection is None:
            raise RuntimeError("SQLite connection is not open")
        return self.connection
