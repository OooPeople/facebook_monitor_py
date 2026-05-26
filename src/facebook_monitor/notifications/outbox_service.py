"""Notification outbox application service。

職責：負責 match notification enqueue、after-commit 註冊、outbox claim、
pending dispatch 與內部維運 helper。外部 I/O 前必須先 claim rows，避免跨
connection 並發 commit 重複發送同一筆通知。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.defaults import PYTHON_NOTIFICATION_RUNTIME_DEFAULTS
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.notifications.channel_dispatch import DesktopSender
from facebook_monitor.notifications.channel_dispatch import DiscordSender
from facebook_monitor.notifications.channel_dispatch import NtfySender
from facebook_monitor.notifications.channel_dispatch import dispatch_notification_outbox_entry
from facebook_monitor.notifications.channel_dispatch import get_channel_definition
from facebook_monitor.notifications.channel_dispatch import (
    record_failed_notification_event_for_outbox_error,
)
from facebook_monitor.notifications.channel_plan import build_enabled_channel_plans
from facebook_monitor.notifications.channel_plan import get_channel_endpoint
from facebook_monitor.notifications.channel_plan import is_channel_enabled_by_config
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.notifications.payload import MatchNotificationFields
from facebook_monitor.notifications.payload import build_compact_notification_body
from facebook_monitor.notifications.payload import build_match_notification_payload
from facebook_monitor.notifications.safe_messages import safe_exception_message


DEFAULT_STALE_PROCESSING_SECONDS = (
    PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.stale_processing_seconds
)
DEFAULT_DISPATCH_BATCH_LIMIT = PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.dispatch_batch_limit


def build_match_notification_message(
    *,
    target: TargetDescriptor,
    author: str,
    item_text: str,
    permalink: str,
    matched_keyword: str,
    item_kind: ItemKind = ItemKind.POST,
) -> tuple[str, str]:
    """建立 keyword match 通知標題與內容，供所有通道共用。"""

    group_name = target.group_name or target.name or target.group_id
    return build_match_notification_payload(
        MatchNotificationFields(
            group_name=group_name,
            item_kind=item_kind.value,
            author=author,
            include_rule=matched_keyword,
            text=item_text,
            permalink=permalink,
        )
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
    item_kind: ItemKind = ItemKind.POST,
    ntfy_sender: NtfySender = send_ntfy_notification,
    desktop_sender: DesktopSender = send_desktop_notification,
    discord_sender: DiscordSender = send_discord_notification,
) -> None:
    """依目前設定寫入 match notification outbox，commit 後才做外部 I/O。"""

    entries = enqueue_match_notifications(
        app=app,
        target=target,
        config=config,
        item_key=item_key,
        author=author,
        item_text=item_text,
        permalink=permalink,
        matched_keyword=matched_keyword,
        item_kind=item_kind,
    )
    if entries:
        def dispatch_after_commit() -> None:
            if app.db_path is None:
                raise RuntimeError("notification outbox dispatch requires application db_path")
            dispatch_new_pending_notification_outbox_for_db(
                db_path=app.db_path,
                ntfy_sender=ntfy_sender,
                desktop_sender=desktop_sender,
                discord_sender=discord_sender,
            )

        app.run_after_commit_once(
            "notification_outbox_dispatch",
            dispatch_after_commit,
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
    item_kind: ItemKind = ItemKind.POST,
) -> tuple[NotificationOutboxEntry, ...]:
    """將 match 通知寫入 outbox；不在 DB transaction 內做外部 I/O。"""

    title, message = build_match_notification_message(
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
    for plan in build_enabled_channel_plans(config):
        message_for_channel = compact_message if plan.use_compact_message else message
        entries.append(
            app.repositories.notification_outbox.enqueue(
                NotificationOutboxEntry(
                    idempotency_key=build_notification_idempotency_key(
                        target_id=target.id,
                        item_key=item_key,
                        channel=plan.channel,
                    ),
                    target_id=target.id,
                    item_key=item_key,
                    item_kind=item_kind,
                    channel=plan.channel,
                    title=title,
                    message=message_for_channel,
                    endpoint=plan.endpoint,
                    permalink=permalink,
                )
            )
        )
    return tuple(entries)


def dispatch_new_pending_notification_outbox(
    *,
    app: ApplicationContext,
    ntfy_sender: NtfySender = send_ntfy_notification,
    desktop_sender: DesktopSender = send_desktop_notification,
    discord_sender: DiscordSender = send_discord_notification,
    stale_processing_seconds: float = DEFAULT_STALE_PROCESSING_SECONDS,
    batch_limit: int = DEFAULT_DISPATCH_BATCH_LIMIT,
) -> int:
    """Claim 並發送所有 pending outbox events，不自動重試 failed events。"""

    app.repositories.notification_outbox.recover_stale_processing(
        older_than_seconds=stale_processing_seconds,
    )
    dispatched_count = 0
    while True:
        entries = app.repositories.notification_outbox.claim_pending(limit=batch_limit)
        if not entries:
            return dispatched_count
        dispatched_count += dispatch_notification_outbox_entries(
            app=app,
            entries=entries,
            ntfy_sender=ntfy_sender,
            desktop_sender=desktop_sender,
            discord_sender=discord_sender,
        )


def dispatch_new_pending_notification_outbox_for_db(
    *,
    db_path: Path,
    ntfy_sender: NtfySender = send_ntfy_notification,
    desktop_sender: DesktopSender = send_desktop_notification,
    discord_sender: DiscordSender = send_discord_notification,
    stale_processing_seconds: float = DEFAULT_STALE_PROCESSING_SECONDS,
    batch_limit: int = DEFAULT_DISPATCH_BATCH_LIMIT,
) -> int:
    """用新的 application context 發送 pending outbox，隔離 scan commit lifecycle。"""

    with SqliteApplicationContext(db_path) as dispatch_app:
        return dispatch_new_pending_notification_outbox(
            app=dispatch_app,
            ntfy_sender=ntfy_sender,
            desktop_sender=desktop_sender,
            discord_sender=discord_sender,
            stale_processing_seconds=stale_processing_seconds,
            batch_limit=batch_limit,
        )


def retry_failed_notification_outbox(
    *,
    app: ApplicationContext,
    ntfy_sender: NtfySender = send_ntfy_notification,
    desktop_sender: DesktopSender = send_desktop_notification,
    discord_sender: DiscordSender = send_discord_notification,
    batch_limit: int = DEFAULT_DISPATCH_BATCH_LIMIT,
) -> int:
    """內部/測試用 failed outbox retry；日常 UI 不提供此入口。"""

    return dispatch_notification_outbox_entries(
        app=app,
        entries=app.repositories.notification_outbox.claim_failed(limit=batch_limit),
        ntfy_sender=ntfy_sender,
        desktop_sender=desktop_sender,
        discord_sender=discord_sender,
    )


def recover_stale_processing_outbox(
    *,
    app: ApplicationContext,
    older_than_seconds: float = DEFAULT_STALE_PROCESSING_SECONDS,
) -> int:
    """明確回收過期 processing outbox rows，供管理或測試使用。"""

    return app.repositories.notification_outbox.recover_stale_processing(
        older_than_seconds=older_than_seconds,
    )


def dispatch_notification_outbox_entries(
    *,
    app: ApplicationContext,
    entries: list[NotificationOutboxEntry],
    ntfy_sender: NtfySender,
    desktop_sender: DesktopSender,
    discord_sender: DiscordSender,
) -> int:
    """發送已 claim 的 outbox events，並將結果寫回 notification_events。"""

    dispatched_count = 0
    for entry in entries:
        if entry.id is None:
            continue
        entry_id = entry.id
        attempts = entry.attempts + 1
        target = app.repositories.targets.get(entry.target_id)
        try:
            if target is None:
                raise ValueError(f"Target not found: {entry.target_id}")
            app.repositories.notification_outbox.touch_processing(
                entry_id=entry_id,
                status=entry.status,
            )
            app.repositories.notification_outbox.connection.commit()
            entry = refresh_outbox_entry_delivery_endpoint(
                app=app,
                target=target,
                entry=entry,
            )
            event_id, status = dispatch_notification_outbox_entry(
                app=app,
                target=target,
                entry=entry,
                ntfy_sender=ntfy_sender,
                desktop_sender=desktop_sender,
                discord_sender=discord_sender,
            )
            app.repositories.notification_outbox.mark_result(
                entry_id=entry_id,
                status=status,
                attempts=attempts,
                notification_event_id=event_id,
            )
            app.repositories.notification_outbox.connection.commit()
            dispatched_count += 1
        except Exception as exc:
            error_message = safe_exception_message(
                f"{entry.channel.value}_dispatch_failed",
                exc,
            )
            app.repositories.notification_outbox.mark_result(
                entry_id=entry_id,
                status=NotificationOutboxStatus.FAILED,
                attempts=attempts,
                message=error_message,
                notification_event_id=record_failed_notification_event_for_outbox_error(
                    app=app,
                    target=target,
                    entry=entry,
                    message=error_message,
                )
                if target is not None
                else None,
            )
            app.repositories.notification_outbox.connection.commit()
    return dispatched_count


def refresh_outbox_entry_delivery_endpoint(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
) -> NotificationOutboxEntry:
    """dispatch 前套用目前 target config 的 endpoint，避免 retry 打舊設定。"""

    if entry.id is None or entry.channel == NotificationChannel.DESKTOP:
        return entry
    definition = get_channel_definition(entry.channel)
    if not definition.endpoint_field:
        return entry
    config = app.services.targets.get_config_for_target(target)
    endpoint = (
        get_channel_endpoint(config, definition)
        if is_channel_enabled_by_config(config, definition)
        else ""
    )
    if endpoint == entry.endpoint:
        return entry
    app.repositories.notification_outbox.update_delivery_endpoint(
        entry_id=entry.id,
        endpoint=endpoint,
    )
    app.repositories.notification_outbox.connection.commit()
    return replace(entry, endpoint=endpoint)


def build_notification_idempotency_key(
    *,
    target_id: str,
    item_key: str,
    channel: NotificationChannel,
) -> str:
    """建立通知 outbox 去重 key，避免同一 match/channel 重複發送。"""

    return f"{target_id}:{item_key}:{channel.value}"


def build_match_compact_notification_message(
    *,
    target: TargetDescriptor,
    author: str,
    item_text: str,
    permalink: str,
    matched_keyword: str,
    item_kind: ItemKind = ItemKind.POST,
) -> str:
    """建立桌面通知使用的短內容。"""

    group_name = target.group_name or target.name or target.group_id
    return build_compact_notification_body(
        MatchNotificationFields(
            group_name=group_name,
            item_kind=item_kind.value,
            author=author,
            include_rule=matched_keyword,
            text=item_text,
            permalink=permalink,
        )
    )
