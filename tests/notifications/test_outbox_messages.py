"""Notification outbox message name tests。"""

from __future__ import annotations

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.notifications.outbox_service import (
    build_match_compact_notification_message,
)
from facebook_monitor.notifications.outbox_service import build_match_discord_notification_message
from facebook_monitor.notifications.outbox_service import build_match_notification_message
from facebook_monitor.notifications.outbox_service import (
    build_runtime_failure_notification_message,
)


def test_match_notification_uses_user_facing_target_name() -> None:
    """命中通知使用 target 顯示名稱，不優先拿 Facebook metadata 名稱。"""

    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
        name="我的自訂名稱",
        group_name="測試社團",
    )

    _title, message = build_match_notification_message(
        target=target,
        author="王小明",
        item_text="票券貼文",
        permalink="https://www.facebook.com/groups/222518561920110/posts/1",
        matched_keyword="票券",
    )
    compact = build_match_compact_notification_message(
        target=target,
        author="王小明",
        item_text="票券貼文",
        permalink="https://www.facebook.com/groups/222518561920110/posts/1",
        matched_keyword="票券",
    )

    assert "社團: 我的自訂名稱" in message
    assert "社團: 測試社團" not in message
    assert "社團: 我的自訂名稱" in compact
    assert "社團: 測試社團" not in compact


def test_comment_match_notification_preserves_comment_target_display_scope() -> None:
    """comments target 沒有自訂名時，通知名稱保留 parent post scope。"""

    target = TargetDescriptor.for_comments(
        group_id="222518561920110",
        parent_post_id="2187454285426518",
        canonical_url=(
            "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
        ),
        group_name="(20+) 測試社團 | Facebook",
    )

    _title, message = build_match_notification_message(
        target=target,
        item_kind=ItemKind.COMMENT,
        author="王小明",
        item_text="票券留言",
        permalink=(
            "https://www.facebook.com/groups/222518561920110/posts/"
            "2187454285426518?comment_id=1"
        ),
        matched_keyword="票券",
    )

    assert "社團: 測試社團 / post:2187454285426518" in message
    assert "(20+)" not in message


def test_discord_match_notification_uses_channel_specific_content_format() -> None:
    """Discord match message 有專用 content 格式，不污染共用 remote payload。"""

    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
        name="測試社團",
    )

    _remote_title, remote_message = build_match_notification_message(
        target=target,
        author="王小明",
        item_text="售6/3內野118區票券",
        permalink="https://www.facebook.com/groups/222518561920110/posts/1",
        matched_keyword="6/3;118",
    )
    _discord_title, discord_message = build_match_discord_notification_message(
        target=target,
        author="王小明",
        item_text="售6/3內野118區票券",
        permalink="https://www.facebook.com/groups/222518561920110/posts/1",
        matched_keyword="6/3;118",
    )

    assert "內容: 售6/3內野118區票券" in remote_message
    assert "**6/3**" not in remote_message
    assert "**118**" not in remote_message
    assert discord_message.startswith("# Facebook keyword match\n社團：")
    assert "命中：6/3  ,  118" in discord_message
    assert "售**6/3**內野**118**區票券" in discord_message
    assert "https://www.facebook.com/groups/222518561920110/posts/1" in (
        discord_message
    )
    assert "命中：6/3  ,  118\n\n售**6/3**內野**118**區票券" in discord_message
    assert "票券\n\n<https://www.facebook.com/groups/222518561920110/posts/1>" in (
        discord_message
    )
    assert "內容:" not in discord_message
    assert "社團:" not in discord_message
    assert "關鍵字:" not in discord_message
    assert "連結:" not in discord_message
    assert "開啟連結：" not in discord_message
    assert "[開啟連結]" not in discord_message
    assert "━" not in discord_message
    assert "```" not in discord_message
    assert "\x1b" not in remote_message
    assert "\x1b" not in discord_message


def test_match_notification_message_preserves_remote_content_newlines() -> None:
    """共用遠端 match message 使用掃描提供的多行顯示文字。"""

    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
        name="測試社團",
    )

    _title, message = build_match_notification_message(
        target=target,
        author="王小明",
        item_text="第一行票券\n第二行座位",
        permalink="https://www.facebook.com/groups/222518561920110/posts/1",
        matched_keyword="票券",
    )
    compact = build_match_compact_notification_message(
        target=target,
        author="王小明",
        item_text="第一行票券\n第二行座位",
        permalink="https://www.facebook.com/groups/222518561920110/posts/1",
        matched_keyword="票券",
    )

    assert "內容:\n第一行票券\n第二行座位" in message
    assert "\n" not in compact
    assert "內容: 第一行票券 第二行座位" in compact


def test_runtime_failure_notification_uses_clean_target_display_name() -> None:
    """runtime failure 通知也沿用共用 target 顯示名稱。"""

    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
        name="(20+) 我的自訂名稱 | Facebook",
        group_name="測試社團",
    )

    _title, message = build_runtime_failure_notification_message(
        target=target,
        reason="unknown",
        failure_count=3,
        error_message="背景掃描錯誤",
    )

    assert "監視項目: 我的自訂名稱" in message
    assert "監視項目: 測試社團" not in message
    assert "(20+)" not in message
