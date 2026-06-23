"""Resident worker shutdown exception helpers."""

from __future__ import annotations

from collections.abc import Mapping

from facebook_monitor.worker.playwright_runtime_errors import (
    is_playwright_runtime_closed_exception,
)


_UNRETRIEVED_FUTURE_EXCEPTION_MESSAGE = "future exception was never retrieved"


def is_playwright_shutdown_noise_context(context: Mapping[str, object]) -> bool:
    """判斷 event loop context 是否為可消化的 Playwright shutdown 背景 Future 噪音。"""

    message = str(context.get("message", "")).lower()
    if _UNRETRIEVED_FUTURE_EXCEPTION_MESSAGE not in message:
        return False
    return is_playwright_runtime_closed_exception(context.get("exception"))
