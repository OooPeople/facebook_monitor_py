"""外部 URL policy tests。"""

from __future__ import annotations

import pytest

from facebook_monitor.core.external_url_policy import sanitize_facebook_group_cover_image_url
from facebook_monitor.core.external_url_policy import sanitize_facebook_image_url


def test_sanitize_facebook_image_url_accepts_fbcdn_https() -> None:
    """Facebook / fbcdn HTTPS 圖片 URL 可進入 UI view model。"""

    result = sanitize_facebook_image_url(
        " https://scontent.xx.fbcdn.net/v/t39.30808-6/cover.jpg?stp=dst-jpg "
    )

    assert result.ok
    assert result.url == "https://scontent.xx.fbcdn.net/v/t39.30808-6/cover.jpg?stp=dst-jpg"


def test_sanitize_facebook_image_url_rejects_unsafe_urls() -> None:
    """任意 host 或非 HTTPS URL 不可成為 dashboard image src。"""

    for value in (
        "",
        "http://scontent.xx.fbcdn.net/cover.jpg",
        "https://example.com/cover.jpg",
        "https://fbcdn.net.evil.test/cover.jpg",
        "https://user:pass@scontent.xx.fbcdn.net/cover.jpg",
        "https://scontent.xx.fbcdn.net:8443/cover.jpg",
        "https://scontent.xx.fbcdn.net:bad/cover.jpg",
        "https://127.0.0.1/cover.jpg",
        "javascript:alert(1)",
    ):
        result = sanitize_facebook_image_url(value)
        assert not result.ok, value
        assert result.url == ""


@pytest.mark.parametrize(
    ("value", "reason"),
    (
        ("", "empty"),
        ("https:///cover.jpg", "host_missing"),
        ("https://[::1", "parse_error"),
        ("http://scontent.xx.fbcdn.net/cover.jpg", "non_https"),
        ("https://example.com/cover.jpg", "host_not_allowed"),
        ("https://fbcdn.net.evil.test/cover.jpg", "host_not_allowed"),
        ("https://user:pass@scontent.xx.fbcdn.net/cover.jpg", "userinfo_not_allowed"),
        ("https://scontent.xx.fbcdn.net:8443/cover.jpg", "port_not_allowed"),
        ("https://scontent.xx.fbcdn.net:bad/cover.jpg", "port_parse_error"),
        ("javascript:alert(1)", "non_https"),
    ),
)
def test_sanitize_facebook_image_url_reports_reject_reason(
    value: str,
    reason: str,
) -> None:
    """reject reason 是後續 host sample diagnostics 的穩定分類。"""

    result = sanitize_facebook_image_url(value)

    assert not result.ok
    assert result.reason == reason


def test_sanitize_facebook_image_url_normalizes_host_boundaries() -> None:
    """大小寫 host、trailing dot 與 :443 不應造成合法 CDN URL 被誤拒。"""

    result = sanitize_facebook_image_url(
        "https://SCONTENT.xx.FBCDN.net.:443/v/cover.jpg?stp=dst-jpg"
    )

    assert result.ok
    assert result.url == "https://scontent.xx.fbcdn.net/v/cover.jpg?stp=dst-jpg"


@pytest.mark.parametrize(
    "url",
    (
        "https://scontent.xx.fbcdn.net/v/t39.30808-6/group-cover.jpg?stp=dst-jpg",
        "https://lookaside.fbsbx.com/lookaside/crawler/media/?media_id=123",
    ),
)
def test_sanitize_facebook_group_cover_image_url_accepts_cover_hosts(url: str) -> None:
    """社團封面 URL policy 不應誤擋合法 Facebook CDN cover URL。"""

    result = sanitize_facebook_group_cover_image_url(url)

    assert result.ok
    assert result.url == url


@pytest.mark.parametrize(
    "url",
    (
        "https://static.facebook.com/images/logos/facebook_2x.png",
        "https://www.facebook.com/images/logos/facebook_2x.png",
        "https://facebook.com/images/logos/facebook_2x.png",
    ),
)
def test_sanitize_facebook_group_cover_image_url_rejects_generic_logo(url: str) -> None:
    """社團封面 URL policy 不接受 Facebook 錯誤頁的通用品牌圖。"""

    result = sanitize_facebook_group_cover_image_url(url)

    assert not result.ok
    assert result.url == ""
    assert result.reason == "generic_facebook_asset"
