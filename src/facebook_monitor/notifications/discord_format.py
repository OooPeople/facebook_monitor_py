"""Discord 專用通知文字格式化。"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

from facebook_monitor.core.keyword_rules import split_keyword_rule_text
from facebook_monitor.notifications.payload import MatchNotificationFields
from facebook_monitor.notifications.payload import item_kind_label
from facebook_monitor.notifications.payload import normalize_notification_fields


ANSI_ESCAPE_CHAR = "\x1b"
ANSI_ESCAPE_SEQUENCE_PATTERN = re.compile(
    r"\x1b\][\s\S]*?(?:\x07|\x1b\\)|"
    r"\x1b[PX^_][\s\S]*?(?:\x07|\x1b\\)|"
    r"\x1b[()][0-2AB]|"
    r"\x1b[78]|"
    r"\x1b\[[0-?]*[ -/]*[@-~]|"
    r"\x9d[\s\S]*?(?:\x07|\x9c|\x1b\\)|"
    r"[\x90\x98\x9e\x9f][\s\S]*?(?:\x9c|\x1b\\)|"
    r"\x9b[0-?]*[ -/]*[@-~]"
)
RESIDUAL_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
DISCORD_MARKDOWN_SPECIAL_CHARS = "\\`*_~|[]()<>"
DISCORD_LINE_START_MARKDOWN_PATTERN = re.compile(
    r"(?m)^([ \t]*)(?:(-#)(?=\s)|(#+)(?=\s)|([-+])(?=\s)|(\d+)(\.)(?=\s))"
)
DISCORD_MATCH_HEADING = "# * Facebook keyword match"
DISCORD_CONTENT_SEPARATOR = "---------------------------------------------"


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
        DISCORD_CONTENT_SEPARATOR,
        format_discord_text_body(normalized.text),
    ]
    if normalized.permalink:
        lines.append(DISCORD_CONTENT_SEPARATOR)
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
        return " ,  ".join(normalize_discord_single_line(rule) for rule in rules)
    return normalize_discord_single_line(matched_rule)


def format_discord_text_body(text: str) -> str:
    """整理 Discord 內容區文字，保留原文但避免 Markdown 誤觸。"""

    cleaned_text = strip_ansi_escape_sequences(text)
    return escape_discord_markdown(cleaned_text)


def normalize_discord_single_line(value: object) -> str:
    """把欄位值整理成單行，避免 metadata 破壞固定行格式。"""

    cleaned_text = strip_ansi_escape_sequences(value)
    return escape_discord_markdown(" ".join(cleaned_text.split()))


def escape_discord_markdown(value: object) -> str:
    """Escape 會影響 Discord Markdown 結構的字元。"""

    escaped: list[str] = []
    for char in str(value or ""):
        if char in DISCORD_MARKDOWN_SPECIAL_CHARS:
            escaped.append(f"\\{char}")
            continue
        escaped.append(char)
    return escape_discord_line_start_markdown("".join(escaped))


def escape_discord_line_start_markdown(value: str) -> str:
    """Escape Discord 行首 heading/list Markdown 結構。"""

    def replace_match(match: re.Match[str]) -> str:
        prefix = match.group(1)
        subtext_marker = match.group(2)
        heading = match.group(3)
        unordered_marker = match.group(4)
        ordered_number = match.group(5)
        if subtext_marker:
            return f"{prefix}\\{subtext_marker}"
        if heading:
            return f"{prefix}\\{heading}"
        if unordered_marker:
            return f"{prefix}\\{unordered_marker}"
        return f"{prefix}{ordered_number}\\."

    return DISCORD_LINE_START_MARKDOWN_PATTERN.sub(replace_match, value)


def strip_ansi_escape_sequences(value: object) -> str:
    """移除原文 ANSI 控制碼，避免污染 Discord content。"""

    text = ANSI_ESCAPE_SEQUENCE_PATTERN.sub("", str(value or ""))
    text = text.replace(ANSI_ESCAPE_CHAR, "")
    return RESIDUAL_CONTROL_CHAR_PATTERN.sub("", text)


def format_discord_link_url(value: object) -> str:
    """整理 Discord content 中直接顯示且不展開預覽的連結。"""

    url = "".join(format_discord_link_url_char(char) for char in str(value or ""))
    return f"<{url}>"


def format_discord_link_url_char(char: str) -> str:
    """整理會破壞 Discord angle-wrapped URL 的單一字元。"""

    if (
        char in {"\\", ")", "<", ">"}
        or char.isspace()
        or unicodedata.category(char) == "Cc"
    ):
        return quote(char, safe="")
    return char
