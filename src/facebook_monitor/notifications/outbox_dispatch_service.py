"""Notification outbox dispatch service。

職責：claim pending/failed outbox rows、呼叫 sender、寫回 sent/failed/skipped
結果；不負責 scan finalize enqueue，也不直接知道 worker commit 流程。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.defaults import PYTHON_NOTIFICATION_RUNTIME_DEFAULTS
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.notifications.channel_dispatch import dispatch_notification_outbox_entry
from facebook_monitor.notifications.channel_dispatch import (
    record_failed_notification_event_for_outbox_error,
)
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.outbox_dispatch_results import (
    INVALID_RUNTIME_FAILURE_NOTIFICATION_MESSAGE,
)
from facebook_monitor.notifications.outbox_dispatch_results import outbox_result_message
from facebook_monitor.notifications.outbox_dispatch_results import (
    record_skipped_runtime_failure_outbox_entry,
)
from facebook_monitor.notifications.outbox_dispatch_results import (
    runtime_failure_outbox_entry_should_skip,
)
from facebook_monitor.notifications.outbox_dispatch_models import (
    PendingNotificationOutboxDispatchResult,
)
from facebook_monitor.notifications.outbox_entry_refresh import (
    refresh_outbox_entry_delivery_endpoint,
)
from facebook_monitor.notifications.outbox_entry_refresh import (
    refresh_outbox_entry_display_metadata_lines,
)
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.notifications.safe_messages import safe_exception_message
from facebook_monitor.notifications.senders import DesktopSender
from facebook_monitor.notifications.senders import DiscordSender
from facebook_monitor.notifications.senders import NtfySender


DEFAULT_STALE_PROCESSING_SECONDS = (
    PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.stale_processing_seconds
)
DEFAULT_DISPATCH_BATCH_LIMIT = PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.dispatch_batch_limit


@dataclass(frozen=True)
class _PreparedOutboxDispatch:
    """保存單筆 outbox dispatch 已載入的 target 與 attempt 語義。"""

    entry_id: int
    entry: NotificationOutboxEntry
    target: TargetDescriptor
    attempts: int


@dataclass(frozen=True)
class _OutboxDispatchOutcome:
    """保存單筆 outbox dispatch 可寫回 repository 的結果。"""

    status: NotificationOutboxStatus
    message: str
    notification_event_id: int | None


def dispatch_new_pending_notification_outbox(
    *,
    app: ApplicationContext,
    ntfy_sender: NtfySender = send_ntfy_notification,
    desktop_sender: DesktopSender = send_desktop_notification,
    discord_sender: DiscordSender = send_discord_notification,
    stale_processing_seconds: float = DEFAULT_STALE_PROCESSING_SECONDS,
    batch_limit: int = DEFAULT_DISPATCH_BATCH_LIMIT,
    should_stop: Callable[[], bool] | None = None,
    max_batches: int | None = None,
) -> PendingNotificationOutboxDispatchResult:
    """Claim 並發送 pending outbox events，回傳本輪 drain 邊界。"""

    app.repositories.notification_outbox.recover_stale_processing(
        older_than_seconds=stale_processing_seconds,
    )
    if max_batches is not None and max_batches <= 0:
        return PendingNotificationOutboxDispatchResult(
            dispatched_count=0,
            claimed_count=0,
            batch_count=0,
        )
    effective_batch_limit = max(int(batch_limit), 1)
    dispatched_count = 0
    claimed_count = 0
    batch_count = 0
    last_batch_was_full = False
    while True:
        if should_stop is not None and should_stop():
            return PendingNotificationOutboxDispatchResult(
                dispatched_count=dispatched_count,
                claimed_count=claimed_count,
                batch_count=batch_count,
                stopped=True,
            )
        if max_batches is not None and batch_count >= max_batches:
            return PendingNotificationOutboxDispatchResult(
                dispatched_count=dispatched_count,
                claimed_count=claimed_count,
                batch_count=batch_count,
                reached_batch_limit=last_batch_was_full,
            )
        entries = app.repositories.notification_outbox.claim_pending(
            limit=effective_batch_limit,
        )
        if not entries:
            return PendingNotificationOutboxDispatchResult(
                dispatched_count=dispatched_count,
                claimed_count=claimed_count,
                batch_count=batch_count,
            )
        claimed_count += len(entries)
        last_batch_was_full = len(entries) >= effective_batch_limit
        dispatched_count += dispatch_notification_outbox_entries(
            app=app,
            entries=entries,
            ntfy_sender=ntfy_sender,
            desktop_sender=desktop_sender,
            discord_sender=discord_sender,
        )
        batch_count += 1


def dispatch_new_pending_notification_outbox_for_db(
    *,
    db_path: Path,
    ntfy_sender: NtfySender = send_ntfy_notification,
    desktop_sender: DesktopSender = send_desktop_notification,
    discord_sender: DiscordSender = send_discord_notification,
    stale_processing_seconds: float = DEFAULT_STALE_PROCESSING_SECONDS,
    batch_limit: int = DEFAULT_DISPATCH_BATCH_LIMIT,
    should_stop: Callable[[], bool] | None = None,
    max_batches: int | None = None,
) -> PendingNotificationOutboxDispatchResult:
    """用新的 application context drain pending outbox，並回傳 bounded 結果。"""

    with SqliteApplicationContext(db_path) as dispatch_app:
        return dispatch_new_pending_notification_outbox(
            app=dispatch_app,
            ntfy_sender=ntfy_sender,
            desktop_sender=desktop_sender,
            discord_sender=discord_sender,
            stale_processing_seconds=stale_processing_seconds,
            batch_limit=batch_limit,
            should_stop=should_stop,
            max_batches=max_batches,
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
        entry_id = int(entry.id)
        attempts = entry.attempts + 1
        target: TargetDescriptor | None = None
        try:
            prepared = prepare_outbox_entry_for_dispatch(
                app=app,
                entry=entry,
                entry_id=entry_id,
                attempts=attempts,
            )
            target = prepared.target
            outcome = dispatch_prepared_outbox_entry(
                app=app,
                prepared=prepared,
                ntfy_sender=ntfy_sender,
                desktop_sender=desktop_sender,
                discord_sender=discord_sender,
            )
            record_outbox_dispatch_result(
                app=app,
                prepared=prepared,
                outcome=outcome,
            )
            app.repositories.notification_outbox.connection.commit()
            dispatched_count += 1
        except Exception as exc:
            record_outbox_dispatch_failure(
                app=app,
                entry=entry,
                entry_id=entry_id,
                attempts=attempts,
                target=target,
                exc=exc,
            )
            app.repositories.notification_outbox.connection.commit()
    return dispatched_count


def prepare_outbox_entry_for_dispatch(
    *,
    app: ApplicationContext,
    entry: NotificationOutboxEntry,
    entry_id: int,
    attempts: int,
) -> _PreparedOutboxDispatch:
    """載入 dispatch 所需 target；target 已刪除時交由 failure recorder 寫回。"""

    target = app.repositories.targets.get(entry.target_id)
    if target is None:
        raise ValueError(f"Target not found: {entry.target_id}")
    return _PreparedOutboxDispatch(
        entry_id=entry_id,
        entry=entry,
        target=target,
        attempts=attempts,
    )


def dispatch_prepared_outbox_entry(
    *,
    app: ApplicationContext,
    prepared: _PreparedOutboxDispatch,
    ntfy_sender: NtfySender,
    desktop_sender: DesktopSender,
    discord_sender: DiscordSender,
) -> _OutboxDispatchOutcome:
    """依單筆 outbox entry 狀態執行 skip 或實際 channel dispatch。"""

    if runtime_failure_outbox_entry_should_skip(prepared.entry):
        event_id = record_skipped_runtime_failure_outbox_entry(
            app=app,
            target=prepared.target,
            entry=prepared.entry,
        )
        return _OutboxDispatchOutcome(
            status=NotificationOutboxStatus.SKIPPED,
            message=INVALID_RUNTIME_FAILURE_NOTIFICATION_MESSAGE,
            notification_event_id=event_id,
        )
    refreshed_entry = mark_processing_and_refresh_outbox_entry(
        app=app,
        prepared=prepared,
    )
    event_id, status, result_message = dispatch_notification_outbox_entry(
        app=app,
        target=prepared.target,
        entry=refreshed_entry,
        ntfy_sender=ntfy_sender,
        desktop_sender=desktop_sender,
        discord_sender=discord_sender,
    )
    return _OutboxDispatchOutcome(
        status=status,
        message=outbox_result_message(status, result_message),
        notification_event_id=event_id,
    )


def mark_processing_and_refresh_outbox_entry(
    *,
    app: ApplicationContext,
    prepared: _PreparedOutboxDispatch,
) -> NotificationOutboxEntry:
    """標記 processing 後刷新 endpoint 與顯示 metadata，保留既有 commit 邊界。"""

    app.repositories.notification_outbox.touch_processing(
        entry_id=prepared.entry_id,
        status=prepared.entry.status,
    )
    app.repositories.notification_outbox.connection.commit()
    entry = refresh_outbox_entry_delivery_endpoint(
        app=app,
        target=prepared.target,
        entry=prepared.entry,
    )
    return refresh_outbox_entry_display_metadata_lines(
        target=prepared.target,
        entry=entry,
    )


def record_outbox_dispatch_result(
    *,
    app: ApplicationContext,
    prepared: _PreparedOutboxDispatch,
    outcome: _OutboxDispatchOutcome,
) -> None:
    """將單筆 outbox dispatch 成功或 skip 結果寫回 repository。"""

    app.repositories.notification_outbox.mark_result(
        entry_id=prepared.entry_id,
        status=outcome.status,
        attempts=prepared.attempts,
        message=outcome.message,
        notification_event_id=outcome.notification_event_id,
    )


def record_outbox_dispatch_failure(
    *,
    app: ApplicationContext,
    entry: NotificationOutboxEntry,
    entry_id: int,
    attempts: int,
    target: TargetDescriptor | None,
    exc: Exception,
) -> None:
    """將單筆 outbox dispatch 例外轉成 failed outbox row 與事件紀錄。"""

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


__all__ = [
    "dispatch_new_pending_notification_outbox",
    "dispatch_new_pending_notification_outbox_for_db",
    "dispatch_notification_outbox_entries",
    "recover_stale_processing_outbox",
    "refresh_outbox_entry_delivery_endpoint",
    "refresh_outbox_entry_display_metadata_lines",
    "retry_failed_notification_outbox",
]
