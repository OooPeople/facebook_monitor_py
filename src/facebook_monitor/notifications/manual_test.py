"""手動測試通知流程。"""

from __future__ import annotations

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.notifications.channel_dispatch import DesktopSender
from facebook_monitor.notifications.channel_dispatch import DiscordSender
from facebook_monitor.notifications.channel_dispatch import NtfySender
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.notifications.payload import MatchNotificationFields
from facebook_monitor.notifications.payload import build_compact_notification_body
from facebook_monitor.notifications.payload import build_match_notification_payload


def send_manual_test_notification(
    *,
    config: TargetConfig,
    ntfy_sender: NtfySender = send_ntfy_notification,
    desktop_sender: DesktopSender = send_desktop_notification,
    discord_sender: DiscordSender = send_discord_notification,
) -> list[str]:
    """使用與正式 match 相同的 payload / channel runner 送出測試通知。"""

    fields = MatchNotificationFields(
        group_name="Facebook Monitor",
        item_kind=ItemKind.POST.value,
        author="Test",
        include_rule="manual test",
        text="This is a test notification from facebook_monitor_py.",
        permalink="",
    )
    title, message = build_match_notification_payload(fields)
    compact_message = build_compact_notification_body(fields)
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
