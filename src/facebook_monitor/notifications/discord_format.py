"""Discord 專用通知文字格式化。"""

from __future__ import annotations

from facebook_monitor.core.keyword_highlight import build_highlight_segments
from facebook_monitor.core.keyword_rules import split_keyword_rule_text
from facebook_monitor.facebook.text_cleanup import collapse_repeated_adjacent_text
from facebook_monitor.facebook.text_cleanup import strip_facebook_expand_collapse_labels
from facebook_monitor.notifications.payload import MatchNotificationFields
from facebook_monitor.notifications.payload import item_kind_label
from facebook_monitor.notifications.payload import MISSING_KEYWORD_LABEL
from facebook_monitor.notifications.payload import truncate_text


DISCORD_MARKDOWN_SPECIAL_CHARS = "\\`*_~|[]()<>"
DISCORD_CONTENT_PREVIEW_LIMIT = 220


def build_discord_match_notification_payload(
    fields: MatchNotificationFields,
) -> tuple[str, str]:
    """建立 Discord match 通知標題與 Text Display 內容。"""

    normalized = normalize_discord_notification_fields(fields)
    title = (
        "Facebook group comment match"
        if normalized.item_kind.lower() == "comment"
        else "Facebook group match"
    )
    lines = [
        f"**社團:** {escape_discord_markdown_line(normalized.group_name)}",
        f"**類型:** {escape_discord_markdown_line(item_kind_label(normalized.item_kind))}",
        f"**作者:** {escape_discord_markdown_line(normalized.author)}",
        f"**命中:** {format_discord_matched_rule_label(normalized.include_rule)}",
        "",
        "**內容:**",
        format_discord_highlighted_text(normalized.text, normalized.include_rule),
    ]
    if normalized.permalink:
        lines.extend(
            (
                "",
                f"[開啟連結]({escape_discord_link_url(normalized.permalink)})",
            )
        )
    return title, "\n".join(lines)


def normalize_discord_notification_fields(
    fields: MatchNotificationFields,
) -> MatchNotificationFields:
    """套用 Discord 專用 fallback，內容保留原始換行。"""

    return MatchNotificationFields(
        group_name=fields.group_name or "(未知)",
        item_kind=str(fields.item_kind or "post"),
        author=fields.author or "(作者未知)",
        include_rule=fields.include_rule or MISSING_KEYWORD_LABEL,
        text=normalize_discord_content_text(fields.text),
        permalink=fields.permalink,
    )


def normalize_discord_content_text(value: object) -> str:
    """清理通知內容但保留換行，供 Discord Text Display 使用。"""

    raw_text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned_lines = [
        collapse_repeated_adjacent_text(strip_facebook_expand_collapse_labels(line))
        for line in raw_text.split("\n")
    ]
    text = truncate_text("\n".join(cleaned_lines).strip(), DISCORD_CONTENT_PREVIEW_LIMIT)
    return text or "(空白)"


def format_discord_matched_rule_label(matched_rule: str) -> str:
    """把分號儲存格式轉成 Discord 較易掃讀的顯示文字。"""

    rules = split_keyword_rule_text(matched_rule)
    label = " · ".join(rules) if rules else str(matched_rule or "")
    return escape_discord_markdown_line(label)


def format_discord_highlighted_text(text: str, matched_rule: str) -> str:
    """把命中片段轉成 Discord 粗體 Markdown。"""

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


def escape_discord_markdown_line(value: object) -> str:
    """把欄位值整理成單行並 escape Discord Markdown。"""

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
