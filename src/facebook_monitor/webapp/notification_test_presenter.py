"""Target 測試通知結果的 Web UI 呈現策略。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NotRequired
from typing import TypedDict

from facebook_monitor.core.user_messages import format_notification_event_message


AUTO_DISMISS_TIMEOUT_MS = 3500
ATTENTION_TIMEOUT_MS = 5000
STICKY_TIMEOUT_MS = 0

_SUCCESS_MESSAGES = {
    "desktop_sent",
    "discord_sent",
    "ntfy_sent",
    "retry_sent",
    "sent",
}
_STICKY_EXACT_MESSAGES = {
    "desktop_failed: unsupported platform",
    "desktop_failed:macos_alert_disabled",
    "desktop_failed:macos_authorization_error",
    "desktop_failed:macos_permission_denied",
    "desktop_failed:macos_timeout",
    "discord_skipped",
    "discord_webhook_invalid",
    "notification_skipped: no channel enabled",
    "ntfy topic is empty",
    "ntfy_skipped",
}
_STICKY_HTTP_STATUS_CODES = {400, 401, 403, 404, 410}


class NotificationTestResultPayload(TypedDict):
    """單一測試通知 channel result 的 JSON payload。"""

    channel: str
    code: str
    message: str
    severity: str
    sticky: bool


class NotificationTestFeedbackPayload(TypedDict):
    """測試通知 feedback response 的 JSON payload。"""

    ok: bool
    all_ok: bool
    message: str
    results: list[str]
    result_details: list[NotificationTestResultPayload]
    sticky: bool
    timeout_ms: int
    tone: str
    error: NotRequired[str]


@dataclass(frozen=True)
class NotificationTestResultView:
    """單一測試通知 channel result 的 UI 呈現資料。"""

    channel: str
    code: str
    message: str
    severity: str
    sticky: bool

    def to_payload(self) -> NotificationTestResultPayload:
        """轉成 JSON response payload。"""

        return {
            "channel": self.channel,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "sticky": self.sticky,
        }


@dataclass(frozen=True)
class NotificationTestFeedback:
    """整次測試通知結果的 UI 呈現資料。"""

    message: str
    results: tuple[NotificationTestResultView, ...]
    tone: str
    sticky: bool
    timeout_ms: int
    ok: bool = True
    error: str | None = None

    @property
    def localized_results(self) -> list[str]:
        """回傳舊 JSON contract 使用的本地化結果列表。"""

        return [result.message for result in self.results]

    @property
    def all_ok(self) -> bool:
        """回傳所有 channel 是否都成功。"""

        return self.ok and all(result.severity == "success" for result in self.results)

    def to_payload(self) -> NotificationTestFeedbackPayload:
        """轉成 JSON response payload，保留舊欄位並追加 presentation metadata。"""

        payload: NotificationTestFeedbackPayload = {
            "ok": self.ok,
            "all_ok": self.all_ok,
            "message": self.message,
            "results": self.localized_results,
            "result_details": [result.to_payload() for result in self.results],
            "sticky": self.sticky,
            "timeout_ms": self.timeout_ms,
            "tone": self.tone,
        }
        if self.error:
            payload["error"] = self.error
        return payload


def build_notification_test_feedback(
    raw_results: list[str] | tuple[str, ...],
) -> NotificationTestFeedback:
    """把 raw notification result codes 轉成測試通知 UI 呈現資料。"""

    result_views = tuple(
        build_notification_test_result_view(result) for result in raw_results
    )
    localized_results = [result.message for result in result_views]
    sticky = any(result.sticky for result in result_views)
    all_ok = all(result.severity == "success" for result in result_views)
    tone = "success" if all_ok else "warning"
    timeout_ms = (
        STICKY_TIMEOUT_MS
        if sticky
        else AUTO_DISMISS_TIMEOUT_MS
        if all_ok
        else ATTENTION_TIMEOUT_MS
    )
    return NotificationTestFeedback(
        message="測試通知結果：" + " / ".join(localized_results),
        results=result_views,
        tone=tone,
        sticky=sticky,
        timeout_ms=timeout_ms,
    )


def build_notification_test_error_feedback(
    raw_result: str,
    *,
    error_message: str,
    sticky: bool | None = None,
) -> NotificationTestFeedback:
    """建立 route-level 測試通知錯誤的 UI 呈現資料。"""

    result = build_notification_test_result_view(
        raw_result,
        message=error_message,
        sticky=sticky,
    )
    timeout_ms = STICKY_TIMEOUT_MS if result.sticky else ATTENTION_TIMEOUT_MS
    return NotificationTestFeedback(
        message=error_message,
        results=(result,),
        tone="warning",
        sticky=result.sticky,
        timeout_ms=timeout_ms,
        ok=False,
        error=error_message,
    )


def build_notification_test_result_view(
    raw_result: str,
    *,
    message: str | None = None,
    sticky: bool | None = None,
) -> NotificationTestResultView:
    """建立單一 raw result 的 UI 呈現資料。"""

    code = str(raw_result or "").strip()
    result_sticky = (
        notification_test_result_is_sticky(code) if sticky is None else bool(sticky)
    )
    return NotificationTestResultView(
        channel=_notification_test_channel(code),
        code=code,
        message=message or format_notification_event_message(code),
        severity="success" if _notification_test_result_is_success(code) else "warning",
        sticky=result_sticky,
    )


def notification_test_result_is_sticky(raw_result: str) -> bool:
    """判斷測試通知結果是否應常駐到下一次測試覆蓋。"""

    code = str(raw_result or "").strip()
    if code in _STICKY_EXACT_MESSAGES:
        return True
    if code.startswith("discord_webhook_"):
        return True
    if code.startswith("ntfy topic "):
        return True
    status_code = _notification_http_status_code(code)
    return status_code in _STICKY_HTTP_STATUS_CODES


def _notification_test_result_is_success(raw_result: str) -> bool:
    """判斷測試通知單一 channel 是否成功。"""

    return str(raw_result or "").strip() in _SUCCESS_MESSAGES


def _notification_http_status_code(raw_result: str) -> int | None:
    """解析 notification sender result 中的 HTTP status code。"""

    code = str(raw_result or "").strip()
    prefixes = (
        "unexpected status code:",
        "ntfy_failed: unexpected status code:",
        "discord_failed:",
    )
    for prefix in prefixes:
        if not code.startswith(prefix):
            continue
        remainder = code.removeprefix(prefix).strip()
        token = remainder.split(maxsplit=1)[0] if remainder else ""
        try:
            return int(token)
        except ValueError:
            return None
    return None


def _notification_test_channel(raw_result: str) -> str:
    """由 raw result 推定測試通知 channel，供 JSON metadata 使用。"""

    code = str(raw_result or "").strip()
    if code.startswith("desktop_"):
        return "desktop"
    if code.startswith("ntfy") or code.startswith("unexpected status code:"):
        return "ntfy"
    if code.startswith("discord") or code.startswith("discord_webhook_"):
        return "discord"
    return "notification"
