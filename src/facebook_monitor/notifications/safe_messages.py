"""通知診斷訊息安全化工具。"""

from __future__ import annotations


def safe_exception_message(prefix: str, exc: BaseException) -> str:
    """回傳不包含 endpoint / token 的例外摘要。"""

    exception_name = type(exc).__name__ or "Exception"
    return f"{prefix}:{exception_name}"
