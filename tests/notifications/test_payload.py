"""Notification payload tests。"""

from __future__ import annotations

from facebook_monitor.notifications.payload import MatchNotificationFields
from facebook_monitor.notifications.payload import build_compact_notification_body
from facebook_monitor.notifications.payload import build_match_notification_payload


def test_build_match_notification_payload_uses_remote_lines() -> None:
    """命中通知會包含社團、類型、作者、關鍵字、內容與連結。"""

    title, message = build_match_notification_payload(
        MatchNotificationFields(
            group_name="測試社團",
            item_kind="post",
            author="王小明",
            include_rule="票券",
            text="這是一篇有票券關鍵字的貼文",
            permalink="https://www.facebook.com/groups/1/posts/2",
        )
    )

    assert title == "Facebook group match"
    assert message.splitlines() == [
        "社團: 測試社團",
        "類型: 貼文",
        "作者: 王小明",
        "關鍵字: 票券",
        "內容: 這是一篇有票券關鍵字的貼文",
        "連結: https://www.facebook.com/groups/1/posts/2",
    ]


def test_build_match_notification_payload_uses_fallbacks() -> None:
    """缺少欄位時會使用明確 fallback。"""

    title, message = build_match_notification_payload(
        MatchNotificationFields(
            group_name="",
            item_kind="comment",
            author="",
            include_rule="",
            text="",
        )
    )

    assert title == "Facebook group comment match"
    assert "社團: (未知)" in message
    assert "類型: 留言" in message
    assert "作者: (作者未知)" in message
    assert "關鍵字: (未指定)" in message
    assert "內容: (空白)" in message


def test_build_comment_notification_payload_collapses_repeated_text() -> None:
    """留言通知會折疊 Facebook DOM 造成的整段相鄰重複文字。"""

    title, message = build_match_notification_payload(
        MatchNotificationFields(
            group_name="測試社團",
            item_kind="comment",
            author="留言作者",
            include_rule="票券",
            text="這是一則有票券關鍵字的留言 這是一則有票券關鍵字的留言",
            permalink="https://www.facebook.com/groups/1/posts/2/?comment_id=3",
        )
    )

    assert title == "Facebook group comment match"
    assert message.count("這是一則有票券關鍵字的留言") == 1


def test_build_match_notification_payload_preserves_content_newlines() -> None:
    """遠端命中通知保留掃描階段提供的內容換行。"""

    _title, message = build_match_notification_payload(
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
        "社團: 測試社團",
        "類型: 貼文",
        "作者: 王小明",
        "關鍵字: 票券",
        "內容:",
        "第一行票券",
        "第二行座位",
        "連結: https://www.facebook.com/groups/1/posts/2",
    ]


def test_build_compact_notification_body_uses_single_line_segments() -> None:
    """桌面通知 body 維持 compact notification 語義。"""

    body = build_compact_notification_body(
        MatchNotificationFields(
            group_name="測試社團",
            item_kind="post",
            author="王小明",
            include_rule="票券",
            text="這是一篇有票券關鍵字的貼文",
            permalink="https://www.facebook.com/groups/1/posts/2",
        )
    )

    assert body == "社團: 測試社團 | 作者: 王小明 | 關鍵字: 票券 | 內容: 這是一篇有票券關鍵字的貼文"


def test_build_compact_notification_body_collapses_content_newlines() -> None:
    """桌面 compact 通知不因顯示文字換行而變成多行。"""

    body = build_compact_notification_body(
        MatchNotificationFields(
            group_name="測試社團",
            item_kind="post",
            author="王小明",
            include_rule="票券",
            text="第一行票券\n第二行座位",
        )
    )

    assert "\n" not in body
    assert body.endswith("內容: 第一行票券 第二行座位")
