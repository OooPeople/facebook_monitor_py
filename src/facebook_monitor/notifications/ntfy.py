"""ntfy notification sender。

職責：提供 ntfy HTTP 發送能力，讓 worker 不直接承擔通知通道細節。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import httpx

from facebook_monitor.core.defaults import PYTHON_NOTIFICATION_RUNTIME_DEFAULTS
from facebook_monitor.notifications.safe_messages import safe_exception_message


@dataclass(frozen=True)
class NtfyConfig:
    """保存 ntfy 發送所需設定。"""

    server: str = PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.ntfy_server
    topic: str = ""
    click_url: str = ""


@dataclass(frozen=True)
class NtfyResult:
    """保存 ntfy 發送結果，供 DB 與 log 記錄使用。"""

    ok: bool
    status_code: int | None
    message: str


def send_ntfy_notification(config: NtfyConfig, title: str, message: str) -> NtfyResult:
    """送出一則 ntfy 通知。"""

    topic = config.topic.strip()
    if not topic:
        return NtfyResult(ok=False, status_code=None, message="ntfy topic is empty")

    server = (
        config.server.strip().rstrip("/")
        or PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.ntfy_server
    )
    url = f"{server}/{quote(topic, safe='')}"
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Title": to_ascii_header_value(
            title,
            fallback=PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.ntfy_ascii_title_fallback,
        ),
        "Priority": "default",
        "Tags": "bell",
    }
    click_url = to_ascii_header_value(config.click_url, fallback="")
    if click_url:
        headers["Click"] = click_url
    try:
        response = httpx.post(
            url,
            content=message.encode("utf-8"),
            headers=headers,
            timeout=PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.ntfy_timeout_seconds,
        )
        if 200 <= response.status_code < 300:
            return NtfyResult(ok=True, status_code=response.status_code, message="sent")
        return NtfyResult(
            ok=False,
            status_code=response.status_code,
            message=f"unexpected status code: {response.status_code}",
        )
    except httpx.HTTPError as error:
        return NtfyResult(
            ok=False,
            status_code=None,
            message=safe_exception_message("ntfy_failed", error),
        )
    except Exception as error:
        return NtfyResult(
            ok=False,
            status_code=None,
            message=safe_exception_message("ntfy_failed", error),
        )


def to_ascii_header_value(value: str, *, fallback: str) -> str:
    """回傳可安全放入 HTTP header 的 ASCII 值，中文內容保留在 body。"""

    text = " ".join(str(value or "").split())
    if not text:
        return fallback
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return fallback
    return text
