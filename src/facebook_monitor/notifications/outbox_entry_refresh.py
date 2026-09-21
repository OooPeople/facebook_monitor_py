"""Notification outbox entry dispatch-time refresh helpers。"""

from __future__ import annotations

from dataclasses import replace

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.notification_channels import get_channel_definition
from facebook_monitor.notifications.channel_plan import get_channel_endpoint
from facebook_monitor.notifications.channel_plan import is_channel_enabled_by_config
from facebook_monitor.persistence.repositories.notification_outbox import (
    StaleNotificationOutboxClaim,
)


def refresh_outbox_entry_delivery_endpoint(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
    claim_token: str,
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
    if not app.repositories.notification_outbox.update_delivery_endpoint(
        entry_id=entry.id,
        endpoint=endpoint,
        status=entry.status,
        claim_token=claim_token,
    ):
        raise StaleNotificationOutboxClaim(
            f"notification outbox claim is stale: entry_id={entry.id}"
        )
    app.repositories.notification_outbox.connection.commit()
    return replace(entry, endpoint=endpoint)
