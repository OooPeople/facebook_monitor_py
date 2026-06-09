"""通知通道實際分發。

職責：保存 sender protocol、channel definition、單筆 outbox event 發送與
notification event 記錄。outbox claim/retry 流程由 `outbox_service` 管理。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.notification_channels import NOTIFICATION_CHANNEL_DEFINITIONS
from facebook_monitor.core.notification_channels import NotificationChannelDefinition
from facebook_monitor.core.notification_channels import get_channel_definition
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.notifications.desktop import DesktopNotificationResult
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import DiscordResult
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult


__all__ = [
    "DesktopSender",
    "DiscordSender",
    "NOTIFICATION_CHANNEL_DEFINITIONS",
    "NotificationChannelDefinition",
    "NtfySender",
    "dispatch_notification_outbox_entry",
    "get_channel_definition",
    "is_channel_enabled",
    "record_failed_notification_event_for_outbox_error",
    "record_notification_event",
]


class NtfySender(Protocol):
    """定義可注入的 ntfy sender 介面。"""

    def __call__(self, config: NtfyConfig, title: str, message: str, /) -> NtfyResult:
        """送出 ntfy 通知並回傳結果。"""


class DesktopSender(Protocol):
    """定義可注入的桌面通知 sender 介面。"""

    def __call__(self, title: str, message: str, /) -> DesktopNotificationResult:
        """送出桌面通知並回傳結果。"""


class DiscordSender(Protocol):
    """定義可注入的 Discord sender 介面。"""

    def __call__(self, config: DiscordConfig, title: str, message: str, /) -> DiscordResult:
        """送出 Discord webhook 通知並回傳結果。"""


@dataclass(frozen=True)
class NotificationSenders:
    """集中保存外部通知 sender，供 channel handler registry 使用。"""

    ntfy: NtfySender
    desktop: DesktopSender
    discord: DiscordSender


NotificationChannelHandler = Callable[
    [ApplicationContext, TargetDescriptor, NotificationOutboxEntry, NotificationSenders],
    tuple[int, NotificationOutboxStatus, str],
]


def is_channel_enabled(
    config: TargetConfig,
    definition: NotificationChannelDefinition,
) -> bool:
    """判斷指定通知通道是否已由使用者啟用。"""

    # Backward-compatible facade；新程式碼優先使用 channel_plan。
    return bool(getattr(config, definition.enabled_field))


def _result_to_status(ok: bool) -> tuple[NotificationStatus, NotificationOutboxStatus]:
    """將 sender result 轉成 notification event / outbox status。"""

    return (
        NotificationStatus.SENT if ok else NotificationStatus.FAILED,
        NotificationOutboxStatus.SENT if ok else NotificationOutboxStatus.FAILED,
    )


def _record_send_result(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
    ok: bool,
    message: str,
) -> tuple[int, NotificationOutboxStatus, str]:
    """寫入 sender result 並回傳 outbox status。"""

    event_status, outbox_status = _result_to_status(ok)
    event_id = record_notification_event(
        app=app,
        target=target,
        item_key=entry.item_key,
        channel=entry.channel,
        status=event_status,
        message=message,
        entry=entry,
    )
    return event_id, outbox_status, message


def _record_skipped_channel(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
    message: str,
) -> tuple[int, NotificationOutboxStatus, str]:
    """寫入缺少 endpoint 等 skipped channel result。"""

    event_id = record_notification_event(
        app=app,
        target=target,
        item_key=entry.item_key,
        channel=entry.channel,
        status=NotificationStatus.SKIPPED,
        message=message,
        entry=entry,
    )
    return event_id, NotificationOutboxStatus.SKIPPED, message


def _dispatch_desktop_channel(
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
    senders: NotificationSenders,
) -> tuple[int, NotificationOutboxStatus, str]:
    result = senders.desktop(entry.title, entry.message)
    return _record_send_result(
        app=app,
        target=target,
        entry=entry,
        ok=result.ok,
        message=result.message,
    )


def _dispatch_ntfy_channel(
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
    senders: NotificationSenders,
) -> tuple[int, NotificationOutboxStatus, str]:
    if not entry.endpoint.strip():
        return _record_skipped_channel(
            app=app,
            target=target,
            entry=entry,
            message=get_channel_definition(entry.channel).skipped_message,
        )
    result = senders.ntfy(
        NtfyConfig(topic=entry.endpoint, click_url=entry.permalink),
        entry.title,
        entry.message,
    )
    return _record_send_result(
        app=app,
        target=target,
        entry=entry,
        ok=result.ok,
        message=result.message,
    )


def _dispatch_discord_channel(
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
    senders: NotificationSenders,
) -> tuple[int, NotificationOutboxStatus, str]:
    if not entry.endpoint.strip():
        return _record_skipped_channel(
            app=app,
            target=target,
            entry=entry,
            message=get_channel_definition(entry.channel).skipped_message,
        )
    result = senders.discord(
        DiscordConfig(webhook_url=entry.endpoint),
        entry.title,
        entry.message,
    )
    return _record_send_result(
        app=app,
        target=target,
        entry=entry,
        ok=result.ok,
        message=result.message,
    )


NOTIFICATION_CHANNEL_HANDLERS: dict[NotificationChannel, NotificationChannelHandler] = {
    NotificationChannel.DESKTOP: _dispatch_desktop_channel,
    NotificationChannel.NTFY: _dispatch_ntfy_channel,
    NotificationChannel.DISCORD: _dispatch_discord_channel,
}


def dispatch_notification_outbox_entry(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
    ntfy_sender: NtfySender,
    desktop_sender: DesktopSender,
    discord_sender: DiscordSender,
) -> tuple[int, NotificationOutboxStatus, str]:
    """發送單筆 outbox event，回傳 event id、outbox 狀態與 result message。"""

    get_channel_definition(entry.channel)
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


def record_failed_notification_event_for_outbox_error(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
    message: str,
) -> int:
    """外部 sender raise 時仍寫入可觀測 failed notification event。"""

    return record_notification_event(
        app=app,
        target=target,
        item_key=entry.item_key,
        channel=entry.channel,
        status=NotificationStatus.FAILED,
        message=message,
        entry=entry,
    )
