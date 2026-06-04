"""Web route DB operation helper tests。"""

from __future__ import annotations

import asyncio
import sqlite3
import threading

from facebook_monitor.webapp.dependencies import run_web_db_operation
from facebook_monitor.webapp.dependencies import run_web_read_operation


def test_run_web_db_operation_retries_sqlite_lock() -> None:
    """Web route DB helper 遇到 transient SQLite lock 會重試整個 operation。"""

    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    result = asyncio.run(
        run_web_db_operation(
            operation,
            operation_name="test",
        )
    )

    assert result == "ok"
    assert attempts == 2


def test_run_web_read_operation_runs_off_event_loop_thread() -> None:
    """Web read helper 應把同步 SQLite read 移出 ASGI event loop thread。"""

    async def run() -> int:
        event_loop_thread_id = threading.get_ident()
        read_thread_id = await run_web_read_operation(
            threading.get_ident,
            operation_name="test.read",
        )
        assert read_thread_id != event_loop_thread_id
        return read_thread_id

    assert isinstance(asyncio.run(run()), int)
