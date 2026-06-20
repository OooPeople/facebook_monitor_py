"""Notification outbox entry 建立 helper。"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEventKind
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.notifications.outbox_idempotency import (
    build_notification_idempotency_key,
)


@dataclass(frozen=True)
class NotificationOutboxChannelPayload:
    """保存單一通知通道準備寫入 outbox 的純資料。"""

    channel: NotificationChannel
    title: str
    message: str
    endpoint: str = ""
    event_kind: NotificationEventKind = NotificationEventKind.MATCH
    permalink: str = ""
    source_scan_run_id: int | None = None
    failure_reason: str = ""
    failure_count: int = 0


def build_notification_outbox_entry(
    *,
    target_id: str,
    item_key: str,
    item_kind: ItemKind,
    payload: NotificationOutboxChannelPayload,
    dedupe_id: int | None = None,
) -> NotificationOutboxEntry:
    """依 channel payload 建立 outbox entry，不碰 DB 或 dedupe state。"""

    return NotificationOutboxEntry(
        idempotency_key=build_notification_idempotency_key(
            target_id=target_id,
            item_key=item_key,
            channel=payload.channel,
        ),
        dedupe_id=dedupe_id,
        target_id=target_id,
        item_key=item_key,
        item_kind=item_kind,
        channel=payload.channel,
        title=payload.title,
        message=payload.message,
        endpoint=payload.endpoint,
        permalink=payload.permalink,
        event_kind=payload.event_kind,
        source_scan_run_id=payload.source_scan_run_id,
        failure_reason=payload.failure_reason,
        failure_count=payload.failure_count,
    )
