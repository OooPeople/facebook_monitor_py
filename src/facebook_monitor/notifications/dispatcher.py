"""通知通道分發。

職責：對齊 userscript 的 channel definitions / runner map 語義，
讓 worker 不直接依通道手寫通知流程。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.notifications.desktop import DesktopNotificationResult
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import DiscordResult
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.notifications.payload import MatchNotificationFields
from facebook_monitor.notifications.payload import build_compact_notification_body
from facebook_monitor.notifications.payload import build_match_notification_payload


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


def notify_match_if_enabled(
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
    """依目前設定分發 match 通知，並記錄每個有動作通道的 event。"""

    title, message = build_match_notification_message(
        target=target,
        item_kind=item_kind,
        author=author,
        item_text=item_text,
        permalink=permalink,
        matched_keyword=matched_keyword,
    )
    compact_message = build_match_compact_notification_message(
        target=target,
        item_kind=item_kind,
        author=author,
        item_text=item_text,
        permalink=permalink,
        matched_keyword=matched_keyword,
    )
    for definition in NOTIFICATION_CHANNEL_DEFINITIONS:
        if definition.channel == NotificationChannel.DESKTOP:
            dispatch_desktop_notification(
                app=app,
                target=target,
                config=config,
                item_key=item_key,
                title=title,
                message=compact_message,
                sender=desktop_sender,
            )
            continue
        if not is_channel_enabled(config, definition):
            continue
        if definition.channel == NotificationChannel.NTFY:
            dispatch_ntfy_notification(
                app=app,
                target=target,
                config=config,
                item_key=item_key,
                permalink=permalink,
                title=title,
                message=message,
                sender=ntfy_sender,
            )
            continue
        if definition.channel == NotificationChannel.DISCORD:
            dispatch_discord_notification(
                app=app,
                target=target,
                config=config,
                item_key=item_key,
                title=title,
                message=message,
                sender=discord_sender,
            )


def send_manual_test_notification(
    *,
    config: TargetConfig,
    ntfy_sender: NtfySender = send_ntfy_notification,
    desktop_sender: DesktopSender = send_desktop_notification,
    discord_sender: DiscordSender = send_discord_notification,
) -> list[str]:
    """使用與正式 match 相同的 payload / channel runner 送出測試通知。"""

    title, message = build_match_notification_payload(
        MatchNotificationFields(
            group_name="Facebook Monitor",
            item_kind=ItemKind.POST.value,
            author="Test",
            include_rule="manual test",
            text="This is a test notification from facebook_monitor_py.",
            permalink="",
        )
    )
    compact_message = build_compact_notification_body(
        MatchNotificationFields(
            group_name="Facebook Monitor",
            item_kind=ItemKind.POST.value,
            author="Test",
            include_rule="manual test",
            text="This is a test notification from facebook_monitor_py.",
            permalink="",
        )
    )
    results: list[str] = []
    if config.enable_desktop_notification:
        desktop_result = desktop_sender(title, compact_message)
        results.append(desktop_result.message)
    if config.enable_ntfy:
        if config.ntfy_topic.strip():
            ntfy_result = ntfy_sender(NtfyConfig(topic=config.ntfy_topic), title, message)
            results.append("ntfy_sent" if ntfy_result.ok else f"ntfy_failed: {ntfy_result.message}")
        else:
            results.append("ntfy_skipped")
    if config.enable_discord_notification:
        if config.discord_webhook.strip():
            discord_result = discord_sender(
                DiscordConfig(webhook_url=config.discord_webhook),
                title,
                message,
            )
            results.append(discord_result.message)
        else:
            results.append("discord_skipped")
    if not results:
        results.append("notification_skipped: no channel enabled")
    return results


def is_channel_enabled(
    config: TargetConfig,
    definition: NotificationChannelDefinition,
) -> bool:
    """判斷指定通知通道是否已由使用者啟用。"""

    return bool(getattr(config, definition.enabled_field))


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


def dispatch_desktop_notification(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    item_key: str,
    title: str,
    message: str,
    sender: DesktopSender,
) -> None:
    """送出桌面通知；未啟用時不記錄事件，對齊 userscript skipped 摘要。"""

    if not config.enable_desktop_notification:
        return
    result = sender(title, message)
    record_notification_event(
        app=app,
        target=target,
        item_key=item_key,
        channel=NotificationChannel.DESKTOP,
        status=NotificationStatus.SENT if result.ok else NotificationStatus.FAILED,
        message=result.message,
    )


def dispatch_ntfy_notification(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    item_key: str,
    permalink: str,
    title: str,
    message: str,
    sender: NtfySender,
) -> None:
    """送出 ntfy 通知；topic 空白時對齊 userscript 記錄 skipped。"""

    if not config.ntfy_topic.strip():
        record_notification_event(
            app=app,
            target=target,
            item_key=item_key,
            channel=NotificationChannel.NTFY,
            status=NotificationStatus.SKIPPED,
            message="ntfy_skipped",
        )
        return

    result = sender(
        NtfyConfig(topic=config.ntfy_topic, click_url=permalink),
        title,
        message,
    )
    record_notification_event(
        app=app,
        target=target,
        item_key=item_key,
        channel=NotificationChannel.NTFY,
        status=NotificationStatus.SENT if result.ok else NotificationStatus.FAILED,
        message=result.message,
    )


def dispatch_discord_notification(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    item_key: str,
    title: str,
    message: str,
    sender: DiscordSender,
) -> None:
    """送出 Discord webhook；webhook 空白時對齊 userscript 記錄 skipped。"""

    if not config.discord_webhook.strip():
        record_notification_event(
            app=app,
            target=target,
            item_key=item_key,
            channel=NotificationChannel.DISCORD,
            status=NotificationStatus.SKIPPED,
            message="discord_skipped",
        )
        return

    result = sender(
        DiscordConfig(webhook_url=config.discord_webhook),
        title,
        message,
    )
    record_notification_event(
        app=app,
        target=target,
        item_key=item_key,
        channel=NotificationChannel.DISCORD,
        status=NotificationStatus.SENT if result.ok else NotificationStatus.FAILED,
        message=result.message,
    )


def record_notification_event(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    item_key: str,
    channel: NotificationChannel,
    status: NotificationStatus,
    message: str,
) -> None:
    """寫入單一 notification event。"""

    app.repositories.notification_events.add(
        NotificationEvent(
            target_id=target.id,
            item_key=item_key,
            channel=channel,
            status=status,
            message=message,
        )
    )
