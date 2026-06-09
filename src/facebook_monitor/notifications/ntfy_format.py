"""ntfy 專用 match notification 文字格式化。"""

from __future__ import annotations

from facebook_monitor.notifications.payload import MatchNotificationFields
from facebook_monitor.notifications.payload import build_match_notification_title
from facebook_monitor.notifications.payload import format_matched_rule_label
from facebook_monitor.notifications.payload import item_kind_label
from facebook_monitor.notifications.payload import normalize_notification_fields
from facebook_monitor.notifications.payload import normalize_notification_single_line


def build_ntfy_match_notification_payload(
    fields: MatchNotificationFields,
) -> tuple[str, str]:
    """建立 ntfy match 通知標題與純文字內容。"""

    normalized = normalize_notification_fields(fields, preserve_newlines=True)
    title = build_match_notification_title(normalized.item_kind)
    return title, "\n".join(_format_ntfy_match_notification_lines(normalized))


def build_ntfy_match_notification_lines(fields: MatchNotificationFields) -> list[str]:
    """建立 ntfy 文字內容；保留推播友善格式，不套用 Discord 樣式。"""

    normalized = normalize_notification_fields(fields, preserve_newlines=True)
    return _format_ntfy_match_notification_lines(normalized)


def _format_ntfy_match_notification_lines(fields: MatchNotificationFields) -> list[str]:
    """依已正規化欄位組出 ntfy 文字行。"""

    lines = [
        f"社團：{normalize_notification_single_line(fields.group_name)}",
        f"類型：{normalize_notification_single_line(item_kind_label(fields.item_kind))}",
        f"作者：{normalize_notification_single_line(fields.author)}",
        f"命中：{format_matched_rule_label(fields.include_rule)}",
    ]
    if "\n" in fields.text:
        lines.append("內容：")
        lines.extend(fields.text.split("\n"))
    else:
        lines.append(f"內容：{fields.text}")
    if fields.permalink:
        lines.append(f"連結：{fields.permalink}")
    return lines
