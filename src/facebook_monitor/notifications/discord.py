"""Discord webhook notification sender。"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


DISCORD_CONTENT_LIMIT = 1900


@dataclass(frozen=True)
class DiscordConfig:
    """保存 Discord webhook 發送設定。"""

    webhook_url: str = ""
    username: str = "facebook_monitor_py"


@dataclass(frozen=True)
class DiscordResult:
    """保存 Discord webhook 發送結果。"""

    ok: bool
    status_code: int | None
    message: str


def send_discord_notification(
    config: DiscordConfig,
    title: str,
    message: str,
) -> DiscordResult:
    """送出一則 Discord webhook 通知。"""

    webhook_url = config.webhook_url.strip()
    if not webhook_url:
        return DiscordResult(ok=False, status_code=None, message="discord_skipped")

    content = truncate_discord_content("\n".join(part for part in (title, message) if part))
    payload = {
        "username": config.username,
        "content": content,
    }
    try:
        response = httpx.post(
            webhook_url,
            json=payload,
            headers={"Accept": "*/*"},
            timeout=15,
        )
        if 200 <= response.status_code < 300:
            return DiscordResult(
                ok=True,
                status_code=response.status_code,
                message="discord_sent",
            )
        return DiscordResult(
            ok=False,
            status_code=response.status_code,
            message=f"discord_failed:{response.status_code}",
        )
    except httpx.HTTPError as exc:
        return DiscordResult(ok=False, status_code=None, message=f"discord_failed: {exc}")
    except Exception as exc:
        return DiscordResult(ok=False, status_code=None, message=f"discord_failed: {exc}")


def truncate_discord_content(value: str, limit: int = DISCORD_CONTENT_LIMIT) -> str:
    """限制 Discord content 長度，對齊 userscript 保守上限。"""

    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."
