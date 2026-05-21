"""Web UI URL safety tests。"""

from __future__ import annotations

from facebook_monitor.webapp.url_safety import safe_facebook_permalink


def test_safe_facebook_permalink_rejects_facebook_redirect_surface() -> None:
    """Facebook host 上的 redirect endpoint 不能被當成 permalink。"""

    assert (
        safe_facebook_permalink(
            "https://www.facebook.com/l.php?u=https%3A%2F%2Fevil.example"
        )
        == ""
    )


def test_safe_facebook_permalink_canonicalizes_post_and_comment_urls() -> None:
    """支援的 posts/comment permalink 會輸出固定 canonical 格式。"""

    assert (
        safe_facebook_permalink(
            "https://m.facebook.com/groups/111/permalink/1234567890"
        )
        == "https://www.facebook.com/groups/111/posts/1234567890"
    )
    assert (
        safe_facebook_permalink(
            "https://www.facebook.com/groups/111/posts/1234567890?comment_id=9876543210"
        )
        == "https://www.facebook.com/groups/111/posts/1234567890/?comment_id=9876543210"
    )


def test_safe_facebook_permalink_rejects_non_permalink_facebook_pages() -> None:
    """一般 Facebook 頁面不是 Web UI 可輸出的外部 permalink。"""

    assert safe_facebook_permalink("https://www.facebook.com/groups/111") == ""
    assert safe_facebook_permalink("https://www.facebook.com/profile.php?id=12345678") == ""
