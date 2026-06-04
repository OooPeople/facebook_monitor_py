"""外部 URL policy tests。"""

from __future__ import annotations

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
