"""通知共用 payload 欄位與格式語義 helper。

職責：保存 match 通知欄位模型、fallback、標題與命中規則顯示邏輯；
各通道最終文字排版由 channel-specific formatter 負責。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from facebook_monitor.core.keyword_rules import split_keyword_rule_text
from facebook_monitor.facebook.text_cleanup import clean_facebook_text
from facebook_monitor.facebook.text_cleanup import clean_facebook_multiline_text

MISSING_KEYWORD_LABEL = "(未指定)"
MATCH_RULE_DISPLAY_SEPARATOR = " ,  "


@dataclass(frozen=True)
class MatchNotificationFields:
    """保存一次命中通知需要的顯示欄位。"""

    group_name: str
    item_kind: str
    author: str
    include_rule: str
    text: str
    permalink: str = ""


def truncate_text(value: object, max_length: int) -> str:
    """將長文字裁切成固定長度。"""

    text = str(value or "")
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def item_kind_label(item_kind: object) -> str:
    """回傳通知中顯示的 item 類型標籤。"""

    return "留言" if str(item_kind or "").lower() == "comment" else "貼文"


def build_match_notification_title(item_kind: object) -> str:
    """依 item 類型建立 match 通知標題。"""

    return (
        "Facebook group comment match"
        if str(item_kind or "").lower() == "comment"
        else "Facebook group match"
    )


def normalize_notification_single_line(value: object) -> str:
    """將通知 metadata 欄位整理成單行顯示文字。"""

    return " ".join(str(value or "").split())


def format_matched_rule_label(
    matched_rule: object,
    *,
    separator: str = MATCH_RULE_DISPLAY_SEPARATOR,
    item_formatter: Callable[[str], str] | None = None,
) -> str:
    """將儲存格式的 include rule 轉成通知中易掃讀的命中標籤。"""

    formatter = item_formatter or normalize_notification_single_line
    rules = split_keyword_rule_text(str(matched_rule or ""))
    if rules:
        return separator.join(formatter(rule) for rule in rules)
    return formatter(str(matched_rule or ""))


def normalize_notification_fields(
    fields: MatchNotificationFields,
    *,
    preserve_newlines: bool = False,
) -> MatchNotificationFields:
    """套用通知欄位的 fallback 與長度限制。"""

    cleaned_text = (
        clean_facebook_multiline_text(fields.text)
        if preserve_newlines
        else clean_facebook_text(fields.text)
    )
    return MatchNotificationFields(
        group_name=fields.group_name or "(未知)",
        item_kind=str(fields.item_kind or "post"),
        author=fields.author or "(作者未知)",
        include_rule=fields.include_rule or MISSING_KEYWORD_LABEL,
        text=truncate_text(cleaned_text, 220) or "(空白)",
        permalink=fields.permalink,
    )
