"""Discord webhook URL 驗證。

職責：讓 Discord 通道只接受真正的 Discord webhook endpoint，避免
notification sender 被設定成任意 HTTP POST client。
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit
from urllib.parse import urlunsplit


ALLOWED_DISCORD_WEBHOOK_HOSTS = frozenset(
    {
        "discord.com",
        "ptb.discord.com",
        "canary.discord.com",
        "discordapp.com",
    }
)
DISCORD_WEBHOOK_PATH_RE = re.compile(r"^/api/webhooks/[0-9]+/[A-Za-z0-9._~-]+$")
MAX_DISCORD_WEBHOOK_URL_LENGTH = 2048


def validate_discord_webhook_url(value: str) -> str:
    """驗證並正規化 Discord webhook URL；空值代表未設定。"""

    url = str(value or "").strip()
    if not url:
        return ""
    if len(url) > MAX_DISCORD_WEBHOOK_URL_LENGTH:
        raise ValueError("discord_webhook_url_too_long")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise ValueError("discord_webhook_url_invalid") from exc
    if parsed.scheme.casefold() != "https":
        raise ValueError("discord_webhook_must_be_https")
    host = (parsed.hostname or "").casefold().rstrip(".")
    if host not in ALLOWED_DISCORD_WEBHOOK_HOSTS:
        raise ValueError("discord_webhook_host_not_allowed")
    if parsed.username or parsed.password:
        raise ValueError("discord_webhook_userinfo_not_allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("discord_webhook_port_not_allowed") from exc
    if port not in (None, 443):
        raise ValueError("discord_webhook_port_not_allowed")
    if not DISCORD_WEBHOOK_PATH_RE.fullmatch(parsed.path):
        raise ValueError("discord_webhook_path_invalid")
    if parsed.query or parsed.fragment:
        raise ValueError("discord_webhook_extra_parts_not_allowed")
    return urlunsplit(("https", host, parsed.path, "", ""))
