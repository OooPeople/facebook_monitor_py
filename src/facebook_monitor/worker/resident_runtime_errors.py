"""Resident worker shutdown exception helpers."""

from __future__ import annotations

from collections.abc import Iterator
from collections.abc import Mapping

from facebook_monitor.worker.playwright_runtime_errors import (
    is_playwright_exception,
    is_playwright_runtime_closed_message,
    is_unambiguous_playwright_runtime_closed_message,
)


_UNRETRIEVED_FUTURE_EXCEPTION_MESSAGE = "future exception was never retrieved"


def is_playwright_shutdown_noise_context(context: Mapping[str, object]) -> bool:
    """判斷 event loop context 是否為可消化的 Playwright shutdown 背景 Future 噪音。"""

    message = str(context.get("message", "")).lower()
    if _UNRETRIEVED_FUTURE_EXCEPTION_MESSAGE not in message:
        return False
    return is_resident_shutdown_runtime_closed_exception(context.get("exception"))


def is_resident_shutdown_runtime_closed_exception(exc: object) -> bool:
    """判斷 shutdown handler 可高信心消化的 Playwright runtime closed 例外。"""

    if not isinstance(exc, Exception):
        return False
    for current in _iter_exception_chain(exc):
        if is_playwright_exception(current) and is_playwright_runtime_closed_message(str(current)):
            return True
        if is_unambiguous_playwright_runtime_closed_message(str(current)):
            return True
    return False


def _iter_exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """走訪 cause/context chain，讓 wrapped Playwright shutdown 例外仍可被辨識。"""

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__
