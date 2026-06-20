"""Keyword match notification outbox payload builders。"""

from __future__ import annotations

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.notifications.channel_plan import build_enabled_channel_plans
from facebook_monitor.notifications.match_message_builders import (
    build_match_compact_notification_message,
)
from facebook_monitor.notifications.match_message_builders import (
    build_match_discord_notification_message,
)
from facebook_monitor.notifications.match_message_builders import (
    build_ntfy_match_notification_message,
)
from facebook_monitor.notifications.outbox_entry_builders import (
    NotificationOutboxChannelPayload,
)


def build_match_channel_payloads(
    *,
    target: TargetDescriptor,
    config: TargetConfig,
    item_kind: ItemKind,
    author: str,
    item_text: str,
    permalink: str,
    matched_keyword: str,
) -> tuple[NotificationOutboxChannelPayload, ...]:
    """依 target config 建立每個已啟用 channel 的 match outbox payload。"""

    title, message = build_ntfy_match_notification_message(
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
    discord_title, discord_message = build_match_discord_notification_message(
        target=target,
        item_kind=item_kind,
        author=author,
        item_text=item_text,
        permalink=permalink,
        matched_keyword=matched_keyword,
    )
    payloads: list[NotificationOutboxChannelPayload] = []
    for plan in build_enabled_channel_plans(config):
        title_for_channel = (
            discord_title if plan.channel == NotificationChannel.DISCORD else title
        )
        if plan.channel == NotificationChannel.DISCORD:
            message_for_channel = discord_message
        else:
            message_for_channel = compact_message if plan.use_compact_message else message
        payloads.append(
            NotificationOutboxChannelPayload(
                channel=plan.channel,
                title=title_for_channel,
                message=message_for_channel,
                endpoint=plan.endpoint,
                permalink=permalink,
            )
        )
    return tuple(payloads)
