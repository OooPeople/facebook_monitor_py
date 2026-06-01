"""SQLite lock retry helper tests。"""

from __future__ import annotations

import asyncio
import sqlite3
import threading

from facebook_monitor.persistence.sqlite_retry import is_sqlite_lock_error
from facebook_monitor.persistence.sqlite_retry import run_sqlite_operation_with_retry
from facebook_monitor.persistence.sqlite_retry import run_sqlite_operation_with_retry_async


def test_is_sqlite_lock_error_matches_busy_and_locked_messages() -> None:
    """SQLite lock helper 應辨識 busy/locked，且不吞其他 OperationalError。"""

    assert is_sqlite_lock_error(sqlite3.OperationalError("database is locked"))
    assert is_sqlite_lock_error(sqlite3.OperationalError("database table is locked"))
    assert is_sqlite_lock_error(sqlite3.OperationalError("database is busy"))
    assert not is_sqlite_lock_error(sqlite3.OperationalError("no such table: targets"))
    assert not is_sqlite_lock_error(RuntimeError("database is locked"))


def test_run_sqlite_operation_with_retry_retries_only_lock_errors() -> None:
    """同步 retry 僅對 SQLite lock 重跑，其他 DB 錯誤直接浮出。"""

    calls = 0

    def flaky_operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert (
        run_sqlite_operation_with_retry(
            flaky_operation,
            operation_name="test_sync",
            retry_delays=(0,),
        )
        == "ok"
    )
    assert calls == 2

    def invalid_operation() -> None:
        raise sqlite3.OperationalError("no such table: targets")

    try:
        run_sqlite_operation_with_retry(
            invalid_operation,
            operation_name="test_sync_invalid",
            retry_delays=(0,),
        )
    except sqlite3.OperationalError as exc:
        assert "no such table" in str(exc)
    else:
        raise AssertionError("non-lock OperationalError should not retry")


def test_run_sqlite_operation_with_retry_async_uses_async_sleep() -> None:
    """async retry 應在 thread 中跑同步 DB operation 並於 lock 後重試。"""

    calls = 0
    main_thread_id = threading.get_ident()
    operation_thread_ids: list[int] = []

    def flaky_operation() -> str:
        nonlocal calls
        calls += 1
        operation_thread_ids.append(threading.get_ident())
        if calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    result = asyncio.run(
        run_sqlite_operation_with_retry_async(
            flaky_operation,
            operation_name="test_async",
            retry_delays=(0,),
        )
    )

    assert result == "ok"
    assert calls == 2
    assert operation_thread_ids
    assert all(thread_id != main_thread_id for thread_id in operation_thread_ids)
