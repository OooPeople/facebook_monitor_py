"""Desktop notification formatter tests。"""

from __future__ import annotations

from facebook_monitor.notifications.desktop_format import build_compact_notification_body
from facebook_monitor.notifications.payload import MatchNotificationFields


def test_build_compact_notification_body_uses_desktop_summary_lines() -> None:
    """桌面通知 body 只保留社團、類型與命中摘要。"""

    body = build_compact_notification_body(
        MatchNotificationFields(
            group_name="測試社團",
            item_kind="post",
            author="王小明",
            include_rule="6/7;108;熱",
            text="這是一篇有票券關鍵字的貼文",
            permalink="https://www.facebook.com/groups/1/posts/2",
        )
    )

    assert body.splitlines() == [
        "社團：測試社團",
        "類型：貼文",
        "命中：6/7 ,  108 ,  熱",
    ]


def test_build_compact_notification_body_ignores_content_newlines() -> None:
    """桌面摘要不放正文內容，避免長貼文擠壓 banner。"""

    body = build_compact_notification_body(
        MatchNotificationFields(
            group_name="測試社團",
            item_kind="post",
            author="王小明",
            include_rule="票券",
            text="第一行票券\n第二行座位",
        )
    )

    assert body.splitlines() == [
        "社團：測試社團",
        "類型：貼文",
        "命中：票券",
    ]


def test_build_compact_notification_body_keeps_required_lines_when_fields_are_long() -> None:
    """長欄位只截斷欄位本身，不可擠掉桌面通知必要摘要行。"""

    body = build_compact_notification_body(
        MatchNotificationFields(
            group_name="超長社團名稱" * 40,
            item_kind="comment",
            author="王小明",
            include_rule=";".join(f"關鍵字{index}" for index in range(50)),
            text="這是一篇有票券關鍵字的貼文",
        )
    )

    lines = body.splitlines()

    assert len(body) <= 250
    assert len(lines) == 3
    assert lines[0].startswith("社團：超長社團名稱")
    assert lines[0].endswith("...")
    assert lines[1] == "類型：留言"
    assert lines[2].startswith("命中：關鍵字0 ,  關鍵字1")
    assert lines[2].endswith("...")
