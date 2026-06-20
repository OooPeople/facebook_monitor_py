"""通知通道實際分發。

職責：保存單筆 outbox event 發送與 notification event
記錄。channel registry 屬於 core notification channels。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from facebook_monitor.application.context import ApplicationContext
import facebook_monitor.core.notification_channels as notification_channels
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.senders import DesktopSender
from facebook_monitor.notifications.senders import DiscordSender
from facebook_monitor.notifications.senders import NtfySender


__all__ = [
    "NotificationDispatchResult",
    "record_notification_event",
    "send_notification_outbox_entry",
]


@dataclass(frozen=True)
class NotificationSenders:
    """集中保存外部通知 sender，供 channel handler registry 使用。"""

    ntfy: NtfySender
    desktop: DesktopSender
    discord: DiscordSender


@dataclass(frozen=True)
class NotificationDispatchResult:
    """保存 sender 執行後尚未寫入 DB 的通知結果。"""

    event_status: NotificationStatus
    outbox_status: NotificationOutboxStatus
    message: str


NotificationChannelHandler = Callable[
    [ApplicationContext, TargetDescriptor, NotificationOutboxEntry, NotificationSenders],
    NotificationDispatchResult,
]


def _result_to_status(ok: bool) -> tuple[NotificationStatus, NotificationOutboxStatus]:
    """將 sender result 轉成 notification event / outbox status。"""

    return (
        NotificationStatus.SENT if ok else NotificationStatus.FAILED,
        NotificationOutboxStatus.SENT if ok else NotificationOutboxStatus.FAILED,
    )


def _sender_result_to_dispatch_result(
    *,
    ok: bool,
    message: str,
) -> NotificationDispatchResult:
    """將 sender result 轉成尚未寫 DB 的 dispatch result。"""

    event_status, outbox_status = _result_to_status(ok)
    return NotificationDispatchResult(
        event_status=event_status,
        outbox_status=outbox_status,
        message=message,
    )


def _skipped_channel_dispatch_result(message: str) -> NotificationDispatchResult:
    """回傳缺少 endpoint 等 skipped channel result。"""

    return NotificationDispatchResult(
        event_status=NotificationStatus.SKIPPED,
        outbox_status=NotificationOutboxStatus.SKIPPED,
        message=message,
    )


def _dispatch_desktop_channel(
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
    senders: NotificationSenders,
) -> NotificationDispatchResult:
    del app, target
    result = senders.desktop(entry.title, entry.message)
    return _sender_result_to_dispatch_result(
        ok=result.ok,
        message=result.message,
    )


def _dispatch_ntfy_channel(
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
    senders: NotificationSenders,
) -> NotificationDispatchResult:
    del app, target
    if not entry.endpoint.strip():
        return _skipped_channel_dispatch_result(
            message=notification_channels.get_channel_definition(
                entry.channel
            ).skipped_message,
        )
    result = senders.ntfy(
        NtfyConfig(topic=entry.endpoint, click_url=entry.permalink),
        entry.title,
        entry.message,
    )
    return _sender_result_to_dispatch_result(
        ok=result.ok,
        message=result.message,
    )


def _dispatch_discord_channel(
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
    senders: NotificationSenders,
) -> NotificationDispatchResult:
    del app, target
    if not entry.endpoint.strip():
        return _skipped_channel_dispatch_result(
            message=notification_channels.get_channel_definition(
                entry.channel
            ).skipped_message,
        )
    result = senders.discord(
        DiscordConfig(webhook_url=entry.endpoint),
        entry.title,
        entry.message,
    )
    return _sender_result_to_dispatch_result(
        ok=result.ok,
        message=result.message,
    )


NOTIFICATION_CHANNEL_HANDLERS: dict[NotificationChannel, NotificationChannelHandler] = {
    NotificationChannel.DESKTOP: _dispatch_desktop_channel,
    NotificationChannel.NTFY: _dispatch_ntfy_channel,
    NotificationChannel.DISCORD: _dispatch_discord_channel,
}


def send_notification_outbox_entry(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
    ntfy_sender: NtfySender,
    desktop_sender: DesktopSender,
    discord_sender: DiscordSender,
) -> NotificationDispatchResult:
    """發送單筆 outbox event，尚不寫入 notification event。"""

    notification_channels.get_channel_definition(entry.channel)
    handler = NOTIFICATION_CHANNEL_HANDLERS.get(entry.channel)
    if handler is None:
        raise ValueError(f"Unsupported notification channel: {entry.channel}")
    return handler(
        app,
        target,
        entry,
        NotificationSenders(
            ntfy=ntfy_sender,
            desktop=desktop_sender,
            discord=discord_sender,
        ),
    )


def record_notification_event(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    item_key: str,
    channel: NotificationChannel,
    status: NotificationStatus,
    message: str,
    entry: NotificationOutboxEntry | None = None,
) -> int:
    """寫入單一 notification event 並回傳 row id。"""

    if entry is None:
        return app.repositories.notification_events.add(
            NotificationEvent(
                target_id=target.id,
                item_key=item_key,
                channel=channel,
                status=status,
                message=message,
            )
        )
    return app.repositories.notification_events.add(
        NotificationEvent(
            target_id=target.id,
            item_key=item_key,
            channel=channel,
            status=status,
            message=message,
            event_kind=entry.event_kind,
            source_scan_run_id=entry.source_scan_run_id,
            failure_reason=entry.failure_reason,
            failure_count=entry.failure_count,
        )
    )
