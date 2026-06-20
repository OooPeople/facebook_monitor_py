"""Match notification outbox enqueue flow。"""

from __future__ import annotations

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.notifications.outbox_entry_builders import (
    build_notification_outbox_entry,
)
from facebook_monitor.notifications.outbox_enqueue_wake import (
    queue_notification_outbox_dispatch_wake_after_commit,
)
from facebook_monitor.notifications.outbox_match_builders import (
    build_match_channel_payloads,
)


def queue_match_notifications_after_commit(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    item_key: str,
    author: str,
    item_text: str,
    permalink: str,
    matched_keyword: str,
    logical_item_id: int | None = None,
    item_kind: ItemKind = ItemKind.POST,
) -> tuple[NotificationOutboxEntry, ...]:
    """依目前設定寫入 match notification outbox，commit 後才做外部 I/O。"""

    entries = enqueue_match_notifications(
        app=app,
        target=target,
        config=config,
        item_key=item_key,
        logical_item_id=logical_item_id,
        author=author,
        item_text=item_text,
        permalink=permalink,
        matched_keyword=matched_keyword,
        item_kind=item_kind,
    )
    if entries:
        queue_notification_outbox_dispatch_wake_after_commit(app)
    return entries


def enqueue_match_notifications(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    item_key: str,
    author: str,
    item_text: str,
    permalink: str,
    matched_keyword: str,
    logical_item_id: int | None = None,
    item_kind: ItemKind = ItemKind.POST,
) -> tuple[NotificationOutboxEntry, ...]:
    """將 match 通知寫入 outbox；不在 DB transaction 內做外部 I/O。"""

    entries: list[NotificationOutboxEntry] = []
    payloads = build_match_channel_payloads(
        target=target,
        config=config,
        item_kind=item_kind,
        author=author,
        item_text=item_text,
        permalink=permalink,
        matched_keyword=matched_keyword,
    )
    for payload in payloads:
        dedupe_id: int | None = None
        if logical_item_id is not None:
            reservation = app.repositories.notification_dedupe.reserve_match(
                target_id=target.id,
                logical_item_id=logical_item_id,
                item_key=item_key,
                item_kind=item_kind,
                channel=payload.channel,
            )
            if not reservation.created:
                continue
            dedupe_id = reservation.dedupe_id
        result = app.repositories.notification_outbox.enqueue(
            build_notification_outbox_entry(
                target_id=target.id,
                item_key=item_key,
                item_kind=item_kind,
                payload=payload,
                dedupe_id=dedupe_id,
            )
        )
        if result.created:
            entries.append(result.entry)
    return tuple(entries)
