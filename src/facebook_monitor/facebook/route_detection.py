"""Facebook group posts route detection。

職責：把瀏覽器目前 URL 解析成目前支援的 group posts target 描述。
此模組保持純函式，避免混入 Playwright 或 persistence。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import parse_qs
from urllib.parse import urlparse

from facebook_monitor.facebook.permalink import build_canonical_group_post_url
from facebook_monitor.facebook.permalink import extract_group_route_query_post_id


FACEBOOK_HOSTS = {
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "mbasic.facebook.com",
}

POST_ROUTE_MARKERS = {"posts", "permalink"}
RESERVED_GROUP_PATH_SEGMENTS = {
    "browse",
    "categories",
    "category",
    "create",
    "discover",
    "feed",
    "groups",
    "joined",
    "notifications",
    "pending",
    "suggested",
}


class RouteDetectionError(ValueError):
    """表示目前 URL 不是可保存的 Facebook target route。"""


@dataclass(frozen=True)
class CapturedGroupPostsRoute:
    """保存從 Facebook group feed URL 擷取出的 target route。"""

    group_id: str
    canonical_url: str
    group_name: str = ""


@dataclass(frozen=True)
class CapturedGroupCommentsRoute:
    """保存從 Facebook group post URL 擷取出的 comments target route。"""

    group_id: str
    parent_post_id: str
    canonical_url: str
    group_name: str = ""


def detect_group_posts_route(
    url: str,
    page_title: str = "",
) -> CapturedGroupPostsRoute:
    """從 Facebook group feed URL 建立可保存的 group posts route。"""

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in FACEBOOK_HOSTS:
        raise RouteDetectionError("目前頁面不是支援的 Facebook 網域")

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0] != "groups":
        raise RouteDetectionError("目前頁面不是 Facebook group feed")

    group_id = path_parts[1].strip()
    if not group_id:
        raise RouteDetectionError("無法從 URL 擷取 group id")
    if is_reserved_group_path_segment(group_id):
        raise RouteDetectionError(
            "目前頁面是 Facebook groups 入口，不是單一社團首頁；"
            "請貼上或切到 /groups/<group_id> 後再 capture"
        )

    if len(path_parts) > 2 and path_parts[2] in POST_ROUTE_MARKERS:
        raise RouteDetectionError("目前頁面是單篇貼文 URL，不是 group feed target")

    if len(path_parts) > 2:
        raise RouteDetectionError("目前 group URL 路徑尚未支援，請切回社團首頁後再 capture")

    return CapturedGroupPostsRoute(
        group_id=group_id,
        canonical_url=f"https://www.facebook.com/groups/{group_id}",
        group_name=clean_facebook_page_title(page_title),
    )


def detect_group_comments_route(
    url: str,
    page_title: str = "",
) -> CapturedGroupCommentsRoute:
    """從 Facebook group post URL 建立可保存的 comments route。"""

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in FACEBOOK_HOSTS:
        raise RouteDetectionError("目前頁面不是支援的 Facebook 網域")

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0] != "groups":
        raise RouteDetectionError("目前頁面不是 Facebook group post")

    group_id = path_parts[1].strip()
    if not group_id:
        raise RouteDetectionError("無法從 URL 擷取 group id")
    if is_reserved_group_path_segment(group_id):
        raise RouteDetectionError("目前頁面是 Facebook groups 入口，不是單一社團貼文")

    parent_post_id = extract_group_post_route_id_from_parsed_url(parsed, group_id)
    if not parent_post_id:
        raise RouteDetectionError("comments target 需要貼上單篇社團貼文 URL")

    canonical_url = build_canonical_group_post_url(group_id, parent_post_id)
    if not canonical_url:
        raise RouteDetectionError("無法建立 comments target canonical URL")

    return CapturedGroupCommentsRoute(
        group_id=group_id,
        parent_post_id=parent_post_id,
        canonical_url=canonical_url,
        group_name=clean_facebook_page_title(page_title),
    )


def extract_group_post_route_id_from_parsed_url(parsed_url: object, expected_group_id: str) -> str:
    """抽取 group post route id，供 comments target 建立流程使用。"""

    path = getattr(parsed_url, "path", "").rstrip("/")
    group_post_match = re.match(r"^/groups/([^/?#]+)/posts?/(?:pcb\.)?(\d+)$", path, re.I)
    if group_post_match:
        return group_post_match.group(2) if group_post_match.group(1) == expected_group_id else ""

    group_permalink_match = re.match(r"^/groups/([^/?#]+)/permalink/(\d+)$", path, re.I)
    if group_permalink_match:
        return (
            group_permalink_match.group(2)
            if group_permalink_match.group(1) == expected_group_id
            else ""
        )

    group_route_match = re.match(r"^/groups/([^/?#]+)(?:/.*)?$", path, re.I)
    if group_route_match:
        if group_route_match.group(1) != expected_group_id:
            return ""
        return extract_group_route_query_post_id(parsed_url)

    query = parse_qs(getattr(parsed_url, "query", ""))
    return str(query.get("story_fbid", [""])[0]).strip()


def clean_facebook_page_title(page_title: str) -> str:
    """清理 Facebook page title，保留較適合當 target 名稱的文字。"""

    title = page_title.strip()
    suffixes = (" | Facebook", " | 臉書")
    for suffix in suffixes:
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    title = re.sub(r"^(?:[（(]\d+[）)]\s*)+", "", title)
    return title


def is_reserved_group_path_segment(value: str) -> bool:
    """判斷 groups 後方路徑是否為 Facebook 保留頁面名稱。"""

    return value.strip().lower() in RESERVED_GROUP_PATH_SEGMENTS
