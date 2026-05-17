"""Facebook URL identity helpers used by core dedupe rules.

職責：提供不依賴 DOM / Playwright / facebook package 的純字串 permalink
identity 規則，避免 core dedupe 反向依賴平台層模組。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urljoin
from urllib.parse import urlparse


def extract_first_pattern_match(values: list[str], patterns: list[re.Pattern[str]]) -> str:
    """依序從多個值套用多個 regex，回傳第一個擷取結果。"""

    for value in values:
        text = str(value or "")
        if not text:
            continue
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return match.group(1)
    return ""


def extract_comment_id_from_value(value: str) -> str:
    """從 URL 或 metadata 字串抽出 comment id。"""

    return extract_first_pattern_match(
        [str(value or "")],
        [
            re.compile(r"[?&](?:comment_id|reply_comment_id)=(\d{8,})", re.I),
            re.compile(r"\b(?:comment_id|reply_comment_id|feedback_comment_id)[\"'=:\s]+(\d{8,})", re.I),
            re.compile(r"\"(?:comment_id|reply_comment_id|feedback_comment_id)\":\"?(\d+)", re.I),
        ],
    )


def normalize_facebook_url(value: str, base_url: str = "https://www.facebook.com") -> Any:
    """將輸入值解析為 Facebook URL；非 Facebook URL 回傳 None。"""

    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlparse(urljoin(base_url, text))
    if parsed.hostname and re.fullmatch(r"(www|m)\.facebook\.com", parsed.hostname, re.I):
        return parsed
    return None


def build_canonical_group_post_url(group_id: str, post_id: str) -> str:
    """組出固定格式的 Facebook group post canonical URL。"""

    normalized_group_id = str(group_id or "").strip()
    normalized_post_id = str(post_id or "").strip()
    if not normalized_group_id or not re.fullmatch(r"\d{8,}", normalized_post_id):
        return ""
    return f"https://www.facebook.com/groups/{normalized_group_id}/posts/{normalized_post_id}"


def extract_group_route_query_post_id(parsed_url: Any) -> str:
    """從 group route query 參數中抽出 post id。"""

    query = parse_qs(parsed_url.query)
    return extract_first_pattern_match(
        [
            query.get("story_fbid", [""])[0],
            query.get("multi_permalinks", [""])[0],
            query.get("set", [""])[0],
        ],
        [
            re.compile(r"\b(\d{8,})\b"),
            re.compile(r"\bgm\.(\d+)", re.I),
        ],
    )


def _build_group_scoped_permalink(
    group_id: str,
    post_id: str,
    expected_group_id: str = "",
) -> str:
    normalized_group_id = str(group_id or "").strip()
    normalized_post_id = str(post_id or "").strip()
    if not normalized_group_id or not normalized_post_id:
        return ""
    if expected_group_id and normalized_group_id != expected_group_id:
        return ""
    return build_canonical_group_post_url(normalized_group_id, normalized_post_id)


def _extract_photo_route_group_id(parsed_url: Any, expected_group_id: str = "") -> str:
    query = parse_qs(parsed_url.query)
    group_id = str(
        query.get("idorvanity", [""])[0]
        or query.get("group", [""])[0]
        or query.get("group_id", [""])[0]
        or query.get("id", [""])[0]
        or expected_group_id
        or ""
    ).strip()
    if expected_group_id and group_id != expected_group_id:
        return ""
    return group_id


def extract_canonical_permalink_from_href(value: str, expected_group_id: str = "") -> str:
    """依產品 permalink 規則，把 Facebook href 變體正規化為 canonical URL。"""

    parsed = normalize_facebook_url(value)
    if parsed is None:
        return ""

    pathname = parsed.path.rstrip("/")
    group_post_match = re.match(r"^/groups/([^/?#]+)/posts?/(\d+)$", pathname, re.I)
    if group_post_match:
        return _build_group_scoped_permalink(
            group_post_match.group(1),
            group_post_match.group(2),
            expected_group_id,
        )

    group_permalink_match = re.match(r"^/groups/([^/?#]+)/permalink/(\d+)$", pathname, re.I)
    if group_permalink_match:
        return _build_group_scoped_permalink(
            group_permalink_match.group(1),
            group_permalink_match.group(2),
            expected_group_id,
        )

    pcb_match = re.match(r"^/groups/([^/?#]+)/posts/pcb\.(\d+)$", pathname, re.I)
    if pcb_match:
        return _build_group_scoped_permalink(
            pcb_match.group(1),
            pcb_match.group(2),
            expected_group_id,
        )

    if re.match(r"^/photo(?:\.php)?$", pathname, re.I):
        return _build_group_scoped_permalink(
            _extract_photo_route_group_id(parsed, expected_group_id),
            extract_group_route_query_post_id(parsed),
            expected_group_id,
        )

    group_route_match = re.match(r"^/groups/([^/?#]+)(?:/.*)?$", pathname, re.I)
    if group_route_match:
        return _build_group_scoped_permalink(
            group_route_match.group(1),
            extract_group_route_query_post_id(parsed),
            expected_group_id,
        )

    if not re.match(r"^/permalink\.php$", pathname, re.I):
        return ""

    query = parse_qs(parsed.query)
    return _build_group_scoped_permalink(
        str(
            query.get("id", [""])[0]
            or query.get("group_id", [""])[0]
            or expected_group_id
            or ""
        ).strip(),
        extract_group_route_query_post_id(parsed),
        expected_group_id,
    )


def normalize_permalink(raw_url: str) -> str:
    """將支援的 Facebook href 變體正規化成 canonical group post URL。"""

    return extract_canonical_permalink_from_href(raw_url).lower()
