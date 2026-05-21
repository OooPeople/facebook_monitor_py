"""Web UI URL safety helpers。

職責：集中清理會輸出成 `<a href>` 或 JSON link 的外部網址，避免 DB 內
非 Facebook permalink 被 Web UI 當成可點擊連結。
"""

from __future__ import annotations

from urllib.parse import urlparse

from facebook_monitor.core.permalink_identity import extract_canonical_permalink_from_href
from facebook_monitor.core.permalink_identity import extract_comment_id_from_value
from facebook_monitor.facebook.permalink import build_canonical_group_comment_url


ALLOWED_FACEBOOK_LINK_HOSTS = frozenset({"www.facebook.com", "m.facebook.com"})


def safe_facebook_permalink(value: str) -> str:
    """只允許 Web UI 顯示 canonical group post/comment permalink。"""

    normalized = str(value or "").strip()
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    if parsed.scheme != "https":
        return ""
    if (parsed.hostname or "").casefold() not in ALLOWED_FACEBOOK_LINK_HOSTS:
        return ""
    post_permalink = extract_canonical_permalink_from_href(normalized)
    if not post_permalink:
        return ""
    comment_id = extract_comment_id_from_value(normalized)
    if comment_id:
        post_parts = _canonical_group_post_parts(post_permalink)
        if post_parts is None:
            return ""
        return build_canonical_group_comment_url(*post_parts, comment_id)
    return post_permalink


def _canonical_group_post_parts(value: str) -> tuple[str, str] | None:
    """從 canonical group post URL 取出 group id 與 post id。"""

    parsed = urlparse(value)
    path_parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() != "www.facebook.com"
        or len(path_parts) != 4
        or path_parts[0] != "groups"
        or path_parts[2] != "posts"
    ):
        return None
    return path_parts[1], path_parts[3]
