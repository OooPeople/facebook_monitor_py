"""ntfy notification formatter tests。"""

from __future__ import annotations

from facebook_monitor.notifications.ntfy_format import build_ntfy_match_notification_payload
from facebook_monitor.notifications.payload import MatchNotificationFields


def test_ntfy_match_payload_uses_discord_like_body_without_heading() -> None:
    """ntfy match body 使用分隔線格式，標題只放在 Title header。"""

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

    assert title == "🎯 Facebook keyword match"
    assert message.splitlines() == [
        "社團：測試社團",
        "類型：貼文",
        "作者：王小明",
        "命中：6/7 ,  108 ,  熱",
        "---------------------------------------------",
        "這是一篇有票券關鍵字的貼文",
        "---------------------------------------------",
        "https://www.facebook.com/groups/1/posts/2",
    ]
    assert not message.startswith("#")
    assert "# 🎯 Facebook keyword match" not in message
    assert "內容：" not in message
    assert "連結：" not in message
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
        "---------------------------------------------",
        "第一行票券",
        "第二行座位",
        "---------------------------------------------",
        "https://www.facebook.com/groups/1/posts/2",
    ]


def test_ntfy_match_payload_normalizes_permalink_to_single_line() -> None:
    """ntfy body 的 permalink 維持單行，避免破壞分隔線下方格式。"""

    _title, message = build_ntfy_match_notification_payload(
        MatchNotificationFields(
            group_name="測試社團",
            item_kind="post",
            author="王小明",
            include_rule="票券",
            text="第一行票券",
            permalink="https://example.com/a\nb\tc",
        )
    )

    assert message.splitlines()[-2:] == [
        "---------------------------------------------",
        "https://example.com/a b c",
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

    assert title == "🎯 Facebook keyword match"
    assert "社團：(未知)" in message
    assert "類型：留言" in message
    assert "作者：(作者未知)" in message
    assert "命中：(未指定)" in message
    assert "---------------------------------------------\n(空白)" in message
    assert not message.endswith("---------------------------------------------")
