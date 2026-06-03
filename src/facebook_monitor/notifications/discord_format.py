"""Discord 專用通知文字格式化。"""

from __future__ import annotations

from facebook_monitor.core.keyword_highlight import build_highlight_segments
from facebook_monitor.notifications.payload import MatchNotificationFields
from facebook_monitor.notifications.payload import item_kind_label
from facebook_monitor.notifications.payload import normalize_notification_fields


DISCORD_MARKDOWN_SPECIAL_CHARS = "\\`*_~|[]()<>"


def build_discord_match_notification_payload(
    fields: MatchNotificationFields,
) -> tuple[str, str]:
    """建立 Discord match 通知標題與 Text Display 內容。"""

    normalized = normalize_notification_fields(fields)
    title = (
        "Facebook group comment match"
        if normalized.item_kind.lower() == "comment"
        else "Facebook group match"
    )
    lines = [
        f"**社團:** {escape_discord_markdown_line(normalized.group_name)}",
        f"**類型:** {escape_discord_markdown_line(item_kind_label(normalized.item_kind))}",
        f"**作者:** {escape_discord_markdown_line(normalized.author)}",
        f"關鍵字: {escape_discord_markdown_line(normalized.include_rule)}",
        (
            "**內容:** "
            f"{format_discord_highlighted_text(normalized.text, normalized.include_rule)}"
        ),
    ]
    if normalized.permalink:
        lines.append(f"**連結:** [開啟 Facebook 貼文]({escape_discord_link_url(normalized.permalink)})")
    return title, "\n".join(lines)


def format_discord_highlighted_text(text: str, matched_rule: str) -> str:
    """把命中片段轉成 Discord 粗體加底線 Markdown。"""

    segments = build_highlight_segments(text, matched_rule)
    if not segments:
        return escape_discord_markdown(text)
    rendered: list[str] = []
    for segment in segments:
        escaped_text = escape_discord_markdown(segment.text)
        if segment.highlighted:
            rendered.append(f"__**{escaped_text}**__")
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
