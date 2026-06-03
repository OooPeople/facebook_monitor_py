"""ntfy sender tests。"""

from __future__ import annotations

from typing import Any

import httpx

from facebook_monitor.notifications.desktop import build_desktop_notification_command
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import build_discord_components_webhook_url
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.discord import truncate_discord_content
from facebook_monitor.notifications.discord_url import validate_discord_webhook_url
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.notifications.ntfy import to_ascii_header_value


def test_send_ntfy_notification_matches_product_encoding(monkeypatch: Any) -> None:
    """ntfy topic 走 URL encode，中文內容放 UTF-8 body，不放進非 ASCII header。"""

    calls: list[dict[str, Any]] = []

    def fake_post(url: str, *, content: bytes, headers: dict[str, str], timeout: int) -> httpx.Response:
        """記錄送出參數，避免測試真的呼叫 ntfy。"""

        calls.append(
            {
                "url": url,
                "content": content,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return httpx.Response(200)

    monkeypatch.setattr(httpx, "post", fake_post)

    result = send_ntfy_notification(
        NtfyConfig(
            topic="中文 topic",
            click_url="https://www.facebook.com/groups/1/posts/2",
        ),
        "Facebook 監視命中: 票券",
        "社團: 測試社團\n內容: 中文內容",
    )

    assert result.ok
    assert calls[0]["url"].endswith("/%E4%B8%AD%E6%96%87%20topic")
    assert calls[0]["content"] == "社團: 測試社團\n內容: 中文內容".encode("utf-8")
    assert calls[0]["headers"]["Content-Type"] == "text/plain; charset=utf-8"
    assert calls[0]["headers"]["Title"] == "Facebook group match"
    assert calls[0]["headers"]["Priority"] == "default"
    assert calls[0]["headers"]["Tags"] == "bell"
    assert calls[0]["headers"]["Click"] == "https://www.facebook.com/groups/1/posts/2"


def test_to_ascii_header_value_keeps_ascii_and_falls_back_for_unicode() -> None:
    """HTTP header 只保留 ASCII，避免 httpx 編碼中文 header 失敗。"""

    assert to_ascii_header_value("Facebook group match", fallback="fallback") == (
        "Facebook group match"
    )
    assert to_ascii_header_value("Facebook 監視命中", fallback="fallback") == "fallback"


def test_send_ntfy_notification_sanitizes_http_exception_message(monkeypatch: Any) -> None:
    """ntfy HTTP 例外訊息不得把 topic / URL 寫進診斷。"""

    def fake_post(
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
        timeout: int,
    ) -> httpx.Response:
        """模擬 httpx 例外內含完整 endpoint。"""

        raise httpx.ConnectError(f"failed to connect {url}")

    monkeypatch.setattr(httpx, "post", fake_post)

    result = send_ntfy_notification(
        NtfyConfig(topic="private-topic"),
        "Facebook group match",
        "message",
    )

    assert not result.ok
    assert result.message == "ntfy_failed:ConnectError"
    assert "private-topic" not in result.message


def test_send_desktop_notification_uses_powershell_balloon_tip(monkeypatch: Any) -> None:
    """desktop sender 參考 hotel_price_watch 的 PowerShell balloon tip 作法。"""

    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> None:
        """記錄命令，避免測試真的發桌面通知。"""

        calls.append(command)

    monkeypatch.setattr("sys.platform", "win32")
    result = send_desktop_notification(
        "Facebook group match",
        "作者: O'Neil",
        command_runner=fake_runner,
    )

    assert result.ok
    assert result.message == "desktop_sent"
    command = calls[0]
    assert command[:3] == ["powershell", "-NoProfile", "-Command"]
    assert "System.Windows.Forms.NotifyIcon" in command[3]
    assert "O''Neil" in command[3]


def test_build_desktop_notification_command_escapes_single_quotes() -> None:
    """PowerShell single-quoted string 會轉義單引號。"""

    command = build_desktop_notification_command(title="A'B", message="C'D")

    assert "$notify.BalloonTipTitle = 'A''B';" in command[3]
    assert "$notify.BalloonTipText = 'C''D';" in command[3]


def test_send_desktop_notification_sanitizes_runner_exception(monkeypatch: Any) -> None:
    """desktop 例外訊息不得把通知內容或 command 寫進診斷。"""

    def failing_runner(_command: list[str]) -> None:
        """模擬 runner 例外內含通知內容。"""

        raise RuntimeError("failed to show private notification body")

    monkeypatch.setattr("sys.platform", "win32")

    result = send_desktop_notification(
        "Facebook group match",
        "private notification body",
        command_runner=failing_runner,
    )

    assert not result.ok
    assert result.message == "desktop_failed:RuntimeError"
    assert "private notification body" not in result.message


def test_send_discord_notification_matches_webhook_payload(
    monkeypatch: Any,
) -> None:
    """Discord sender 送 JSON content，並保留產品長度上限。"""

    calls: list[dict[str, Any]] = []

    def fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> httpx.Response:
        """記錄送出參數，避免測試真的呼叫 Discord。"""

        calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return httpx.Response(204)

    monkeypatch.setattr(httpx, "post", fake_post)
    result = send_discord_notification(
        DiscordConfig(webhook_url="https://discord.com/api/webhooks/1234567890/token_value"),
        "Facebook group match",
        "社團: 測試社團",
    )

    assert result.ok
    assert result.status_code == 204
    assert result.message == "discord_sent"
    assert calls[0]["url"] == (
        "https://discord.com/api/webhooks/1234567890/token_value?with_components=true"
    )
    payload = calls[0]["json"]
    assert payload["username"] == "facebook_monitor_py"
    assert payload["allowed_mentions"] == {"parse": []}
    assert payload["flags"] == 32772
    assert "content" not in payload
    assert "embeds" not in payload
    assert payload["components"] == [
        {
            "type": 10,
            "content": "## Facebook group match\n社團: 測試社團",
        },
        {
            "type": 14,
            "divider": True,
            "spacing": 2,
        },
    ]
    assert calls[0]["headers"]["Accept"] == "*/*"


def test_send_discord_notification_retries_short_rate_limit(
    monkeypatch: Any,
) -> None:
    """Discord 429 有短 Retry-After 時，sender 會等待後重送一次。"""

    calls: list[int] = []
    sleeps: list[float] = []

    def fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> httpx.Response:
        """第一次回 rate limit，第二次成功。"""

        calls.append(len(calls) + 1)
        if len(calls) == 1:
            return httpx.Response(
                429,
                json={"message": "You are being rate limited.", "retry_after": 0.25},
            )
        return httpx.Response(204)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("facebook_monitor.notifications.discord.time.sleep", sleeps.append)

    result = send_discord_notification(
        DiscordConfig(webhook_url="https://discord.com/api/webhooks/1234567890/token_value"),
        "Facebook group match",
        "社團: 測試社團",
    )

    assert result.ok
    assert result.status_code == 204
    assert result.message == "discord_sent"
    assert calls == [1, 2]
    assert sleeps == [0.25]


def test_send_discord_notification_reports_rate_limit_details(
    monkeypatch: Any,
) -> None:
    """Discord 429 超過等待上限時，訊息保留 retry-after 與 Discord body 摘要。"""

    def fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> httpx.Response:
        """回傳不可立即等待的 Discord rate limit。"""

        return httpx.Response(
            429,
            headers={"Retry-After": "30"},
            json={
                "message": "You are being rate limited.",
                "retry_after": 30,
                "global": False,
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    result = send_discord_notification(
        DiscordConfig(webhook_url="https://discord.com/api/webhooks/1234567890/token_value"),
        "Facebook group match",
        "社團: 測試社團",
    )

    assert not result.ok
    assert result.status_code == 429
    assert result.message == (
        "discord_failed:429 retry_after=30s global=false "
        "message=You are being rate limited."
    )


def test_send_discord_notification_sanitizes_http_exception_message(
    monkeypatch: Any,
) -> None:
    """Discord HTTP 例外訊息不得把 webhook URL 寫進診斷。"""

    def fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> httpx.Response:
        """模擬 httpx 例外內含完整 webhook。"""

        raise httpx.ConnectError(f"failed to connect {url}")

    monkeypatch.setattr(httpx, "post", fake_post)

    result = send_discord_notification(
        DiscordConfig(webhook_url="https://discord.com/api/webhooks/1234567890/private-token"),
        "Facebook group match",
        "message",
    )

    assert not result.ok
    assert result.message == "discord_failed:ConnectError"
    assert "private-token" not in result.message


def test_truncate_discord_content_uses_conservative_limit() -> None:
    """Discord content 會限制長度，避免超過 webhook 上限。"""

    content = truncate_discord_content("x" * 2000, limit=20)

    assert content == "x" * 17 + "..."


def test_build_discord_components_webhook_url_merges_query() -> None:
    """Discord Components V2 query flag 會與既有 query 合併。"""

    url = build_discord_components_webhook_url(
        "https://discord.com/api/webhooks/1234567890/token_value?wait=true"
    )

    assert url == (
        "https://discord.com/api/webhooks/1234567890/token_value?wait=true&with_components=true"
    )


def test_validate_discord_webhook_url_rejects_non_discord_hosts() -> None:
    """Discord webhook URL 不可退化成 generic webhook。"""

    for value in (
        "http://discord.com/api/webhooks/123/token",
        "https://example.com/api/webhooks/123/token",
        "https://discord.com.evil.test/api/webhooks/123/token",
        "https://127.0.0.1/api/webhooks/123/token",
        "https://user:pass@discord.com/api/webhooks/123/token",
        "https://discord.com:8443/api/webhooks/123/token",
        "https://discord.com/api/not-webhooks/123/token",
        "https://discord.com/api/webhooks/not-numeric/token",
        "https://discord.com/api/webhooks/123/token?wait=true",
    ):
        try:
            validate_discord_webhook_url(value)
        except ValueError:
            continue
        raise AssertionError(f"expected invalid Discord webhook URL: {value}")


def test_send_discord_notification_does_not_post_invalid_webhook(
    monkeypatch: Any,
) -> None:
    """舊資料或污染資料中的 invalid webhook 不可觸發 HTTP POST。"""

    calls: list[str] = []

    def fake_post(*args: object, **kwargs: object) -> httpx.Response:
        calls.append("called")
        return httpx.Response(204)

    monkeypatch.setattr(httpx, "post", fake_post)

    result = send_discord_notification(
        DiscordConfig(webhook_url="https://127.0.0.1/api/webhooks/123/token"),
        "Facebook group match",
        "message",
    )

    assert not result.ok
    assert result.message == "discord_webhook_invalid"
    assert calls == []
