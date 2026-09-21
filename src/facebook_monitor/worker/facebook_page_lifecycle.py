"""Facebook browser page lifetime 的共用安全檢查。

本模組只處理 Playwright page 關閉確認與 persistent context 啟動清理；
不負責 circuit、pacing 或 target runtime state。
"""

from __future__ import annotations

import logging
import sys
from typing import Any


logger = logging.getLogger(__name__)


class FacebookPageCloseError(RuntimeError):
    """表示 page 無法確認已關閉，後續 browser work 必須 fail closed。"""


def is_page_closed(page: Any) -> bool:
    """以 Playwright 相容介面判斷 page 是否已關閉。"""

    check = getattr(page, "is_closed", None)
    return bool(check()) if callable(check) else False


def open_context_pages(context: Any) -> tuple[Any, ...]:
    """回傳 context 目前仍存活的 pages；測試替身沒有 pages 時回傳空集合。"""

    pages = getattr(context, "pages", ())
    return tuple(page for page in pages if not is_page_closed(page))


async def close_async_browser_resource_preserving_primary(
    resource: Any,
    *,
    description: str,
) -> None:
    """關閉 async browser resource；cleanup 失敗不得蓋掉原始產品例外。"""

    primary_error = sys.exception()
    try:
        await resource.close()
    except Exception:
        if primary_error is None:
            raise
        logger.warning(
            "%s close failed while preserving primary %s",
            description,
            type(primary_error).__name__,
            exc_info=True,
        )


def close_sync_browser_resource_preserving_primary(
    resource: Any,
    *,
    description: str,
) -> None:
    """關閉 sync browser resource；cleanup 失敗不得蓋掉原始產品例外。"""

    primary_error = sys.exception()
    try:
        resource.close()
    except Exception:
        if primary_error is None:
            raise
        logger.warning(
            "%s close failed while preserving primary %s",
            description,
            type(primary_error).__name__,
            exc_info=True,
        )


async def close_page_checked_async(page: Any | None) -> None:
    """關閉 async page 並確認結果；不確定狀態不可靜默忽略。"""

    if page is None or is_page_closed(page):
        return
    try:
        await page.close()
    except Exception as exc:
        if is_page_closed(page):
            return
        raise FacebookPageCloseError("async Facebook page close was not confirmed") from exc
    if not is_page_closed(page):
        raise FacebookPageCloseError("async Facebook page remained open after close")


def close_page_checked_sync(page: Any | None) -> None:
    """關閉 sync page 並確認結果；不確定狀態不可靜默忽略。"""

    if page is None or is_page_closed(page):
        return
    close = getattr(page, "close", None)
    if not callable(close):
        raise FacebookPageCloseError("sync Facebook page does not expose close()")
    try:
        close()
    except Exception as exc:
        if is_page_closed(page):
            return
        raise FacebookPageCloseError("sync Facebook page close was not confirmed") from exc
    if not is_page_closed(page):
        raise FacebookPageCloseError("sync Facebook page remained open after close")


async def close_existing_context_pages_async(context: Any) -> None:
    """在建立正式 page 前關閉 persistent context 自帶的所有既有 pages。"""

    for page in open_context_pages(context):
        await close_page_checked_async(page)
    if open_context_pages(context):
        raise FacebookPageCloseError("persistent context retained an unmanaged open page")


def close_existing_context_pages_sync(context: Any) -> None:
    """在 sync debug/tooling 建立受管 page 前關閉 persistent context 自帶 pages。"""

    for page in open_context_pages(context):
        close_page_checked_sync(page)
    if open_context_pages(context):
        raise FacebookPageCloseError("persistent context retained an unmanaged open page")


__all__ = [
    "FacebookPageCloseError",
    "close_async_browser_resource_preserving_primary",
    "close_existing_context_pages_async",
    "close_existing_context_pages_sync",
    "close_page_checked_async",
    "close_page_checked_sync",
    "close_sync_browser_resource_preserving_primary",
    "is_page_closed",
    "open_context_pages",
]
