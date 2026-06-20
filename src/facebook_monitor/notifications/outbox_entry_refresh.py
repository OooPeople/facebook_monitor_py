"""Notification outbox entry dispatch-time refresh helpers。"""

from __future__ import annotations

from dataclasses import replace

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.target_display import format_target_display_name
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEventKind
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.notification_channels import get_channel_definition
from facebook_monitor.notifications.channel_plan import get_channel_endpoint
from facebook_monitor.notifications.channel_plan import is_channel_enabled_by_config
from facebook_monitor.notifications.discord_format import normalize_discord_single_line
from facebook_monitor.notifications.payload import normalize_notification_single_line
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


def refresh_outbox_entry_display_metadata_lines(
    *,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
) -> NotificationOutboxEntry:
    """dispatch 前用目前 target 顯示名稱修正舊 outbox metadata header。"""

    group_name = format_target_display_name(target)
    if not group_name:
        return entry
    if entry.event_kind == NotificationEventKind.MATCH:
        message = _replace_notification_line(
            entry.message,
            prefix="社團：",
            value=_format_outbox_metadata_name_for_channel(entry.channel, group_name),
        )
    elif entry.event_kind == NotificationEventKind.RUNTIME_FAILURE:
        message = _replace_notification_line(
            entry.message,
            prefix="監視項目: ",
            value=_format_outbox_metadata_name_for_channel(entry.channel, group_name),
            preserve_inline_suffix=True,
            inline_suffix_markers=(" | 錯誤類型:", " | 連續次數:", " | 狀態:"),
        )
    else:
        return entry
    if message == entry.message:
        return entry
    return replace(entry, message=message)


def _replace_notification_line(
    message: str,
    *,
    prefix: str,
    value: str,
    preserve_inline_suffix: bool = False,
    inline_suffix_markers: tuple[str, ...] = (" | ",),
) -> str:
    """只替換第一個 metadata header，避免誤改通知正文。"""

    lines = str(message or "").splitlines()
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            suffix = ""
            if preserve_inline_suffix:
                old_value = line[len(prefix) :]
                marker_indexes = [
                    old_value.find(marker)
                    for marker in inline_suffix_markers
                    if old_value.find(marker) >= 0
                ]
                separator_index = min(marker_indexes) if marker_indexes else -1
                if separator_index >= 0:
                    suffix = old_value[separator_index:]
            lines[index] = f"{prefix}{value}{suffix}"
            return "\n".join(lines)
    return message


def _format_outbox_metadata_name_for_channel(
    channel: NotificationChannel,
    value: str,
) -> str:
    """套用 channel-specific metadata 單行格式，避免 dispatch-time 修正文案破版。"""

    if channel == NotificationChannel.DISCORD:
        return normalize_discord_single_line(value)
    return normalize_notification_single_line(value)
