"""Web read model shared SQLite context helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.persistence.sqlite_retry import is_sqlite_lock_error
from facebook_monitor.webapp.dashboard_read_models import DashboardReadUnavailable


def read_application_context(db_path: Path) -> SqliteApplicationContext:
    """建立 Web UI read model 用 context，避免 partial update 跑 schema init。"""

    return SqliteApplicationContext(db_path, initialize_schema_on_enter=False)


def raise_dashboard_read_unavailable_if_locked(exc: sqlite3.OperationalError) -> None:
    """將 SQLite lock 轉成 route 可處理的 read model 暫不可用錯誤。"""

    if is_sqlite_lock_error(exc):
        raise DashboardReadUnavailable(str(exc)) from exc
