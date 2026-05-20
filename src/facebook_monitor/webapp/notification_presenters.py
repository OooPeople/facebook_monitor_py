"""Notification UI presenter helpers。

職責：集中 Web UI 使用的通知通道顯示名稱，避免 dashboard 與設定摘要
各自維護一套 label。
"""

from __future__ import annotations

from facebook_monitor.core.notification_channels import (
    format_notification_channel_label as _format_notification_channel_label,
)
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.user_messages import (
    format_notification_event_message as _format_notification_event_message,
)
from facebook_monitor.core.user_messages import (
    format_notification_status_label as _format_notification_status_label,
)


def format_notification_channel_label(channel: NotificationChannel) -> str:
    """回傳通知通道 UI label。"""

    return _format_notification_channel_label(channel)


def format_notification_status_label(status: NotificationStatus) -> str:
    """回傳通知狀態 UI label。"""

    return _format_notification_status_label(status.value)


def format_notification_event_message(value: str) -> str:
    """回傳 notification event message 的 UI 摘要。"""

    return _format_notification_event_message(value)


__all__ = [
    "format_notification_channel_label",
    "format_notification_event_message",
    "format_notification_status_label",
]
