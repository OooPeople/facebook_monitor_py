"""Discord webhook notification sender。"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

import httpx

from facebook_monitor.notifications.safe_messages import safe_exception_message


DISCORD_CONTENT_LIMIT = 1900
DISCORD_RATE_LIMIT_STATUS = 429
DISCORD_RATE_LIMIT_RETRY_LIMIT = 1
DISCORD_RATE_LIMIT_RETRY_AFTER_CAP_SECONDS = 5.0


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
    *,
    rate_limit_retry_limit: int = DISCORD_RATE_LIMIT_RETRY_LIMIT,
    retry_after_cap_seconds: float = DISCORD_RATE_LIMIT_RETRY_AFTER_CAP_SECONDS,
) -> DiscordResult:
    """送出一則 Discord webhook 通知，遇到短暫 429 會依 Retry-After 重試一次。"""

    webhook_url = config.webhook_url.strip()
    if not webhook_url:
        return DiscordResult(ok=False, status_code=None, message="discord_skipped")

    content = truncate_discord_content("\n".join(part for part in (title, message) if part))
    payload = {
        "username": config.username,
        "content": content,
    }
    try:
        attempts = max(int(rate_limit_retry_limit), 0) + 1
        for attempt_index in range(attempts):
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
            if response.status_code == DISCORD_RATE_LIMIT_STATUS:
                retry_after = extract_discord_retry_after_seconds(response)
                if (
                    attempt_index + 1 < attempts
                    and 0 < retry_after <= max(float(retry_after_cap_seconds), 0.0)
                ):
                    time.sleep(retry_after)
                    continue
            return DiscordResult(
                ok=False,
                status_code=response.status_code,
                message=build_discord_failure_message(response),
            )
        return DiscordResult(ok=False, status_code=None, message="discord_failed")
    except httpx.HTTPError as exc:
        return DiscordResult(
            ok=False,
            status_code=None,
            message=safe_exception_message("discord_failed", exc),
        )
    except Exception as exc:
        return DiscordResult(
            ok=False,
            status_code=None,
            message=safe_exception_message("discord_failed", exc),
        )


def truncate_discord_content(value: str, limit: int = DISCORD_CONTENT_LIMIT) -> str:
    """限制 Discord content 長度，對齊 userscript 保守上限。"""

    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."


def extract_discord_retry_after_seconds(response: httpx.Response) -> float:
    """從 Discord 429 response 讀取 retry-after 秒數，失敗時回傳 0。"""

    for header in ("Retry-After", "X-RateLimit-Reset-After"):
        value = response.headers.get(header)
        if value:
            seconds = parse_retry_after_seconds(value)
            if seconds > 0:
                return seconds
    payload = parse_response_json(response)
    retry_after = payload.get("retry_after")
    if isinstance(retry_after, int | float):
        return max(float(retry_after), 0.0)
    if isinstance(retry_after, str):
        return parse_retry_after_seconds(retry_after)
    return 0.0


def parse_retry_after_seconds(value: str) -> float:
    """將 Retry-After header 整理為非負秒數。"""

    try:
        return max(float(str(value).strip()), 0.0)
    except ValueError:
        return 0.0


def build_discord_failure_message(response: httpx.Response) -> str:
    """建立不含 webhook 的 Discord 失敗診斷訊息。"""

    status_code = response.status_code
    if status_code != DISCORD_RATE_LIMIT_STATUS:
        return f"discord_failed:{status_code}"

    payload = parse_response_json(response)
    retry_after = extract_discord_retry_after_seconds(response)
    parts = [f"discord_failed:{status_code}"]
    if retry_after > 0:
        parts.append(f"retry_after={retry_after:g}s")
    if "global" in payload:
        parts.append(f"global={str(bool(payload.get('global'))).lower()}")
    message = str(payload.get("message") or "").strip()
    if message:
        parts.append(f"message={message[:80]}")
    return " ".join(parts)


def parse_response_json(response: httpx.Response) -> dict[str, Any]:
    """安全解析 Discord JSON body；非 JSON 時回傳空 dict。"""

    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}
