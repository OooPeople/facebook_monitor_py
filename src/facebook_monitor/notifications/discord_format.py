"""Discord 專用通知文字格式化。"""

from __future__ import annotations

import re

from facebook_monitor.core.keyword_rules import split_keyword_rule_text
from facebook_monitor.notifications.payload import MatchNotificationFields
from facebook_monitor.notifications.payload import item_kind_label
from facebook_monitor.notifications.payload import normalize_notification_fields


ANSI_ESCAPE_CHAR = "\x1b"
ANSI_ESCAPE_SEQUENCE_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
DISCORD_MARKDOWN_SPECIAL_CHARS = "\\`*_~|[]()<>"
DISCORD_MATCH_HEADING = "# * Facebook keyword match"
DISCORD_TRAILING_SEPARATOR_LINES = ("```", " ", "```")


def build_discord_match_notification_payload(
    fields: MatchNotificationFields,
) -> tuple[str, str]:
    """建立 Discord match 通知標題與傳統 content 內容。"""

    normalized = normalize_discord_notification_fields(fields)
    # Discord 傳統 content payload 不會顯示 title；保留它是為了通知 outbox
    # 與 sender 共用的 (title, message) 契約。
    title = (
        "Facebook group comment match"
        if normalized.item_kind.lower() == "comment"
        else "Facebook group match"
    )
    lines = [
        DISCORD_MATCH_HEADING,
        f"社團：{normalize_discord_single_line(normalized.group_name)}",
        f"類型：{normalize_discord_single_line(item_kind_label(normalized.item_kind))}",
        f"作者：{normalize_discord_single_line(normalized.author)}",
        f"命中：{format_discord_matched_rule_label(normalized.include_rule)}",
        "",
        format_discord_text_body(normalized.text),
    ]
    if normalized.permalink:
        lines.append("")
        lines.append(format_discord_link_url(normalized.permalink))
    lines.extend(DISCORD_TRAILING_SEPARATOR_LINES)
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
        return " ,  ".join(normalize_discord_single_line(rule) for rule in rules)
    return normalize_discord_single_line(matched_rule)


def format_discord_text_body(text: str) -> str:
    """整理 Discord 內容區文字，保留原文但避免 Markdown 誤觸。"""

    cleaned_text = strip_ansi_escape_sequences(text)
    return escape_discord_markdown(cleaned_text)


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
    """整理 Discord content 中直接顯示且不展開預覽的連結。"""

    url = (
        str(value or "")
        .replace("\\", "%5C")
        .replace(")", "%29")
        .replace(" ", "%20")
        .replace("<", "%3C")
        .replace(">", "%3E")
    )
    return f"<{url}>"
