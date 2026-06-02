"""Resident worker runtime exception classification helpers."""

from __future__ import annotations


def _is_playwright_driver_shutdown_exception(exc: object) -> bool:
    """判斷是否為 Playwright driver 關閉期間產生的已知背景 future 例外。"""

    return isinstance(exc, Exception) and "Connection closed while reading from the driver" in str(
        exc
    )
