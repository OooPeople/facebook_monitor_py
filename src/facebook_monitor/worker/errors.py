"""Worker 共用錯誤與例外分類。

職責：保存 posts/comments、one-shot、resident 主路徑與 fallback/debug 共用的
失敗分類，避免共用錯誤型別被放在任一 target-specific pipeline 造成誤導。
"""

from __future__ import annotations

from facebook_monitor.core.scan_failures import EXTRACTOR_RUNTIME_REASON
from facebook_monitor.core.scan_failures import PAGE_LOAD_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import PROFILE_LOCKED_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON


class WorkerFailure(RuntimeError):
    """保存 worker 可記錄到 scan run 的失敗分類。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def classify_playwright_exception(error: Exception) -> str:
    """將 Playwright 例外轉成 worker 失敗分類。"""

    message = str(error).lower()
    if "user data directory is already in use" in message or "processsingleton" in message:
        return PROFILE_LOCKED_REASON
    if _is_playwright_runtime_closed_message(message):
        return SCHEDULER_RUNTIME_REASON
    if "timeout" in message and _is_body_locator_message(message):
        return PAGE_LOAD_TIMEOUT_REASON
    if "net::" in message or "navigation" in message:
        return PAGE_LOAD_TIMEOUT_REASON
    if _is_extractor_runtime_message(message):
        return EXTRACTOR_RUNTIME_REASON
    if "timeout" in message:
        return PAGE_LOAD_TIMEOUT_REASON
    return UNKNOWN_REASON


def classify_wrapped_playwright_exception(error: Exception) -> str:
    """辨識被一般 Exception 包住的 Playwright 失敗訊息。"""

    message = str(error).lower()
    if _is_playwright_runtime_closed_message(
        message
    ) or _is_playwright_api_error_message(message):
        return classify_playwright_exception(error)
    return UNKNOWN_REASON


def _is_playwright_api_error_message(message: str) -> bool:
    """判斷一般 Exception 訊息是否仍帶有 Playwright API 來源。"""

    tokens = (
        "page.evaluate",
        "page.goto",
        "page.reload",
        "page.wait_for",
        "locator.",
        "browsercontext.",
        "browser.new",
        "execution context was destroyed",
    )
    return any(token in message for token in tokens)


def _is_extractor_runtime_message(message: str) -> bool:
    """判斷 Playwright evaluate / selector 類錯誤是否來自 DOM extractor。"""

    tokens = (
        "page.evaluate",
        "locator",
        "selector",
        "queryselector",
        "query selector",
        "evaluation failed",
        "execution context was destroyed",
    )
    return any(token in message for token in tokens)


def _is_body_locator_message(message: str) -> bool:
    """判斷錯誤是否來自登入/session guard 的 body locator probe。"""

    tokens = ('locator("body")', "locator('body')")
    return any(token in message for token in tokens)


def _is_playwright_runtime_closed_message(message: str) -> bool:
    """判斷 Playwright page/context/browser 已關閉的可恢復 runtime 例外。"""

    closed_tokens = (
        "connection closed while reading from the driver",
        "target page, context or browser has been closed",
        "page, context or browser has been closed",
        "browser has been closed",
        "context has been closed",
        "page has been closed",
        "target closed",
    )
    return any(token in message for token in closed_tokens)
