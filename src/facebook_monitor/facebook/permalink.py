"""Facebook permalink normalization helpers。

職責：集中 canonical permalink 語義，提供 posts/comments extractor 共用的
URL 正規化與來源判斷。此模組不依賴 Playwright 或 DOM。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs

from facebook_monitor.core.permalink_identity import build_canonical_group_post_url
from facebook_monitor.core.permalink_identity import extract_comment_id_from_value
from facebook_monitor.core.permalink_identity import extract_group_route_query_post_id
from facebook_monitor.core.permalink_identity import normalize_facebook_url
from facebook_monitor.core.permalink_identity import normalize_permalink as normalize_permalink_identity


@dataclass(frozen=True)
class PermalinkDetails:
    """保存 canonical permalink 與其來源。"""

    permalink: str = ""
    source: str = "unavailable"


@dataclass(frozen=True)
class CommentPermalinkDetails:
    """保存 canonical comment permalink、來源與 comment id。"""

    permalink: str = ""
    source: str = "unavailable"
    comment_id: str = ""


def normalize_permalink(raw_url: str) -> str:
    """將支援的 Facebook href 變體正規化成 canonical group post URL。"""

    return normalize_permalink_identity(raw_url)


def build_canonical_group_comment_url(group_id: str, post_id: str, comment_id: str) -> str:
    """組出固定格式的 Facebook group comment canonical URL。"""

    normalized_group_id = str(group_id or "").strip()
    normalized_post_id = str(post_id or "").strip()
    normalized_comment_id = str(comment_id or "").strip()
    if (
        not normalized_group_id
        or not re.fullmatch(r"\d{8,}", normalized_post_id)
        or not re.fullmatch(r"\d{8,}", normalized_comment_id)
    ):
        return ""
    return (
        f"https://www.facebook.com/groups/{normalized_group_id}/posts/"
        f"{normalized_post_id}/?comment_id={normalized_comment_id}"
    )


def build_group_scoped_permalink_details(
    group_id: str,
    post_id: str,
    source: str,
    expected_group_id: str = "",
) -> PermalinkDetails:
    """在 group id 符合預期時建立 canonical permalink details。"""

    normalized_group_id = str(group_id or "").strip()
    normalized_post_id = str(post_id or "").strip()
    if not normalized_group_id or not normalized_post_id:
        return PermalinkDetails("", "")
    if expected_group_id and normalized_group_id != expected_group_id:
        return PermalinkDetails("", "")
    permalink = build_canonical_group_post_url(normalized_group_id, normalized_post_id)
    return PermalinkDetails(permalink, source) if permalink else PermalinkDetails("", "")


def extract_group_post_route_id(parsed_url: Any, expected_group_id: str = "") -> str:
    """從 Facebook group post route 抽出 parent post id。"""

    if parsed_url is None:
        return ""
    pathname = str(getattr(parsed_url, "path", "") or "").rstrip("/")
    group_post_match = re.match(r"^/groups/([^/?#]+)/posts?/(?:pcb\.)?(\d+)$", pathname, re.I)
    if group_post_match:
        group_id = group_post_match.group(1)
        if expected_group_id and group_id != expected_group_id:
            return ""
        return group_post_match.group(2)

    group_permalink_match = re.match(r"^/groups/([^/?#]+)/permalink/(\d+)$", pathname, re.I)
    if group_permalink_match:
        group_id = group_permalink_match.group(1)
        if expected_group_id and group_id != expected_group_id:
            return ""
        return group_permalink_match.group(2)

    group_route_match = re.match(r"^/groups/([^/?#]+)(?:/.*)?$", pathname, re.I)
    if group_route_match:
        group_id = group_route_match.group(1)
        if expected_group_id and group_id != expected_group_id:
            return ""
        return extract_group_route_query_post_id(parsed_url)

    return ""


def extract_photo_route_group_id(parsed_url: Any, expected_group_id: str = "") -> str:
    """從 photo route query 參數中推回 group id。"""

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


def extract_photo_route_permalink_details(
    parsed_url: Any,
    expected_group_id: str = "",
) -> PermalinkDetails:
    """將 photo route 正規化回對應 group post permalink。"""

    return build_group_scoped_permalink_details(
        extract_photo_route_group_id(parsed_url, expected_group_id),
        extract_group_route_query_post_id(parsed_url),
        "photo_gm_anchor",
        expected_group_id,
    )


def get_permalink_source_priority(source: str = "") -> int:
    """回傳 permalink 來源排序權重，數字越小越可信。"""

    priorities = {
        "groups_post_anchor": 0,
        "group_permalink_anchor": 1,
        "permalink_php_anchor": 2,
        "group_query_anchor": 3,
        "pcb_anchor": 4,
    }
    return priorities.get(source, 5)


def is_comment_permalink_href(value: str) -> bool:
    """判斷 href 是否為 comment-level permalink。"""

    parsed = normalize_facebook_url(value)
    if parsed is None:
        return False
    query = parse_qs(parsed.query)
    return "comment_id" in query or "reply_comment_id" in query


def extract_comment_permalink_details(
    value: str,
    *,
    group_id: str,
    parent_post_id: str,
) -> CommentPermalinkDetails:
    """將 comment permalink href 正規化為 canonical comment URL。"""

    comment_id = extract_comment_id_from_value(value)
    if not comment_id:
        return CommentPermalinkDetails("", "", "")

    parsed = normalize_facebook_url(value)
    route_post_id = extract_group_post_route_id(parsed, group_id)
    post_id = route_post_id or str(parent_post_id or "").strip()
    permalink = build_canonical_group_comment_url(group_id, post_id, comment_id)
    return CommentPermalinkDetails(
        permalink=permalink or str(value or "").strip(),
        source="comment_anchor" if permalink else "comment_anchor_raw",
        comment_id=comment_id,
    )


def extract_canonical_permalink_from_href(
    value: str,
    expected_group_id: str = "",
) -> PermalinkDetails:
    """依產品 permalink 規則，把 Facebook href 變體正規化為 canonical URL。"""

    parsed = normalize_facebook_url(value)
    if parsed is None:
        return PermalinkDetails("", "")

    pathname = parsed.path.rstrip("/")
    group_post_match = re.match(r"^/groups/([^/?#]+)/posts?/(\d+)$", pathname, re.I)
    if group_post_match:
        return build_group_scoped_permalink_details(
            group_post_match.group(1),
            group_post_match.group(2),
            "groups_post_anchor",
            expected_group_id,
        )

    group_permalink_match = re.match(r"^/groups/([^/?#]+)/permalink/(\d+)$", pathname, re.I)
    if group_permalink_match:
        return build_group_scoped_permalink_details(
            group_permalink_match.group(1),
            group_permalink_match.group(2),
            "group_permalink_anchor",
            expected_group_id,
        )

    pcb_match = re.match(r"^/groups/([^/?#]+)/posts/pcb\.(\d+)$", pathname, re.I)
    if pcb_match:
        return build_group_scoped_permalink_details(
            pcb_match.group(1),
            pcb_match.group(2),
            "pcb_anchor",
            expected_group_id,
        )

    if re.match(r"^/photo(?:\.php)?$", pathname, re.I):
        return extract_photo_route_permalink_details(parsed, expected_group_id)

    group_route_match = re.match(r"^/groups/([^/?#]+)(?:/.*)?$", pathname, re.I)
    if group_route_match:
        return build_group_scoped_permalink_details(
            group_route_match.group(1),
            extract_group_route_query_post_id(parsed),
            "group_query_anchor",
            expected_group_id,
        )

    if not re.match(r"^/permalink\.php$", pathname, re.I):
        return PermalinkDetails("", "")

    query = parse_qs(parsed.query)
    return build_group_scoped_permalink_details(
        str(
            query.get("id", [""])[0]
            or query.get("group_id", [""])[0]
            or expected_group_id
            or ""
        ).strip(),
        extract_group_route_query_post_id(parsed),
        "permalink_php_anchor",
        expected_group_id,
    )
