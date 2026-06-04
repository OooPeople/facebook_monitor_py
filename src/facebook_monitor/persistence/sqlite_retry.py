"""SQLite lock 分類與 bounded retry helper。

職責：集中判斷暫時性 SQLite writer contention，並以整個 application
operation 為單位重試，避免各層自行用字串比對或在 transaction 中半途重跑。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import sqlite3
import time
from typing import TypeVar


T = TypeVar("T")

DEFAULT_SQLITE_LOCK_RETRY_DELAYS: tuple[float, ...] = (0.05, 0.1, 0.2, 0.5, 1.0)
_SQLITE_LOCK_PRIMARY_CODES = {
    getattr(sqlite3, "SQLITE_BUSY", 5),
    getattr(sqlite3, "SQLITE_LOCKED", 6),
}
_SQLITE_LOCK_MESSAGES = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


def is_sqlite_lock_error(exc: BaseException) -> bool:
    """判斷例外是否代表 SQLite 暫時性 busy/locked contention。"""

    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int):
        if code in _SQLITE_LOCK_PRIMARY_CODES or (code & 0xFF) in _SQLITE_LOCK_PRIMARY_CODES:
            return True
    name = str(getattr(exc, "sqlite_errorname", "") or "").upper()
    if name.startswith("SQLITE_BUSY") or name.startswith("SQLITE_LOCKED"):
        return True
    message = str(exc).lower()
    return any(text in message for text in _SQLITE_LOCK_MESSAGES)


def run_sqlite_operation_with_retry(
    operation: Callable[[], T],
    *,
    operation_name: str,
    logger: logging.Logger | None = None,
    retry_delays: tuple[float, ...] = DEFAULT_SQLITE_LOCK_RETRY_DELAYS,
) -> T:
    """同步執行一個 DB operation，遇到暫時性 lock 時 bounded retry。"""

    max_attempts = len(retry_delays) + 1
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if not is_sqlite_lock_error(exc) or attempt >= max_attempts:
                _log_retry_exhausted(
                    logger,
                    operation_name=operation_name,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    exc=exc,
                )
                raise
            delay = retry_delays[attempt - 1]
            _log_retry(
                logger,
                operation_name=operation_name,
                attempt=attempt,
                max_attempts=max_attempts,
                delay=delay,
                exc=exc,
            )
            time.sleep(delay)
    raise AssertionError("unreachable sqlite retry state")


async def run_sqlite_operation_with_retry_async(
    operation: Callable[[], T],
    *,
    operation_name: str,
    logger: logging.Logger | None = None,
    retry_delays: tuple[float, ...] = DEFAULT_SQLITE_LOCK_RETRY_DELAYS,
) -> T:
    """在 thread 中執行同步 DB operation，避免 SQLite busy timeout 卡住 event loop。"""

    max_attempts = len(retry_delays) + 1
    for attempt in range(1, max_attempts + 1):
        try:
            return await asyncio.to_thread(operation)
        except sqlite3.OperationalError as exc:
            if not is_sqlite_lock_error(exc) or attempt >= max_attempts:
                _log_retry_exhausted(
                    logger,
                    operation_name=operation_name,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    exc=exc,
                )
                raise
            delay = retry_delays[attempt - 1]
            _log_retry(
                logger,
                operation_name=operation_name,
                attempt=attempt,
                max_attempts=max_attempts,
                delay=delay,
                exc=exc,
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable sqlite retry state")


def _log_retry(
    logger: logging.Logger | None,
    *,
    operation_name: str,
    attempt: int,
    max_attempts: int,
    delay: float,
    exc: sqlite3.OperationalError,
) -> None:
    """記錄下一次 retry 的結構化訊息。"""

    if logger is None:
        return
    logger.warning(
        "sqlite_operation_retry operation=%s attempt=%s max_attempts=%s "
        "delay_seconds=%.3f error=%s",
        operation_name,
        attempt,
        max_attempts,
        delay,
        exc,
    )


def _log_retry_exhausted(
    logger: logging.Logger | None,
    *,
    operation_name: str,
    attempt: int,
    max_attempts: int,
    exc: sqlite3.OperationalError,
) -> None:
    """記錄 retry 已用盡的訊息；非 lock 錯誤不降級處理。"""

    if logger is None or not is_sqlite_lock_error(exc):
        return
    logger.error(
        "sqlite_operation_retry_exhausted operation=%s attempt=%s max_attempts=%s "
        "error=%s",
        operation_name,
        attempt,
        max_attempts,
        exc,
    )
