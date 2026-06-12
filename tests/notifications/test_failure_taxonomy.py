"""Notification failure taxonomy tests。"""

from __future__ import annotations

import pytest

from facebook_monitor.notifications.failure_taxonomy import classify_notification_failure


@pytest.mark.parametrize(
    ("channel", "last_error", "expected"),
    [
        ("ntfy", "ntfy topic is empty", "missing_endpoint"),
        ("discord", "discord_webhook_invalid", "invalid_endpoint"),
        (
            "discord",
            "discord_failed:429 retry_after=30s message=You are being rate limited.",
            "rate_limited",
        ),
        ("ntfy", "ntfy_failed:500", "http_status"),
        ("desktop", "desktop_failed:macos_permission_denied", "permission_denied"),
        ("desktop", "desktop_failed: unsupported platform", "unsupported_platform"),
        ("ntfy", "ntfy_dispatch_failed:RuntimeError", "sender_exception"),
        ("discord", "failed_result", "sender_result_failed"),
        ("discord", "unexpected", "unknown"),
    ],
)
def test_classify_notification_failure(
    channel: str,
    last_error: str,
    expected: str,
) -> None:
    """常見 sender 失敗訊息會映射成穩定、可行動分類。"""

    assert (
        classify_notification_failure(
            channel=channel,
            failure_reason="",
            last_error=last_error,
        )
        == expected
    )
