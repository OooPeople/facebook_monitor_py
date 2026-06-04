"""手動測試通知流程。"""

from __future__ import annotations

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.notifications.channel_dispatch import DesktopSender
from facebook_monitor.notifications.channel_dispatch import DiscordSender
from facebook_monitor.notifications.channel_dispatch import NtfySender
from facebook_monitor.notifications.channel_plan import build_enabled_channel_plans
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.discord_format import build_discord_match_notification_payload
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
        author="測試",
        include_rule="測試",
        text="這是 Facebook Monitor 的測試通知。",
        permalink="",
    )
    title, message = build_match_notification_payload(fields)
    discord_title, discord_message = build_discord_match_notification_payload(fields)
    compact_message = build_compact_notification_body(fields)
    results: list[str] = []
    for plan in build_enabled_channel_plans(config):
        if plan.channel == NotificationChannel.DESKTOP:
            desktop_result = desktop_sender(title, compact_message)
            results.append(desktop_result.message)
        elif plan.channel == NotificationChannel.NTFY:
            if plan.endpoint.strip():
                ntfy_result = ntfy_sender(NtfyConfig(topic=plan.endpoint), title, message)
                results.append("ntfy_sent" if ntfy_result.ok else f"ntfy_failed: {ntfy_result.message}")
            else:
                results.append(plan.definition.skipped_message)
        elif plan.channel == NotificationChannel.DISCORD:
            if plan.endpoint.strip():
                discord_result = discord_sender(
                    DiscordConfig(webhook_url=plan.endpoint),
                    discord_title,
                    discord_message,
                )
                results.append(discord_result.message)
            else:
                results.append(plan.definition.skipped_message)
    if not results:
        results.append("notification_skipped: no channel enabled")
    return results
