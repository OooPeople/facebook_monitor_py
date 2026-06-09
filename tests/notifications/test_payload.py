"""Notification payload shared helper tests。"""

from __future__ import annotations

from facebook_monitor.notifications.payload import MatchNotificationFields
from facebook_monitor.notifications.payload import build_match_notification_title
from facebook_monitor.notifications.payload import format_matched_rule_label
from facebook_monitor.notifications.payload import normalize_notification_fields


def test_build_match_notification_title_uses_item_kind() -> None:
    """match 標題由共用 helper 依 item kind 決定。"""

    assert build_match_notification_title("post") == "Facebook group match"
    assert build_match_notification_title("comment") == "Facebook group comment match"


def test_format_matched_rule_label_splits_stored_rule_text() -> None:
    """命中規則顯示使用共用分隔符號，避免各通道分歧。"""

    assert format_matched_rule_label("6/7;108;熱") == "6/7 ,  108 ,  熱"


def test_format_matched_rule_label_supports_channel_specific_item_formatter() -> None:
    """通道可在共用 split 邏輯上套用自己的單項格式化。"""

    assert (
        format_matched_rule_label(
            "A_B;C*D",
            item_formatter=lambda value: value.replace("_", "\\_").replace("*", "\\*"),
        )
        == "A\\_B ,  C\\*D"
    )


def test_normalize_notification_fields_uses_fallbacks() -> None:
    """缺少欄位時會使用明確 fallback。"""

    normalized = normalize_notification_fields(
        MatchNotificationFields(
            group_name="",
            item_kind="comment",
            author="",
            include_rule="",
            text="",
        ),
        preserve_newlines=True,
    )

    assert normalized.group_name == "(未知)"
    assert normalized.item_kind == "comment"
    assert normalized.author == "(作者未知)"
    assert normalized.include_rule == "(未指定)"
    assert normalized.text == "(空白)"


def test_normalize_notification_fields_preserves_content_newlines() -> None:
    """保留換行時仍套用 Facebook 文字清理與重複段落折疊。"""

    normalized = normalize_notification_fields(
        MatchNotificationFields(
            group_name="測試社團",
            item_kind="post",
            author="王小明",
            include_rule="票券",
            text="第一行票券\n第二行座位\n第一行票券\n第二行座位",
        ),
        preserve_newlines=True,
    )

    assert normalized.text == "第一行票券\n第二行座位"
