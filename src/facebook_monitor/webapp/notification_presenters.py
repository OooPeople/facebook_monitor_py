"""Notification UI presenter helpers。

職責：集中 Web UI 使用的通知通道顯示名稱，避免 dashboard 與設定摘要
各自維護一套 label。
"""

from __future__ import annotations

from facebook_monitor.core.models import NotificationChannel


def format_notification_channel_label(channel: NotificationChannel) -> str:
    """回傳通知通道 UI label。"""

    labels = {
        NotificationChannel.DESKTOP: "桌面",
        NotificationChannel.NTFY: "ntfy",
        NotificationChannel.DISCORD: "Discord",
    }
    return labels.get(channel, channel.value)


__all__ = ["format_notification_channel_label"]
