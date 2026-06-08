"""Target 測試通知 presenter tests。"""

from __future__ import annotations

from facebook_monitor.webapp.notification_test_presenter import (
    ATTENTION_TIMEOUT_MS,
    AUTO_DISMISS_TIMEOUT_MS,
    STICKY_TIMEOUT_MS,
    build_notification_test_error_feedback,
    build_notification_test_feedback,
)


def test_notification_test_feedback_auto_dismisses_success_results() -> None:
    """所有 channel 成功時，測試結果維持短時間自動消失。"""

    feedback = build_notification_test_feedback(
        ["desktop_sent", "ntfy_sent", "discord_sent"]
    )
    payload = feedback.to_payload()

    assert payload["ok"] is True
    assert payload["all_ok"] is True
    assert payload["sticky"] is False
    assert payload["timeout_ms"] == AUTO_DISMISS_TIMEOUT_MS
    assert payload["tone"] == "success"
    assert payload["results"] == [
        "桌面通知已送出",
        "ntfy 通知已送出",
        "Discord 通知已送出",
    ]
    assert payload["result_details"] == [
        {
            "channel": "desktop",
            "code": "desktop_sent",
            "message": "桌面通知已送出",
            "severity": "success",
            "sticky": False,
        },
        {
            "channel": "ntfy",
            "code": "ntfy_sent",
            "message": "ntfy 通知已送出",
            "severity": "success",
            "sticky": False,
        },
        {
            "channel": "discord",
            "code": "discord_sent",
            "message": "Discord 通知已送出",
            "severity": "success",
            "sticky": False,
        },
    ]


def test_notification_test_feedback_keeps_actionable_setup_failures_visible() -> None:
    """需要使用者調整設定或權限的結果會常駐到下一次測試覆蓋。"""

    feedback = build_notification_test_feedback(
        ["desktop_failed:macos_permission_denied"]
    )
    payload = feedback.to_payload()

    assert payload["all_ok"] is False
    assert payload["sticky"] is True
    assert payload["timeout_ms"] == STICKY_TIMEOUT_MS
    assert payload["tone"] == "warning"
    assert "系統設定" in str(payload["message"])
    assert "允許通知" in str(payload["message"])
    assert payload["result_details"] == [
        {
            "channel": "desktop",
            "code": "desktop_failed:macos_permission_denied",
            "message": (
                "macOS 未允許 Facebook Monitor 發送通知。"
                "請到「系統設定」→「通知」找到 Facebook Monitor，"
                "開啟「允許通知」後再重新測試。"
            ),
            "severity": "warning",
            "sticky": True,
        }
    ]


def test_notification_test_feedback_sticks_all_channel_configuration_failures() -> None:
    """常駐策略不可只套用桌面通知，ntfy / Discord 設定問題也要保留。"""

    feedback = build_notification_test_feedback(
        ["ntfy topic is empty", "discord_webhook_invalid"]
    )
    payload = feedback.to_payload()

    assert payload["all_ok"] is False
    assert payload["sticky"] is True
    assert payload["timeout_ms"] == STICKY_TIMEOUT_MS
    assert payload["results"] == [
        "ntfy 主題未設定",
        "Discord webhook URL 格式不正確",
    ]
    assert payload["result_details"] == [
        {
            "channel": "ntfy",
            "code": "ntfy topic is empty",
            "message": "ntfy 主題未設定",
            "severity": "warning",
            "sticky": True,
        },
        {
            "channel": "discord",
            "code": "discord_webhook_invalid",
            "message": "Discord webhook URL 格式不正確",
            "severity": "warning",
            "sticky": True,
        },
    ]


def test_notification_test_feedback_sticks_actionable_http_status_failures() -> None:
    """HTTP 權限、找不到與停用等可行動錯誤需常駐顯示。"""

    feedback = build_notification_test_feedback(
        [
            "ntfy_failed: unexpected status code: 401",
            "discord_failed:404",
        ]
    )
    payload = feedback.to_payload()

    assert payload["all_ok"] is False
    assert payload["sticky"] is True
    assert payload["timeout_ms"] == STICKY_TIMEOUT_MS
    assert [item["sticky"] for item in payload["result_details"]] == [True, True]


def test_notification_test_feedback_extends_rate_limit_without_sticking() -> None:
    """429 限流可稍後重試，需延長顯示但不常駐。"""

    feedback = build_notification_test_feedback(
        ["discord_failed:429 retry_after=30s global=false"]
    )
    payload = feedback.to_payload()

    assert payload["all_ok"] is False
    assert payload["sticky"] is False
    assert payload["timeout_ms"] == ATTENTION_TIMEOUT_MS
    assert payload["result_details"] == [
        {
            "channel": "discord",
            "code": "discord_failed:429 retry_after=30s global=false",
            "message": "Discord 發送受限，稍後可重試",
            "severity": "warning",
            "sticky": False,
        }
    ]


def test_notification_test_feedback_auto_dismisses_transient_failures_more_slowly() -> None:
    """連線例外等短暫失敗不常駐，但比成功訊息多顯示一段時間。"""

    feedback = build_notification_test_feedback(["ntfy_failed:ConnectError"])
    payload = feedback.to_payload()

    assert payload["all_ok"] is False
    assert payload["sticky"] is False
    assert payload["timeout_ms"] == ATTENTION_TIMEOUT_MS
    assert payload["tone"] == "warning"


def test_notification_test_error_feedback_uses_same_lifecycle_contract() -> None:
    """route-level 表單錯誤也走同一份 sticky / timeout metadata。"""

    feedback = build_notification_test_error_feedback(
        "discord_webhook_url_invalid",
        error_message="測試通知失敗: Discord webhook URL 格式不正確",
    )
    payload = feedback.to_payload()

    assert payload["ok"] is False
    assert payload["all_ok"] is False
    assert payload["sticky"] is True
    assert payload["timeout_ms"] == STICKY_TIMEOUT_MS
    assert payload["tone"] == "warning"
    assert payload["error"] == "測試通知失敗: Discord webhook URL 格式不正確"
    assert payload["result_details"] == [
        {
            "channel": "discord",
            "code": "discord_webhook_url_invalid",
            "message": "測試通知失敗: Discord webhook URL 格式不正確",
            "severity": "warning",
            "sticky": True,
        }
    ]
