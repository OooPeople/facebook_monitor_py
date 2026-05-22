"""外部 URL 安全策略。

職責：集中 Web UI 可直接讓瀏覽器載入的外部資源 URL 邊界，避免
extractor 或舊 DB 值把任意 HTTP(S) URL 送進 template。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.parse import urlunsplit


FACEBOOK_IMAGE_ALLOWED_HOST_SUFFIXES = (
    "fbcdn.net",
    "fbsbx.com",
    "facebook.com",
)


@dataclass(frozen=True)
class ExternalUrlValidationResult:
    """保存外部 URL 驗證結果。"""

    ok: bool
    url: str = ""
    reason: str = ""


def sanitize_facebook_image_url(value: object) -> ExternalUrlValidationResult:
    """只允許 Facebook / fbcdn HTTPS 圖片 URL 進入 UI。"""

    raw = str(value or "").strip()
    if not raw:
        return ExternalUrlValidationResult(ok=False, reason="empty")
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ExternalUrlValidationResult(ok=False, reason="parse_error")
    if parsed.scheme.casefold() != "https":
        return ExternalUrlValidationResult(ok=False, reason="non_https")
    host = (parsed.hostname or "").casefold().rstrip(".")
    if not host:
        return ExternalUrlValidationResult(ok=False, reason="host_missing")
    if not any(_host_matches_suffix(host, suffix) for suffix in FACEBOOK_IMAGE_ALLOWED_HOST_SUFFIXES):
        return ExternalUrlValidationResult(ok=False, reason="host_not_allowed")
    if parsed.username or parsed.password:
        return ExternalUrlValidationResult(ok=False, reason="userinfo_not_allowed")
    if parsed.port not in (None, 443):
        return ExternalUrlValidationResult(ok=False, reason="port_not_allowed")
    return ExternalUrlValidationResult(
        ok=True,
        url=urlunsplit(("https", host, parsed.path, parsed.query, "")),
    )


def _host_matches_suffix(host: str, suffix: str) -> bool:
    """確認 host 是 suffix 本身或其子網域，避免 evilfbcdn.net 這類繞過。"""

    normalized_suffix = suffix.casefold().lstrip(".")
    return host == normalized_suffix or host.endswith("." + normalized_suffix)
