"""Worker 共用錯誤與例外分類。

職責：保存 posts/comments、one-shot、resident 主路徑與 fallback/debug 共用的
失敗分類，避免共用錯誤型別被放在任一 target-specific pipeline 造成誤導。
"""

from __future__ import annotations


class WorkerFailure(RuntimeError):
    """保存 worker 可記錄到 scan run 的失敗分類。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def classify_playwright_exception(error: Exception) -> str:
    """將 Playwright 例外轉成 worker 失敗分類。"""

    message = str(error).lower()
    if "user data directory is already in use" in message or "processsingleton" in message:
        return "profile_locked"
    if "timeout" in message:
        return "page_load_timeout"
    if "net::" in message or "navigation" in message:
        return "page_load_timeout"
    return "unknown"
