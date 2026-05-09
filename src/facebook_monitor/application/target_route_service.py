"""Target URL route detection service。

職責：將使用者貼上的 Facebook URL 轉成 posts/comments target 建立所需 route，
供 Web UI、未來 wizard 或其他 application command 共用。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.facebook.route_detection import RouteDetectionError
from facebook_monitor.facebook.route_detection import detect_group_comments_route
from facebook_monitor.facebook.route_detection import detect_group_posts_route


@dataclass(frozen=True)
class DetectedCommentsTargetRoute:
    """保存由單篇貼文 URL 自動判斷出的 comments target route。"""

    group_id: str
    parent_post_id: str
    canonical_url: str

    @property
    def group_canonical_url(self) -> str:
        """回傳解析社團名稱時使用的社團首頁 URL。"""

        return f"https://www.facebook.com/groups/{self.group_id}"


@dataclass(frozen=True)
class DetectedPostsTargetRoute:
    """保存由社團首頁 URL 自動判斷出的 posts target route。"""

    group_id: str
    canonical_url: str


def detect_target_route_from_url(value: str) -> DetectedCommentsTargetRoute | DetectedPostsTargetRoute:
    """依 URL 自動判斷新增 target 類型，不要求使用者手動選 posts/comments。"""

    try:
        comments_route = detect_group_comments_route(value, page_title="")
    except RouteDetectionError:
        try:
            posts_route = detect_group_posts_route(value, page_title="")
        except RouteDetectionError as posts_error:
            raise posts_error
        return DetectedPostsTargetRoute(
            group_id=posts_route.group_id,
            canonical_url=posts_route.canonical_url,
        )
    return DetectedCommentsTargetRoute(
        group_id=comments_route.group_id,
        parent_post_id=comments_route.parent_post_id,
        canonical_url=comments_route.canonical_url,
    )
