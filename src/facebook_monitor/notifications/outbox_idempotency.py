"""Notification outbox idempotency key helpers。"""

from __future__ import annotations

from facebook_monitor.core.models import NotificationChannel


def build_notification_idempotency_key(
    *,
    target_id: str,
    item_key: str,
    channel: NotificationChannel,
) -> str:
    """建立通知 outbox 去重 key，避免同一事件與 channel 重複發送。"""

    return f"{target_id}:{item_key}:{channel.value}"
