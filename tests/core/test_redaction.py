"""使用者可見文字敏感資訊遮罩測試。"""

from __future__ import annotations

from facebook_monitor.core.redaction import redact_sensitive_text


def test_redact_sensitive_text_hides_discord_webhook_token() -> None:
    """Discord webhook URL 不可把 token 顯示到 UI 或 log 摘要。"""

    text = redact_sensitive_text(
        "通知失敗：https://discord.com/api/webhooks/123456/very-secret-token"
    )

    assert "very-secret-token" not in text
    assert "123456" in text
    assert "[已隱藏]" in text


def test_redact_sensitive_text_hides_credentials_and_home_paths() -> None:
    """一般 URL credential、secret query 與本機使用者目錄都要遮罩。"""

    text = redact_sensitive_text(
        "錯誤 https://user:pass@example.test/hook?token=secret&mode=test "
        r"C:\Users\alice\facebook_monitor_data\logs\error.log "
        "/Users/alice/facebook_monitor_data/logs/error.log"
    )

    assert "user:pass" not in text
    assert "secret" not in text
    assert "alice" not in text
    assert "%USERPROFILE%" in text
    assert "~" in text


def test_redact_sensitive_text_hides_home_paths_with_spaces() -> None:
    """使用者名稱含空白時，support bundle path redaction 仍不可漏出姓名片段。"""

    text = redact_sensitive_text(
        r"C:\Users\John Doe\facebook_monitor_data\logs\error.log "
        "/Users/John Doe/facebook_monitor_data/logs/error.log"
    )

    assert "John" not in text
    assert "Doe" not in text
    assert r"%USERPROFILE%\facebook_monitor_data\logs\error.log" in text
    assert "~/facebook_monitor_data/logs/error.log" in text
