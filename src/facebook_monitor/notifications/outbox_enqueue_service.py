"""Notification outbox enqueue service。

職責：負責 match/runtime notification enqueue 與 after-commit wake 註冊；
dispatch/drain/retry 由 `outbox_dispatch_service` 擁有。正式 runtime 的
after-commit hook 只喚醒背景 dispatcher，不直接送外部通知。
"""

from __future__ import annotations

import logging

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEventKind
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
from facebook_monitor.core.scan_failure_policy import is_runtime_failure_notification_terminal
from facebook_monitor.core.scan_failure_policy import normalize_scan_failure_reason
from facebook_monitor.notifications.channel_plan import build_enabled_channel_plans
from facebook_monitor.notifications.desktop_format import (
    build_runtime_failure_compact_notification_message,
)
from facebook_monitor.notifications.match_message_builders import (
    build_match_compact_notification_message,
)
from facebook_monitor.notifications.match_message_builders import (
    build_match_discord_notification_message,
)
from facebook_monitor.notifications.match_message_builders import (
    build_ntfy_match_notification_message,
)
from facebook_monitor.notifications.outbox_dispatcher import (
    wake_notification_outbox_dispatcher_for_db,
)
from facebook_monitor.notifications.runtime_failure_message_builders import (
    build_runtime_failure_notification_message,
)


logger = logging.getLogger(__name__)


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
        _queue_notification_outbox_dispatch_wake_after_commit(app)
    return entries


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
        _queue_notification_outbox_dispatch_wake_after_commit(app)
    return entries


def _queue_notification_outbox_dispatch_wake_after_commit(app: ApplicationContext) -> None:
    """註冊 commit 後喚醒 outbox dispatcher；未註冊時 pending rows 留在 DB。"""

    def wake_after_commit() -> None:
        if app.db_path is None:
            raise RuntimeError("notification outbox dispatch requires application db_path")
        if not wake_notification_outbox_dispatcher_for_db(app.db_path):
            logger.debug(
                "notification_outbox_dispatcher_not_registered db_path=%s",
                app.db_path,
            )

    app.run_after_commit_once(
        "notification_outbox_dispatch_wake",
        wake_after_commit,
    )


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

    title, message = build_ntfy_match_notification_message(
        target=target,
        item_kind=item_kind,
        author=author,
        item_text=item_text,
        permalink=permalink,
        matched_keyword=matched_keyword,
    )
    entries: list[NotificationOutboxEntry] = []
    compact_message = build_match_compact_notification_message(
        target=target,
        item_kind=item_kind,
        author=author,
        item_text=item_text,
        permalink=permalink,
        matched_keyword=matched_keyword,
    )
    discord_title, discord_message = build_match_discord_notification_message(
        target=target,
        item_kind=item_kind,
        author=author,
        item_text=item_text,
        permalink=permalink,
        matched_keyword=matched_keyword,
    )
    for plan in build_enabled_channel_plans(config):
        dedupe_id: int | None = None
        if logical_item_id is not None:
            reservation = app.repositories.notification_dedupe.reserve_match(
                target_id=target.id,
                logical_item_id=logical_item_id,
                item_key=item_key,
                item_kind=item_kind,
                channel=plan.channel,
            )
            if not reservation.created:
                continue
            dedupe_id = reservation.dedupe_id
        title_for_channel = discord_title if plan.channel == NotificationChannel.DISCORD else title
        if plan.channel == NotificationChannel.DISCORD:
            message_for_channel = discord_message
        else:
            message_for_channel = compact_message if plan.use_compact_message else message
        result = app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=build_notification_idempotency_key(
                    target_id=target.id,
                    item_key=item_key,
                    channel=plan.channel,
                ),
                dedupe_id=dedupe_id,
                target_id=target.id,
                item_key=item_key,
                item_kind=item_kind,
                channel=plan.channel,
                title=title_for_channel,
                message=message_for_channel,
                endpoint=plan.endpoint,
                permalink=permalink,
            )
        )
        if result.created:
            entries.append(result.entry)
    return tuple(entries)


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
    title, message = build_runtime_failure_notification_message(
        target=target,
        reason=normalized_reason,
        failure_count=failure_count,
        error_message=error_message,
        target_stopped=target_stopped,
    )
    item_key = f"runtime-failure:{scan_run_id}"
    item_kind = (
        ItemKind.COMMENT
        if target.target_kind.value == "comments"
        else ItemKind.POST
    )
    entries: list[NotificationOutboxEntry] = []
    for plan in build_enabled_channel_plans(config):
        reservation = app.repositories.notification_dedupe.reserve_runtime_failure(
            target_id=target.id,
            scan_run_id=scan_run_id,
            item_key=item_key,
            item_kind=item_kind,
            channel=plan.channel,
            failure_reason=normalized_reason,
            failure_count=max(int(failure_count), 1),
        )
        if not reservation.created:
            continue
        result = app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=build_notification_idempotency_key(
                    target_id=target.id,
                    item_key=item_key,
                    channel=plan.channel,
                ),
                dedupe_id=reservation.dedupe_id,
                target_id=target.id,
                item_key=item_key,
                item_kind=item_kind,
                channel=plan.channel,
                title=title,
                message=(
                    build_runtime_failure_compact_notification_message(message)
                    if plan.use_compact_message
                    else message
                ),
                endpoint=plan.endpoint,
                permalink=target.canonical_url,
                event_kind=NotificationEventKind.RUNTIME_FAILURE,
                source_scan_run_id=scan_run_id,
                failure_reason=normalized_reason,
                failure_count=max(int(failure_count), 1),
            )
        )
        if result.created:
            entries.append(result.entry)
    return tuple(entries)


def build_notification_idempotency_key(
    *,
    target_id: str,
    item_key: str,
    channel: NotificationChannel,
) -> str:
    """建立通知 outbox 去重 key，避免同一 match/channel 重複發送。"""

    return f"{target_id}:{item_key}:{channel.value}"
