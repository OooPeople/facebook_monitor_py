"""Discord 專用通知文字格式化。"""

from __future__ import annotations

from facebook_monitor.core.keyword_highlight import build_highlight_segments
from facebook_monitor.core.keyword_rules import split_keyword_rule_text
from facebook_monitor.notifications.payload import MatchNotificationFields
from facebook_monitor.notifications.payload import item_kind_label
from facebook_monitor.notifications.payload import normalize_notification_fields


DISCORD_MESSAGE_SEPARATOR = "-" * 40
DISCORD_MARKDOWN_SPECIAL_CHARS = "\\`*_~|[]()<>"


def build_discord_match_notification_payload(
    fields: MatchNotificationFields,
) -> tuple[str, str]:
    """建立 Discord match 通知標題與傳統 content 內容。"""

    normalized = normalize_discord_notification_fields(fields)
    title = (
        "Facebook group comment match"
        if normalized.item_kind.lower() == "comment"
        else "Facebook group match"
    )
    lines = [
        f"社團: {normalize_discord_single_line(normalized.group_name)}",
        f"類型: {normalize_discord_single_line(item_kind_label(normalized.item_kind))}",
        f"作者: {normalize_discord_single_line(normalized.author)}",
        f"命中: {format_discord_matched_rule_label(normalized.include_rule)}",
        "",
        format_discord_highlighted_text(normalized.text, normalized.include_rule),
    ]
    if normalized.permalink:
        lines.extend(
            (
                "",
                f"[開啟連結]({escape_discord_link_url(normalized.permalink)})",
            )
        )
    lines.append(DISCORD_MESSAGE_SEPARATOR)
    return title, "\n".join(lines)


def normalize_discord_notification_fields(
    fields: MatchNotificationFields,
) -> MatchNotificationFields:
    """套用 Discord 專用 fallback，內容保留原始換行。"""

    return normalize_notification_fields(fields, preserve_newlines=True)


def format_discord_matched_rule_label(matched_rule: str) -> str:
    """把分號儲存格式轉成 Discord 較易掃讀的顯示文字。"""

    rules = split_keyword_rule_text(matched_rule)
    label = " · ".join(rules) if rules else str(matched_rule or "")
    return normalize_discord_single_line(label)


def format_discord_highlighted_text(text: str, matched_rule: str) -> str:
    """把內容區命中片段轉成 Discord 粗體 Markdown。"""

    segments = build_highlight_segments(text, matched_rule)
    if not segments:
        return escape_discord_markdown(text)
    rendered: list[str] = []
    for segment in segments:
        escaped_text = escape_discord_markdown(segment.text)
        if segment.highlighted:
            rendered.append(f"**{escaped_text}**")
            continue
        rendered.append(escaped_text)
    return "".join(rendered)


def normalize_discord_single_line(value: object) -> str:
    """把欄位值整理成單行，避免 metadata 破壞固定行格式。"""

    return escape_discord_markdown(" ".join(str(value or "").split()))


def escape_discord_markdown(value: object) -> str:
    """Escape 會影響 Discord Markdown 結構的字元。"""

    escaped: list[str] = []
    for char in str(value or ""):
        if char in DISCORD_MARKDOWN_SPECIAL_CHARS:
            escaped.append(f"\\{char}")
            continue
        escaped.append(char)
    return "".join(escaped)


def escape_discord_link_url(value: object) -> str:
    """整理 Discord masked link target，避免右括號截斷連結。"""

    return str(value or "").replace("\\", "%5C").replace(")", "%29").replace(" ", "%20")
