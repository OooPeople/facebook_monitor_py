"""ntfy 專用 match notification 文字格式化。"""

from __future__ import annotations

from facebook_monitor.notifications.payload import MatchNotificationFields
from facebook_monitor.notifications.payload import format_matched_rule_label
from facebook_monitor.notifications.payload import item_kind_label
from facebook_monitor.notifications.payload import normalize_notification_fields
from facebook_monitor.notifications.payload import normalize_notification_single_line

NTFY_MATCH_TITLE = "🎯 Facebook keyword match"
NTFY_CONTENT_SEPARATOR = "---------------------------------------------"


def build_ntfy_match_notification_payload(
    fields: MatchNotificationFields,
) -> tuple[str, str]:
    """建立 ntfy match 通知標題與純文字內容。"""

    normalized = normalize_notification_fields(fields, preserve_newlines=True)
    return NTFY_MATCH_TITLE, "\n".join(_format_ntfy_match_notification_lines(normalized))


def build_ntfy_match_notification_lines(fields: MatchNotificationFields) -> list[str]:
    """建立 ntfy 文字內容；使用分隔線凸顯 metadata、正文與連結。"""

    normalized = normalize_notification_fields(fields, preserve_newlines=True)
    return _format_ntfy_match_notification_lines(normalized)


def _format_ntfy_match_notification_lines(fields: MatchNotificationFields) -> list[str]:
    """依已正規化欄位組出 ntfy 文字行。"""

    lines = [
        f"社團：{normalize_notification_single_line(fields.group_name)}",
        f"類型：{normalize_notification_single_line(item_kind_label(fields.item_kind))}",
        f"作者：{normalize_notification_single_line(fields.author)}",
        f"命中：{format_matched_rule_label(fields.include_rule)}",
        NTFY_CONTENT_SEPARATOR,
    ]
    lines.extend(fields.text.split("\n"))
    if fields.permalink:
        lines.append(NTFY_CONTENT_SEPARATOR)
        lines.append(normalize_notification_single_line(fields.permalink))
    return lines
