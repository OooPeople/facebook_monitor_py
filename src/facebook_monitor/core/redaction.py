"""使用者可見文字的敏感資訊遮罩工具。"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl
from urllib.parse import urlencode
from urllib.parse import urlsplit
from urllib.parse import urlunsplit


_DISCORD_WEBHOOK_RE = re.compile(
    r"https?://(?P<host>(?:canary\.|ptb\.)?discord(?:app)?\.com)"
    r"/api/webhooks/(?P<webhook_id>\d+)/(?P<token>[^\s\"'<>]+)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"\bhttps?://[^\s\"'<>]+", re.IGNORECASE)
_WINDOWS_USER_PATH_RE = re.compile(r"\b[A-Za-z]:\\Users\\[^\\\r\n]+")
_POSIX_HOME_PATH_RE = re.compile(r"(?<!\w)/(?:Users|home)/[^/\r\n]+")
_AUTH_SCHEME_RE = re.compile(
    r"(?i)\b(authorization)(\s*[:=]\s*)(bearer|basic)\s+([^\s,;&]+)"
)
_AUTH_VALUE_RE = re.compile(
    r"(?i)\b(authorization)(\s*[:=]\s*)(?!(?:bearer|basic)\b)([^\s,;&]+)"
)
_COOKIE_HEADER_RE = re.compile(r"(?im)\b(set-cookie|cookie)\s*:\s*([^\r\n]+)")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(token|secret|password|api[_-]?key)\s*[:=]\s*([^\s,;&]+)"
)
_SECRET_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "key",
    "password",
    "secret",
    "sig",
    "signature",
    "token",
}
REDACTED = "[已隱藏]"


def redact_sensitive_text(value: str) -> str:
    """遮掉 endpoint token、credential query 與本機使用者目錄。"""

    text = str(value or "")
    if not text:
        return ""
    text = _DISCORD_WEBHOOK_RE.sub(_redact_discord_webhook_match, text)
    text = _AUTH_SCHEME_RE.sub(_redact_auth_scheme_match, text)
    text = _AUTH_VALUE_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text)
    text = _COOKIE_HEADER_RE.sub(lambda match: f"{match.group(1)}: {REDACTED}", text)
    text = _URL_RE.sub(lambda match: _redact_url(match.group(0)), text)
    text = _WINDOWS_USER_PATH_RE.sub(r"%USERPROFILE%", text)
    text = _POSIX_HOME_PATH_RE.sub(r"~", text)
    return _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}={REDACTED}", text)


def _redact_auth_scheme_match(match: re.Match[str]) -> str:
    """保留 Authorization scheme，遮掉實際 credential。"""

    return f"{match.group(1)}{match.group(2)}{match.group(3)} {REDACTED}"


def _redact_discord_webhook_match(match: re.Match[str]) -> str:
    """保留 Discord webhook id 方便辨識，移除實際 token。"""

    return f"https://{match.group('host')}/api/webhooks/{match.group('webhook_id')}/{REDACTED}"


def _redact_url(raw_url: str) -> str:
    """遮掉 URL 內常見 credential 位置；無法解析時回傳原值。"""

    try:
        parts = urlsplit(raw_url)
    except ValueError:
        return raw_url
    netloc = parts.netloc
    changed = False
    if "@" in netloc:
        host = netloc.rsplit("@", 1)[1]
        netloc = f"{REDACTED}@{host}"
        changed = True
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    if query_pairs:
        redacted_pairs = []
        for key, value in query_pairs:
            if key.lower() in _SECRET_QUERY_KEYS:
                redacted_pairs.append((key, REDACTED))
                changed = True
            else:
                redacted_pairs.append((key, value))
        query = urlencode(redacted_pairs, doseq=True)
    else:
        query = parts.query
    if not changed:
        return raw_url
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
