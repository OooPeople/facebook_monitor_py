"""Notification outbox dispatch service。

職責：claim pending/failed outbox rows、呼叫 sender、寫回 sent/failed/skipped
結果；不負責 scan finalize enqueue，也不直接知道 worker commit 流程。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_display import format_target_display_name
from facebook_monitor.core.defaults import PYTHON_NOTIFICATION_RUNTIME_DEFAULTS
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEventKind
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.notification_channels import get_channel_definition
from facebook_monitor.core.scan_failure_policy import is_runtime_failure_notification_terminal
from facebook_monitor.notifications.channel_dispatch import dispatch_notification_outbox_entry
from facebook_monitor.notifications.channel_dispatch import record_notification_event
from facebook_monitor.notifications.channel_dispatch import (
    record_failed_notification_event_for_outbox_error,
)
from facebook_monitor.notifications.channel_plan import get_channel_endpoint
from facebook_monitor.notifications.channel_plan import is_channel_enabled_by_config
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.discord_format import normalize_discord_single_line
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.notifications.payload import normalize_notification_single_line
from facebook_monitor.notifications.safe_messages import safe_exception_message
from facebook_monitor.notifications.senders import DesktopSender
from facebook_monitor.notifications.senders import DiscordSender
from facebook_monitor.notifications.senders import NtfySender


DEFAULT_STALE_PROCESSING_SECONDS = (
    PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.stale_processing_seconds
)
DEFAULT_DISPATCH_BATCH_LIMIT = PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.dispatch_batch_limit
INVALID_RUNTIME_FAILURE_NOTIFICATION_MESSAGE = "runtime_failure_not_terminal"


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
            if _runtime_failure_outbox_entry_should_skip(entry):
                event_id = _record_skipped_runtime_failure_outbox_entry(
                    app=app,
                    target=target,
                    entry=entry,
                )
                app.repositories.notification_outbox.mark_result(
                    entry_id=entry_id,
                    status=NotificationOutboxStatus.SKIPPED,
                    attempts=attempts,
                    message=INVALID_RUNTIME_FAILURE_NOTIFICATION_MESSAGE,
                    notification_event_id=event_id,
                )
                app.repositories.notification_outbox.connection.commit()
                dispatched_count += 1
                continue
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
            entry = refresh_outbox_entry_display_metadata_lines(
                target=target,
                entry=entry,
            )
            event_id, status, result_message = dispatch_notification_outbox_entry(
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
                message=_outbox_result_message(status, result_message),
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


def _outbox_result_message(status: NotificationOutboxStatus, message: str) -> str:
    """成功通知不佔用 last_error；failed/skipped 保留可診斷 result code。"""

    if status == NotificationOutboxStatus.SENT:
        return ""
    return str(message or "").strip()


def _runtime_failure_outbox_entry_should_skip(entry: NotificationOutboxEntry) -> bool:
    """舊 pending runtime failure 若未達 terminal 門檻，dispatch 時直接略過。"""

    return (
        entry.event_kind == NotificationEventKind.RUNTIME_FAILURE
        and not is_runtime_failure_notification_terminal(
            entry.failure_reason,
            failure_count=entry.failure_count,
        )
    )


def _record_skipped_runtime_failure_outbox_entry(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    entry: NotificationOutboxEntry,
) -> int:
    """為被 policy 擋下的 runtime failure outbox 寫入 skipped event。"""

    return record_notification_event(
        app=app,
        target=target,
        item_key=entry.item_key,
        channel=entry.channel,
        status=NotificationStatus.SKIPPED,
        message=INVALID_RUNTIME_FAILURE_NOTIFICATION_MESSAGE,
        entry=entry,
    )


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


__all__ = [
    "dispatch_new_pending_notification_outbox",
    "dispatch_new_pending_notification_outbox_for_db",
    "dispatch_notification_outbox_entries",
    "recover_stale_processing_outbox",
    "refresh_outbox_entry_delivery_endpoint",
    "refresh_outbox_entry_display_metadata_lines",
    "retry_failed_notification_outbox",
]
