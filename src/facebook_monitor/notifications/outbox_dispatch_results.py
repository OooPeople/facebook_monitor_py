"""Notification outbox dispatch result mapping helpers。"""

from __future__ import annotations

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import NotificationEventKind
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.scan_failure_policy import is_runtime_failure_notification_terminal
from facebook_monitor.notifications.channel_dispatch import record_notification_event


INVALID_RUNTIME_FAILURE_NOTIFICATION_MESSAGE = "runtime_failure_not_terminal"


def outbox_result_message(status: NotificationOutboxStatus, message: str) -> str:
    """成功通知不佔用 last_error；failed/skipped 保留可診斷 result code。"""

    if status == NotificationOutboxStatus.SENT:
        return ""
    return str(message or "").strip()


def runtime_failure_outbox_entry_should_skip(entry: NotificationOutboxEntry) -> bool:
    """舊 pending runtime failure 若未達 terminal 門檻，dispatch 時直接略過。"""

    return (
        entry.event_kind == NotificationEventKind.RUNTIME_FAILURE
        and not is_runtime_failure_notification_terminal(
            entry.failure_reason,
            failure_count=entry.failure_count,
        )
    )


def record_skipped_runtime_failure_outbox_entry(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
) -> int:
    """為被 policy 擋下的 runtime failure outbox 寫入 skipped event。"""

    return record_notification_event(
        app=app,
        target=target,
        item_key=entry.item_key,
        channel=entry.channel,
        status=NotificationStatus.SKIPPED,
        message=INVALID_RUNTIME_FAILURE_NOTIFICATION_MESSAGE,
        entry=entry,
    )
