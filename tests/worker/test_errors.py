"""Worker error classification tests。"""

from __future__ import annotations

from playwright.async_api import Error as AsyncPlaywrightError

from facebook_monitor.core.scan_failures import PAGE_LOAD_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.worker.errors import classify_playwright_exception


def test_classify_playwright_browser_context_closed_as_scheduler_runtime() -> None:
    """Playwright page/context/browser 關閉應走可重試 runtime failure 策略。"""

    reason = classify_playwright_exception(
        AsyncPlaywrightError("Target page, context or browser has been closed")
    )

    assert reason == SCHEDULER_RUNTIME_REASON


def test_classify_playwright_driver_closed_as_scheduler_runtime() -> None:
    """Playwright driver 連線中斷也需要重建整個 browser runtime。"""

    reason = classify_playwright_exception(
        AsyncPlaywrightError("Connection closed while reading from the driver")
    )

    assert reason == SCHEDULER_RUNTIME_REASON


def test_classify_playwright_navigation_error_as_page_load_timeout() -> None:
    """既有 navigation 類錯誤仍維持 page_load_timeout 策略。"""

    reason = classify_playwright_exception(
        AsyncPlaywrightError(
            "Page.evaluate: Execution context was destroyed, "
            "most likely because of a navigation."
        )
    )

    assert reason == PAGE_LOAD_TIMEOUT_REASON
