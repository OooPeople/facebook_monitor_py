"""通知 payload 組裝邏輯。

職責：集中建立本機 worker 要送出的通知標題與內容，維持遠端通知欄位
的穩定格式。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.facebook.text_cleanup import clean_facebook_text

MISSING_KEYWORD_LABEL = "(未指定)"


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


def normalize_notification_fields(fields: MatchNotificationFields) -> MatchNotificationFields:
    """套用通知欄位的 fallback 與長度限制。"""

    return MatchNotificationFields(
        group_name=fields.group_name or "(未知)",
        item_kind=str(fields.item_kind or "post"),
        author=fields.author or "(作者未知)",
        include_rule=fields.include_rule or MISSING_KEYWORD_LABEL,
        text=truncate_text(clean_facebook_text(fields.text), 220) or "(空白)",
        permalink=fields.permalink,
    )


def build_remote_notification_lines(fields: MatchNotificationFields) -> list[str]:
    """建立遠端通知使用的多行內容。"""

    normalized = normalize_notification_fields(fields)
    lines = [
        f"社團: {normalized.group_name}",
        f"類型: {item_kind_label(normalized.item_kind)}",
        f"作者: {normalized.author}",
        f"關鍵字: {normalized.include_rule}",
        f"內容: {normalized.text}",
    ]
    if normalized.permalink:
        lines.append(f"連結: {normalized.permalink}")
    return lines


def build_compact_notification_segments(fields: MatchNotificationFields) -> list[str]:
    """建立桌面通知使用的短格式片段。"""

    normalized = normalize_notification_fields(fields)
    return [
        f"社團: {normalized.group_name}",
        f"作者: {normalized.author}",
        f"關鍵字: {normalized.include_rule}",
        f"內容: {normalized.text}",
    ]


def build_compact_notification_body(fields: MatchNotificationFields) -> str:
    """建立桌面通知使用的單行短內容。"""

    return truncate_text(" | ".join(build_compact_notification_segments(fields)), 250)


def build_match_notification_payload(fields: MatchNotificationFields) -> tuple[str, str]:
    """建立 keyword match 通知標題與多行內容。"""

    normalized = normalize_notification_fields(fields)
    title = (
        "Facebook group comment match"
        if normalized.item_kind.lower() == "comment"
        else "Facebook group match"
    )
    return title, "\n".join(build_remote_notification_lines(normalized))
