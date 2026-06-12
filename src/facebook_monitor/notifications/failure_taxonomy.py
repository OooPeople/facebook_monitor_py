"""Notification failure taxonomy。

職責：從既有 failure_reason / last_error 衍生穩定診斷分類，不改 DB schema、
不改 retry 行為，也不取代原始錯誤字串。
"""

from __future__ import annotations


UNKNOWN_NOTIFICATION_FAILURE_CATEGORY = "unknown"
_SUBSTRING_RULES = (
    ("missing_endpoint", ("topic is empty",)),
    ("invalid_endpoint", ("webhook_invalid", "invalid webhook")),
    ("rate_limited", ("429", "rate limit", "rate_limited")),
    ("permission_denied", ("permission_denied", "authorization_error")),
    ("unsupported_platform", ("unsupported platform",)),
    ("sender_exception", ("dispatch_failed:",)),
)
_EXACT_RULES = {
    "failed_result": "sender_result_failed",
    "first_down": "sender_result_failed",
    "previous_down": "sender_result_failed",
}


def classify_notification_failure(
    *,
    channel: str,
    failure_reason: str = "",
    last_error: str = "",
) -> str:
    """回傳 notification failed row 的穩定診斷分類。"""

    normalized_channel = str(channel or "").strip().lower()
    text = " ".join(
        part.strip().lower()
        for part in (failure_reason, last_error)
        if str(part or "").strip()
    )
    if not text:
        return UNKNOWN_NOTIFICATION_FAILURE_CATEGORY
    if text.endswith("_skipped"):
        return "missing_endpoint"
    for category, needles in _SUBSTRING_RULES:
        if _contains_any(text, needles):
            return category
    if _looks_like_http_status_failure(text, channel=normalized_channel):
        return "http_status"
    if "failed:" in text:
        return "sender_exception"
    if text in _EXACT_RULES:
        return _EXACT_RULES[text]
    return UNKNOWN_NOTIFICATION_FAILURE_CATEGORY


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    """回傳文字是否包含任一分類關鍵片段。"""

    return any(needle in text for needle in needles)


def _looks_like_http_status_failure(text: str, *, channel: str) -> bool:
    """辨識 sender message 中的 HTTP status failure。"""

    prefixes = tuple(
        prefix
        for prefix in (
            f"{channel}_failed:" if channel else "",
            "ntfy_failed:",
            "discord_failed:",
        )
        if prefix
    )
    for prefix in prefixes:
        suffix = text.removeprefix(prefix)
        if suffix != text and suffix[:3].isdigit():
            return True
    return False
