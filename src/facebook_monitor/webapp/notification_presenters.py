"""Notification status UI presenter type adapter。"""

from __future__ import annotations

from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.user_messages import (
    format_notification_status_label as _format_notification_status_label,
)


def format_notification_status_label(status: NotificationStatus) -> str:
    """回傳通知狀態 UI label。"""

    return _format_notification_status_label(status.value)


__all__ = [
    "format_notification_status_label",
]
