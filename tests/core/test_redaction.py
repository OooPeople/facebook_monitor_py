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
    assert "mode=test" in text
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


def test_redact_sensitive_text_hides_authorization_bearer_token() -> None:
    """Authorization Bearer header 不可留下 credential。"""

    text = redact_sensitive_text("Authorization: Bearer abc.def.ghi")

    assert "abc.def.ghi" not in text
    assert "Bearer" in text
    assert "[已隱藏]" in text


def test_redact_sensitive_text_hides_authorization_basic_token() -> None:
    """Authorization Basic header 不可留下 credential。"""

    text = redact_sensitive_text("Authorization: Basic dXNlcjpwYXNz")

    assert "dXNlcjpwYXNz" not in text
    assert "Basic" in text
    assert "[已隱藏]" in text


def test_redact_sensitive_text_hides_lowercase_authorization_assignment() -> None:
    """authorization=Bearer token 形式也不可留下 credential。"""

    text = redact_sensitive_text("authorization=Bearer secret-token")

    assert "secret-token" not in text
    assert "Bearer" in text
    assert "[已隱藏]" in text


def test_redact_sensitive_text_hides_cookie_headers() -> None:
    """Cookie / Set-Cookie header value 應整段遮罩。"""

    text = redact_sensitive_text(
        "Cookie: c_user=123; xs=secret\nSet-Cookie: sessionid=secret; HttpOnly"
    )

    assert "c_user=123" not in text
    assert "xs=secret" not in text
    assert "sessionid=secret" not in text
    assert text.count("[已隱藏]") == 2


def test_redact_sensitive_text_preserves_query_after_authorization_param() -> None:
    """URL authorization query 被遮罩時不可吃掉後續非 secret 參數。"""

    text = redact_sensitive_text("https://example.test/cb?authorization=secret&mode=test")

    assert "secret" not in text
    assert "mode=test" in text
