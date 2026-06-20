"""Runtime failure notification outbox enqueue flow。"""

from __future__ import annotations

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
from facebook_monitor.core.scan_failure_policy import is_runtime_failure_notification_terminal
from facebook_monitor.core.scan_failure_policy import normalize_scan_failure_reason
from facebook_monitor.notifications.outbox_entry_builders import (
    build_notification_outbox_entry,
)
from facebook_monitor.notifications.outbox_enqueue_wake import (
    queue_notification_outbox_dispatch_wake_after_commit,
)
from facebook_monitor.notifications.outbox_runtime_failure_builders import (
    build_runtime_failure_channel_payloads,
)
from facebook_monitor.notifications.outbox_runtime_failure_builders import (
    build_runtime_failure_item_key,
)
from facebook_monitor.notifications.outbox_runtime_failure_builders import (
    build_runtime_failure_item_kind,
)
from facebook_monitor.notifications.outbox_runtime_failure_builders import (
    normalize_runtime_failure_count,
)


def queue_runtime_failure_notifications_after_commit(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    scan_run_id: int,
    reason: str,
    failure_count: int,
    error_message: str,
    target_stopped: bool = True,
    failure_source: ScanFailureSource = "unknown_exception",
) -> tuple[NotificationOutboxEntry, ...]:
    """將 terminal runtime failure 通知寫入 outbox，commit 後才做外部 I/O。"""

    entries = enqueue_runtime_failure_notifications(
        app=app,
        target=target,
        config=config,
        scan_run_id=scan_run_id,
        reason=reason,
        failure_count=failure_count,
        error_message=error_message,
        target_stopped=target_stopped,
        failure_source=failure_source,
    )
    if entries:
        queue_notification_outbox_dispatch_wake_after_commit(app)
    return entries


def enqueue_runtime_failure_notifications(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    scan_run_id: int,
    reason: str,
    failure_count: int,
    error_message: str,
    target_stopped: bool = True,
    failure_source: ScanFailureSource = "unknown_exception",
) -> tuple[NotificationOutboxEntry, ...]:
    """將 target runtime failure 通知寫入 outbox。"""

    if scan_run_id <= 0:
        return ()
    normalized_reason = normalize_scan_failure_reason(reason)
    if not is_runtime_failure_notification_terminal(
        reason=normalized_reason,
        failure_count=failure_count,
        source=failure_source,
    ):
        return ()
    item_key = build_runtime_failure_item_key(scan_run_id)
    item_kind = build_runtime_failure_item_kind(target)
    normalized_failure_count = normalize_runtime_failure_count(failure_count)
    payloads = build_runtime_failure_channel_payloads(
        target=target,
        config=config,
        scan_run_id=scan_run_id,
        normalized_reason=normalized_reason,
        failure_count=normalized_failure_count,
        error_message=error_message,
        target_stopped=target_stopped,
    )
    entries: list[NotificationOutboxEntry] = []
    for payload in payloads:
        reservation = app.repositories.notification_dedupe.reserve_runtime_failure(
            target_id=target.id,
            scan_run_id=scan_run_id,
            item_key=item_key,
            item_kind=item_kind,
            channel=payload.channel,
            failure_reason=normalized_reason,
            failure_count=normalized_failure_count,
        )
        if not reservation.created:
            continue
        result = app.repositories.notification_outbox.enqueue(
            build_notification_outbox_entry(
                target_id=target.id,
                item_key=item_key,
                item_kind=item_kind,
                payload=payload,
                dedupe_id=reservation.dedupe_id,
            )
        )
        if result.created:
            entries.append(result.entry)
    return tuple(entries)
