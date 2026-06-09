"""ntfy notification formatter tests。"""

from __future__ import annotations

from facebook_monitor.notifications.ntfy_format import build_ntfy_match_notification_payload
from facebook_monitor.notifications.payload import MatchNotificationFields


def test_ntfy_match_payload_uses_plain_text_layout() -> None:
    """ntfy match 使用推播友善純文字格式，不套 Discord 樣式。"""

    title, message = build_ntfy_match_notification_payload(
        MatchNotificationFields(
            group_name="測試社團",
            item_kind="post",
            author="王小明",
            include_rule="6/7;108;熱",
            text="這是一篇有票券關鍵字的貼文",
            permalink="https://www.facebook.com/groups/1/posts/2",
        )
    )

    assert title == "Facebook group match"
    assert message.splitlines() == [
        "社團：測試社團",
        "類型：貼文",
        "作者：王小明",
        "命中：6/7 ,  108 ,  熱",
        "內容：這是一篇有票券關鍵字的貼文",
        "連結：https://www.facebook.com/groups/1/posts/2",
    ]
    assert "# * Facebook keyword match" not in message
    assert "---------------------------------------------" not in message
    assert "<https://www.facebook.com/groups/1/posts/2>" not in message


def test_ntfy_match_payload_preserves_content_newlines() -> None:
    """ntfy 內容保留掃描階段提供的正文換行。"""

    _title, message = build_ntfy_match_notification_payload(
        MatchNotificationFields(
            group_name="測試社團",
            item_kind="post",
            author="王小明",
            include_rule="票券",
            text="第一行票券\n第二行座位",
            permalink="https://www.facebook.com/groups/1/posts/2",
        )
    )

    assert message.splitlines() == [
        "社團：測試社團",
        "類型：貼文",
        "作者：王小明",
        "命中：票券",
        "內容：",
        "第一行票券",
        "第二行座位",
        "連結：https://www.facebook.com/groups/1/posts/2",
    ]


def test_ntfy_match_payload_uses_fallbacks() -> None:
    """ntfy formatter 沿用共用 fallback 語義。"""

    title, message = build_ntfy_match_notification_payload(
        MatchNotificationFields(
            group_name="",
            item_kind="comment",
            author="",
            include_rule="",
            text="",
        )
    )

    assert title == "Facebook group comment match"
    assert "社團：(未知)" in message
    assert "類型：留言" in message
    assert "作者：(作者未知)" in message
    assert "命中：(未指定)" in message
    assert "內容：(空白)" in message
