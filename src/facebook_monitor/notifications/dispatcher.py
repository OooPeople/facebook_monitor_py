"""通知 façade exports。

職責：保留通知子系統的穩定匯入面；實作分散於 `channel_dispatch`、
`outbox_service` 與 `manual_test`。
"""

from __future__ import annotations

from facebook_monitor.notifications.channel_dispatch import DesktopSender
from facebook_monitor.notifications.channel_dispatch import DiscordSender
from facebook_monitor.notifications.channel_dispatch import NOTIFICATION_CHANNEL_DEFINITIONS
from facebook_monitor.notifications.channel_dispatch import NotificationChannelDefinition
from facebook_monitor.notifications.channel_dispatch import NtfySender
from facebook_monitor.notifications.channel_dispatch import is_channel_enabled
from facebook_monitor.notifications.manual_test import send_manual_test_notification
from facebook_monitor.notifications.outbox_service import build_match_compact_notification_message
from facebook_monitor.notifications.outbox_service import build_match_notification_message
from facebook_monitor.notifications.outbox_service import build_notification_idempotency_key
from facebook_monitor.notifications.outbox_service import dispatch_new_pending_notification_outbox
from facebook_monitor.notifications.outbox_service import dispatch_notification_outbox_entries
from facebook_monitor.notifications.outbox_service import enqueue_match_notifications
from facebook_monitor.notifications.outbox_service import queue_match_notifications_after_commit
from facebook_monitor.notifications.outbox_service import recover_stale_processing_outbox
from facebook_monitor.notifications.outbox_service import retry_failed_notification_outbox


__all__ = [
    "DesktopSender",
    "DiscordSender",
    "NOTIFICATION_CHANNEL_DEFINITIONS",
    "NotificationChannelDefinition",
    "NtfySender",
    "build_match_compact_notification_message",
    "build_match_notification_message",
    "build_notification_idempotency_key",
    "dispatch_new_pending_notification_outbox",
    "dispatch_notification_outbox_entries",
    "enqueue_match_notifications",
    "is_channel_enabled",
    "queue_match_notifications_after_commit",
    "recover_stale_processing_outbox",
    "retry_failed_notification_outbox",
    "send_manual_test_notification",
]
