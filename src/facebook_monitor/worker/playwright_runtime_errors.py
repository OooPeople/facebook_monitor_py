"""Playwright runtime closed 訊息判斷。

職責：集中描述 Playwright driver / browser / context / page 已關閉的低階訊息；
不決定 scan failure policy，也不決定 event loop shutdown 是否要吞例外。
"""

from __future__ import annotations

from playwright.async_api import Error as AsyncPlaywrightError
from playwright.sync_api import Error as SyncPlaywrightError


PLAYWRIGHT_UNAMBIGUOUS_RUNTIME_CLOSED_TOKENS: tuple[str, ...] = (
    "connection closed while reading from the driver",
    "target page, context or browser has been closed",
    "page, context or browser has been closed",
    "browser has been closed",
    "context has been closed",
    "page has been closed",
)
PLAYWRIGHT_AMBIGUOUS_RUNTIME_CLOSED_TOKENS: tuple[str, ...] = (
    "target closed",
)
PLAYWRIGHT_RUNTIME_CLOSED_TOKENS: tuple[str, ...] = (
    PLAYWRIGHT_UNAMBIGUOUS_RUNTIME_CLOSED_TOKENS
    + PLAYWRIGHT_AMBIGUOUS_RUNTIME_CLOSED_TOKENS
)


def is_playwright_runtime_closed_message(message: str) -> bool:
    """判斷訊息是否代表 Playwright runtime 或其 page/context 已關閉。"""

    normalized = message.lower()
    return any(token in normalized for token in PLAYWRIGHT_RUNTIME_CLOSED_TOKENS)


def is_unambiguous_playwright_runtime_closed_message(message: str) -> bool:
    """判斷訊息是否為高信心 Playwright runtime closed，不含短 token。"""

    normalized = message.lower()
    return any(token in normalized for token in PLAYWRIGHT_UNAMBIGUOUS_RUNTIME_CLOSED_TOKENS)


def is_playwright_runtime_closed_exception(exc: object) -> bool:
    """判斷 exception 本身是否帶有 Playwright runtime closed 訊息。"""

    return isinstance(exc, Exception) and is_playwright_runtime_closed_message(str(exc))


def is_playwright_exception(exc: object) -> bool:
    """判斷 exception 是否為 Playwright 公開 API 例外型別。"""

    return isinstance(exc, (AsyncPlaywrightError, SyncPlaywrightError))
