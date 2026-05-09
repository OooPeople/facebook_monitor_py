"""通知通道實際分發。

職責：保存 sender protocol、channel definition、單筆 outbox event 發送與
notification event 記錄。outbox claim/retry 流程由 `outbox_service` 管理。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from facebook_monitor.application.context import ApplicationContext
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


class NtfySender(Protocol):
    """定義可注入的 ntfy sender 介面。"""

    def __call__(self, config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """送出 ntfy 通知並回傳結果。"""


class DesktopSender(Protocol):
    """定義可注入的桌面通知 sender 介面。"""

    def __call__(self, title: str, message: str) -> DesktopNotificationResult:
        """送出桌面通知並回傳結果。"""


class DiscordSender(Protocol):
    """定義可注入的 Discord sender 介面。"""

    def __call__(self, config: DiscordConfig, title: str, message: str) -> DiscordResult:
        """送出 Discord webhook 通知並回傳結果。"""


@dataclass(frozen=True)
class NotificationChannelDefinition:
    """描述單一通知通道的設定欄位與 skipped 狀態。"""

    channel: NotificationChannel
    enabled_field: str
    endpoint_field: str = ""
    skipped_message: str = ""


NOTIFICATION_CHANNEL_DEFINITIONS: tuple[NotificationChannelDefinition, ...] = (
    NotificationChannelDefinition(
        channel=NotificationChannel.DESKTOP,
        enabled_field="enable_desktop_notification",
        skipped_message="desktop_skipped",
    ),
    NotificationChannelDefinition(
        channel=NotificationChannel.NTFY,
        enabled_field="enable_ntfy",
        endpoint_field="ntfy_topic",
        skipped_message="ntfy_skipped",
    ),
    NotificationChannelDefinition(
        channel=NotificationChannel.DISCORD,
        enabled_field="enable_discord_notification",
        endpoint_field="discord_webhook",
        skipped_message="discord_skipped",
    ),
)


def is_channel_enabled(
    config: TargetConfig,
    definition: NotificationChannelDefinition,
) -> bool:
    """判斷指定通知通道是否已由使用者啟用。"""

    return bool(getattr(config, definition.enabled_field))


def dispatch_notification_outbox_entry(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
    ntfy_sender: NtfySender,
    desktop_sender: DesktopSender,
    discord_sender: DiscordSender,
) -> tuple[int, NotificationOutboxStatus]:
    """發送單筆 outbox event，回傳 notification event id 與 outbox 狀態。"""

    if entry.channel == NotificationChannel.DESKTOP:
        result = desktop_sender(entry.title, entry.message)
        event_id = record_notification_event(
            app=app,
            target=target,
            item_key=entry.item_key,
            channel=entry.channel,
            status=NotificationStatus.SENT if result.ok else NotificationStatus.FAILED,
            message=result.message,
        )
        return event_id, (
            NotificationOutboxStatus.SENT if result.ok else NotificationOutboxStatus.FAILED
        )
    if entry.channel == NotificationChannel.NTFY:
        if not entry.endpoint.strip():
            event_id = record_notification_event(
                app=app,
                target=target,
                item_key=entry.item_key,
                channel=entry.channel,
                status=NotificationStatus.SKIPPED,
                message="ntfy_skipped",
            )
            return event_id, NotificationOutboxStatus.SKIPPED
        result = ntfy_sender(
            NtfyConfig(topic=entry.endpoint, click_url=entry.permalink),
            entry.title,
            entry.message,
        )
        event_id = record_notification_event(
            app=app,
            target=target,
            item_key=entry.item_key,
            channel=entry.channel,
            status=NotificationStatus.SENT if result.ok else NotificationStatus.FAILED,
            message=result.message,
        )
        return event_id, (
            NotificationOutboxStatus.SENT if result.ok else NotificationOutboxStatus.FAILED
        )
    if entry.channel == NotificationChannel.DISCORD:
        if not entry.endpoint.strip():
            event_id = record_notification_event(
                app=app,
                target=target,
                item_key=entry.item_key,
                channel=entry.channel,
                status=NotificationStatus.SKIPPED,
                message="discord_skipped",
            )
            return event_id, NotificationOutboxStatus.SKIPPED
        result = discord_sender(
            DiscordConfig(webhook_url=entry.endpoint),
            entry.title,
            entry.message,
        )
        event_id = record_notification_event(
            app=app,
            target=target,
            item_key=entry.item_key,
            channel=entry.channel,
            status=NotificationStatus.SENT if result.ok else NotificationStatus.FAILED,
            message=result.message,
        )
        return event_id, (
            NotificationOutboxStatus.SENT if result.ok else NotificationOutboxStatus.FAILED
        )
    raise ValueError(f"Unsupported notification channel: {entry.channel}")


def record_notification_event(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    item_key: str,
    channel: NotificationChannel,
    status: NotificationStatus,
    message: str,
) -> int:
    """寫入單一 notification event 並回傳 row id。"""

    return app.repositories.notification_events.add(
        NotificationEvent(
            target_id=target.id,
            item_key=item_key,
            channel=channel,
            status=status,
            message=message,
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
    )
