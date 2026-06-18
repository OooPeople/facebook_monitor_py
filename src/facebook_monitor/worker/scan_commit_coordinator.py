"""Thin coordinator for resident scan commit helpers.

職責：集中 formal resident scan commit side effects，並轉成 typed outcome。
本模組不擁有 scanner、Playwright page、scheduler policy 或通知投遞策略。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.scan_failure_policy import SCHEDULER_RUNTIME_RESTART_ACTION
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.scan_failures import TARGET_STOPPED_REASON
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.notifications.senders import DesktopSender
from facebook_monitor.notifications.senders import DiscordSender
from facebook_monitor.notifications.senders import NtfySender
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcome
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcomeKind
from facebook_monitor.worker.scan_finalize import ScanFinalizeResult
from facebook_monitor.worker.scan_failure_finalize import (
    record_guarded_scan_failure_result_for_db_async,
)
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import finalize_scan_items
from facebook_monitor.worker.scan_finalize import mark_target_idle_for_scan_commit
from facebook_monitor.worker.scan_finalize import record_guarded_skipped_scan
from facebook_monitor.worker.scan_pipeline_results import SuccessScanResult


@dataclass(frozen=True)
class FailureScanCommitRequest:
    """保存 guarded failure finalize 需要的完整輸入。"""

    db_path: Path
    target_id: str
    reason: str
    message: str
    source: ScanFailureSource
    worker_path: str
    commit_guard: ScanCommitGuard
    worker_mode: WorkerMode = WorkerMode.HEADLESS
    exception_class: str = ""
    profile_lease_state: str = ""
    page_reused: bool | None = None
    scan_request_id: str = ""
    runtime_error_message: str | None = None


def commit_guarded_idle_after_success(
    *,
    app: ApplicationContext,
    target_id: str,
    commit_guard: ScanCommitGuard,
) -> ScanCommitOutcome:
    """success finalize 後執行 guarded idle commit。"""

    committed = mark_target_idle_for_scan_commit(
        app=app,
        target_id=target_id,
        commit_guard=commit_guard,
    )
    if not committed:
        return ScanCommitOutcome(
            kind=ScanCommitOutcomeKind.GUARD_MISMATCH,
            target_id=target_id,
            reason="scan_commit_guard_mismatch",
        )
    return ScanCommitOutcome(
        kind=ScanCommitOutcomeKind.IDLE_COMMITTED,
        target_id=target_id,
    )


def commit_guarded_protective_skip(
    *,
    app: ApplicationContext,
    target_id: str,
    target: TargetDescriptor,
    metadata: dict[str, Any],
    commit_guard: ScanCommitGuard,
) -> ScanCommitOutcome:
    """執行 guarded protective skip finalize，並回傳 typed outcome。"""

    result: ScanFinalizeResult = record_guarded_skipped_scan(
        app=app,
        target=target,
        metadata=metadata,
        commit_guard=commit_guard,
    )
    return ScanCommitOutcome(
        kind=ScanCommitOutcomeKind.SKIP_COMMITTED,
        target_id=target_id,
        reason=str(result.scan_summary.get("skip_reason") or ""),
        scan_run_id=result.scan_run_id,
    )


def commit_success(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    result: SuccessScanResult,
    commit_guard: ScanCommitGuard,
    notification_sender: NtfySender = send_ntfy_notification,
    desktop_notification_sender: DesktopSender = send_desktop_notification,
    discord_notification_sender: DiscordSender = send_discord_notification,
) -> ScanCommitOutcome:
    """以 coordinator 擁有 success finalize 與 guarded idle commit。"""

    try:
        finalize_result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=list(result.items),
            item_count=result.item_count,
            metadata=dict(result.metadata),
            notification_sender=notification_sender,
            desktop_notification_sender=desktop_notification_sender,
            discord_notification_sender=discord_notification_sender,
            commit_guard=commit_guard,
        )
    except WorkerFailure as exc:
        if exc.reason == TARGET_STOPPED_REASON:
            return ScanCommitOutcome(
                kind=ScanCommitOutcomeKind.GUARD_MISMATCH,
                target_id=target.id,
                reason=exc.reason,
            )
        raise
    committed_idle = mark_target_idle_for_scan_commit(
        app=app,
        target_id=target.id,
        commit_guard=commit_guard,
    )
    if not committed_idle:
        raise WorkerFailure(
            TARGET_STOPPED_REASON,
            "target stopped before success idle commit",
        )
    return ScanCommitOutcome(
        kind=ScanCommitOutcomeKind.SUCCESS_COMMITTED,
        target_id=target.id,
        scan_run_id=finalize_result.scan_run_id,
        matched_count=finalize_result.matched_count,
        new_count=finalize_result.new_count,
    )


async def commit_failure_request_for_db_async(
    request: FailureScanCommitRequest,
) -> ScanCommitOutcome:
    """依 typed request 執行 guarded failure finalize，並回傳 typed outcome。"""

    result = await record_guarded_scan_failure_result_for_db_async(
        db_path=request.db_path,
        target_id=request.target_id,
        reason=request.reason,
        message=request.message,
        source=request.source,
        worker_path=request.worker_path,
        commit_guard=request.commit_guard,
        worker_mode=request.worker_mode,
        exception_class=request.exception_class,
        profile_lease_state=request.profile_lease_state,
        page_reused=request.page_reused,
        scan_request_id=request.scan_request_id,
        runtime_error_message=request.runtime_error_message,
    )
    if result is None:
        return ScanCommitOutcome(
            kind=ScanCommitOutcomeKind.GUARD_MISMATCH,
            target_id=request.target_id,
            reason="scan_failure_guard_mismatch",
        )
    decision = result.decision
    return ScanCommitOutcome(
        kind=ScanCommitOutcomeKind.FAILURE_COMMITTED,
        target_id=request.target_id,
        reason=decision.reason,
        scan_run_id=result.scan_run_id,
        request_runtime_restart=(
            decision.recovery_action == SCHEDULER_RUNTIME_RESTART_ACTION
        ),
        discard_page=decision.discard_page,
        failure_decision=decision,
        runtime_failure_notification_count=result.runtime_failure_notification_count,
    )


__all__ = [
    "FailureScanCommitRequest",
    "commit_failure_request_for_db_async",
    "commit_guarded_idle_after_success",
    "commit_guarded_protective_skip",
    "commit_success",
]
