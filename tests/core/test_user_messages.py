"""使用者可見訊息格式化測試。"""

from __future__ import annotations

from facebook_monitor.core.user_messages import format_failure_message
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.core.user_messages import format_notification_event_message
from facebook_monitor.core.user_messages import format_update_reason_message


def test_page_evaluate_navigation_error_is_localized() -> None:
    """Playwright navigation raw exception 不應直接顯示在 UI 訊息。"""

    raw = (
        "page_load_timeout: Page.evaluate: Execution context was destroyed, "
        "most likely because of a navigation."
    )

    message = format_failure_message_text(raw)

    assert "頁面載入逾時" in message
    assert "頁面載入、重新導向或重新整理時中斷" in message
    assert "Page.evaluate" not in message
    assert "Execution context was destroyed" not in message


def test_failure_message_uses_reason_specific_detail() -> None:
    """worker 寫入失敗訊息時不沿用 raw exception detail。"""

    message = format_failure_message(
        "page_load_timeout",
        "Page.evaluate: Execution context was destroyed",
    )

    assert message.startswith("頁面載入逾時：")
    assert "Page.evaluate" not in message


def test_generic_raw_english_failure_message_is_not_exposed() -> None:
    """未知英文 exception 在使用者可見訊息中只能顯示中文摘要。"""

    message = format_failure_message_text("Unexpected browser driver failure")

    assert message == "發生未分類錯誤，請查看 log 或稍後重試。"
    assert "Unexpected" not in message


def test_mixed_failure_message_redacts_secret_values() -> None:
    """含中文的 exception detail 仍不得顯示 webhook token 或使用者目錄。"""

    message = format_failure_message_text(
        r"操作失敗：https://discord.com/api/webhooks/123456/very-secret-token "
        r"C:\Users\alice\facebook_monitor_data\logs\error.log"
    )

    assert "very-secret-token" not in message
    assert "alice" not in message
    assert "[已隱藏]" in message
    assert "%USERPROFILE%" in message


def test_notification_event_message_is_localized() -> None:
    """通知事件內部代碼顯示時必須轉成中文摘要。"""

    assert format_notification_event_message("discord_sent") == "Discord 通知已送出"
    assert "系統設定" in format_notification_event_message(
        "desktop_failed:macos_permission_denied"
    )
    assert "Facebook Monitor" in format_notification_event_message(
        "desktop_failed:macos_permission_denied"
    )
    assert (
        format_notification_event_message("notification_test_failed:RuntimeError")
        == "通知測試發生錯誤"
    )
    assert (
        format_notification_event_message("ntfy_failed: unexpected status code: 401")
        == "ntfy 發送失敗，狀態碼 401"
    )


def test_update_platform_unsupported_reason_is_localized() -> None:
    """更新平台不支援 reason 在 UI 顯示時必須是中文。"""

    assert (
        format_update_reason_message("platform_unsupported")
        == "目前平台沒有對應的更新檔"
    )
