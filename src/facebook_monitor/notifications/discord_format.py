"""Discord 專用通知文字格式化。"""

from __future__ import annotations

import re

from facebook_monitor.core.keyword_highlight import build_highlight_segments
from facebook_monitor.core.keyword_rules import split_keyword_rule_text
from facebook_monitor.notifications.payload import MatchNotificationFields
from facebook_monitor.notifications.payload import item_kind_label
from facebook_monitor.notifications.payload import normalize_notification_fields


ANSI_ESCAPE_CHAR = "\x1b"
ANSI_ESCAPE_SEQUENCE_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
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
        f"社團：{normalize_discord_single_line(normalized.group_name)}",
        f"類型：{normalize_discord_single_line(item_kind_label(normalized.item_kind))}",
        f"作者：{normalize_discord_single_line(normalized.author)}",
        f"命中：{format_discord_matched_rule_label(normalized.include_rule)}",
        "",
        format_discord_highlighted_text_body(normalized.text, normalized.include_rule),
    ]
    if normalized.permalink:
        lines.append("")
        lines.append(format_discord_link_url(normalized.permalink))
    return title, "\n".join(lines)


def normalize_discord_notification_fields(
    fields: MatchNotificationFields,
) -> MatchNotificationFields:
    """套用 Discord 專用 fallback，內容保留原始換行。"""

    return normalize_notification_fields(fields, preserve_newlines=True)


def format_discord_matched_rule_label(matched_rule: str) -> str:
    """把分號儲存格式轉成 Discord 較易掃讀的顯示文字。"""

    rules = split_keyword_rule_text(matched_rule)
    if rules:
        return "  ,  ".join(normalize_discord_single_line(rule) for rule in rules)
    return normalize_discord_single_line(matched_rule)


def format_discord_highlighted_text_body(text: str, matched_rule: str) -> str:
    """把內容區命中片段轉成 Discord 粗體 Markdown。"""

    cleaned_text = strip_ansi_escape_sequences(text)
    segments = build_highlight_segments(cleaned_text, matched_rule)
    if not segments:
        return escape_discord_markdown(cleaned_text)
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


def strip_ansi_escape_sequences(value: object) -> str:
    """移除原文 ANSI 控制碼，避免污染 Discord content。"""

    text = ANSI_ESCAPE_SEQUENCE_PATTERN.sub("", str(value or ""))
    return text.replace(ANSI_ESCAPE_CHAR, "")


def format_discord_link_url(value: object) -> str:
    """整理 Discord content 中直接顯示的連結。"""

    return str(value or "").replace("\\", "%5C").replace(")", "%29").replace(" ", "%20")
