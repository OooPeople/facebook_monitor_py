"""Facebook route detection tests。"""

from __future__ import annotations

import pytest

from facebook_monitor.facebook.route_detection import RouteDetectionError
from facebook_monitor.facebook.route_detection import clean_facebook_page_title
from facebook_monitor.facebook.route_detection import detect_group_comments_route
from facebook_monitor.facebook.route_detection import detect_group_posts_route


def test_detect_group_posts_route_from_group_feed_url() -> None:
    """group feed URL 可轉成 canonical target route。"""

    route = detect_group_posts_route(
        "https://www.facebook.com/groups/222518561920110/?sorting_setting=CHRONOLOGICAL",
        page_title="測試社團 | Facebook",
    )

    assert route.group_id == "222518561920110"
    assert route.canonical_url == "https://www.facebook.com/groups/222518561920110"
    assert route.group_name == "測試社團"


def test_reject_post_permalink() -> None:
    """單篇貼文 permalink 不會被誤存為 group feed target。"""

    with pytest.raises(RouteDetectionError):
        detect_group_posts_route(
            "https://www.facebook.com/groups/222518561920110/posts/123456789"
        )


def test_reject_groups_feed() -> None:
    """Facebook groups 入口頁不會被誤判為單一社團。"""

    with pytest.raises(RouteDetectionError):
        detect_group_posts_route("https://www.facebook.com/groups/feed")


def test_reject_non_facebook_url() -> None:
    """非 Facebook URL 會回報 route detection error。"""

    with pytest.raises(RouteDetectionError):
        detect_group_posts_route("https://example.com/groups/222518561920110")


def test_detect_group_comments_route_from_group_post_url() -> None:
    """comments route 會從單篇社團貼文 URL 抽出 group 與 parent post。"""

    route = detect_group_comments_route(
        "https://www.facebook.com/groups/222518561920110/posts/2187454285426518/"
        "?comment_id=123456789",
        page_title="(3) 測試社團 | Facebook",
    )

    assert route.group_id == "222518561920110"
    assert route.parent_post_id == "2187454285426518"
    assert route.canonical_url == (
        "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
    )
    assert route.group_name == "測試社團"


def test_detect_group_comments_route_from_permalink_url() -> None:
    """comments route 支援 Facebook group permalink 形狀。"""

    route = detect_group_comments_route(
        "https://www.facebook.com/groups/222518561920110/permalink/2187454285426518",
    )

    assert route.group_id == "222518561920110"
    assert route.parent_post_id == "2187454285426518"


def test_detect_group_comments_route_rejects_group_feed_url() -> None:
    """comments target 必須使用單篇貼文 URL，不能用社團首頁。"""

    with pytest.raises(RouteDetectionError, match="單篇社團貼文"):
        detect_group_comments_route("https://www.facebook.com/groups/222518561920110")


def test_clean_page_title() -> None:
    """Facebook suffix 與前置通知數會從 page title 移除。"""

    assert clean_facebook_page_title("測試社團 | Facebook") == "測試社團"
    assert clean_facebook_page_title("(3) 測試社團 | Facebook") == "測試社團"
    assert clean_facebook_page_title("(2) (3) 測試社團 | Facebook") == "測試社團"
    assert clean_facebook_page_title("（12） 測試社團 | 臉書") == "測試社團"
