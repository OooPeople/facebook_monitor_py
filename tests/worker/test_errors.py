"""Worker error classification tests。"""

from __future__ import annotations

from playwright.async_api import Error as AsyncPlaywrightError

from facebook_monitor.core.scan_failures import EXTRACTOR_RUNTIME_REASON
from facebook_monitor.core.scan_failures import PAGE_LOAD_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.worker.errors import classify_playwright_exception
from facebook_monitor.worker.errors import classify_wrapped_playwright_exception


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


def test_classify_wrapped_playwright_driver_closed_as_scheduler_runtime() -> None:
    """被一般 Exception 包住的 driver 斷線仍應歸 browser runtime failure。"""

    messages = (
        "Connection closed while reading from the driver",
        "Page.evaluate: Connection closed while reading from the driver",
        "BrowserContext.new_page: Connection closed while reading from the driver",
    )

    for message in messages:
        reason = classify_wrapped_playwright_exception(Exception(message))

        assert reason == SCHEDULER_RUNTIME_REASON


def test_classify_wrapped_non_playwright_timeout_stays_unknown() -> None:
    """一般內部 timeout 不應因字面文字被誤歸 Playwright page load。"""

    reason = classify_wrapped_playwright_exception(Exception("internal timeout"))

    assert reason == UNKNOWN_REASON
    assert (
        classify_wrapped_playwright_exception(Exception("page.render timeout"))
        == UNKNOWN_REASON
    )


def test_classify_playwright_navigation_error_as_page_load_timeout() -> None:
    """既有 navigation 類錯誤仍維持 page_load_timeout 策略。"""

    reason = classify_playwright_exception(
        AsyncPlaywrightError(
            "Page.evaluate: Execution context was destroyed, "
            "most likely because of a navigation."
        )
    )

    assert reason == PAGE_LOAD_TIMEOUT_REASON


def test_classify_playwright_evaluate_error_as_extractor_runtime() -> None:
    """DOM script / selector regression 應比 unknown 更可診斷。"""

    reason = classify_playwright_exception(
        AsyncPlaywrightError("Page.evaluate: TypeError: Cannot read properties of null")
    )

    assert reason == EXTRACTOR_RUNTIME_REASON


def test_classify_playwright_evaluate_timeout_as_extractor_runtime() -> None:
    """evaluate timeout 應保留 DOM extractor 診斷，而不是誤報 page load。"""

    reason = classify_playwright_exception(
        AsyncPlaywrightError("Page.evaluate: Timeout 30000ms exceeded")
    )

    assert reason == EXTRACTOR_RUNTIME_REASON


def test_classify_playwright_body_locator_timeout_as_page_load_timeout() -> None:
    """body locator timeout 來自登入/session guard，仍應歸 page load 類。"""

    reason = classify_playwright_exception(
        AsyncPlaywrightError(
            'Locator.inner_text: Timeout 10000ms exceeded\n'
            'Call log:\n  - waiting for locator("body")'
        )
    )

    assert reason == PAGE_LOAD_TIMEOUT_REASON


def test_classify_playwright_selector_error_as_extractor_runtime() -> None:
    """selector / locator 類 Playwright 錯誤保留 extractor runtime reason。"""

    reason = classify_playwright_exception(
        AsyncPlaywrightError("Locator.click: Error: strict mode violation")
    )

    assert reason == EXTRACTOR_RUNTIME_REASON
