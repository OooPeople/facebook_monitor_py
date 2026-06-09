"""桌面通知專用 match notification 摘要格式化。"""

from __future__ import annotations

from facebook_monitor.notifications.payload import MatchNotificationFields
from facebook_monitor.notifications.payload import format_matched_rule_label
from facebook_monitor.notifications.payload import item_kind_label
from facebook_monitor.notifications.payload import normalize_notification_fields
from facebook_monitor.notifications.payload import normalize_notification_single_line
from facebook_monitor.notifications.payload import truncate_text

DESKTOP_GROUP_NAME_MAX_LENGTH = 96
DESKTOP_MATCH_RULE_MAX_LENGTH = 120
DESKTOP_BODY_MAX_LENGTH = 250


def build_compact_notification_segments(fields: MatchNotificationFields) -> list[str]:
    """建立桌面通知使用的短格式片段。"""

    normalized = normalize_notification_fields(fields, preserve_newlines=False)
    group_name = truncate_text(
        normalize_notification_single_line(normalized.group_name),
        DESKTOP_GROUP_NAME_MAX_LENGTH,
    )
    match_rule = truncate_text(
        format_matched_rule_label(normalized.include_rule),
        DESKTOP_MATCH_RULE_MAX_LENGTH,
    )
    return [
        f"社團：{group_name}",
        f"類型：{normalize_notification_single_line(item_kind_label(normalized.item_kind))}",
        f"命中：{match_rule}",
    ]


def build_compact_notification_body(fields: MatchNotificationFields) -> str:
    """建立桌面通知使用的三行短內容。"""

    return truncate_text(
        "\n".join(build_compact_notification_segments(fields)),
        DESKTOP_BODY_MAX_LENGTH,
    )


def build_runtime_failure_compact_notification_message(message: str) -> str:
    """建立所有平台 desktop channel 共用的 runtime failure 短內容。"""

    return " | ".join(line for line in str(message or "").splitlines() if line)
