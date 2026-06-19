"""Coordinator for resident scan commit helpers.

職責：集中 formal resident scan commit side effects、guard rejection 分類，
並轉成 typed outcome。
本模組不擁有 scanner、Playwright page、scheduler policy 或通知投遞策略。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.scan_failure_policy import SCHEDULER_RUNTIME_RESTART_ACTION
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
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
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitSideEffects
from facebook_monitor.worker.scan_finalize import ScanFinalizeResult
from facebook_monitor.worker.scan_failure_finalize import (
    GuardedScanFailureFinalizeRejected,
    GuardedScanFailureFinalizeRejectedKind,
    GuardedScanFailureFinalizeResult,
    record_guarded_scan_failure_result_for_db_async,
)
from facebook_monitor.worker.scan_finalize import begin_scan_commit_transaction
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import finalize_scan_items
from facebook_monitor.worker.scan_finalize import mark_target_idle_for_scan_commit
from facebook_monitor.worker.scan_finalize import record_guarded_skipped_scan
from facebook_monitor.worker.scan_pipeline_results import ProtectiveSkipScanResult
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


class _ScanCommitPermissionKind(StrEnum):
    """coordinator 內部使用的 commit guard 判斷分類。"""

    ALLOWED = "allowed"
    TARGET_INACTIVE = "target_inactive"
    GUARD_MISMATCH = "guard_mismatch"


@dataclass(frozen=True)
class _ScanCommitPermission:
    """保存 commit 前 guard 判斷結果與可觀測 reason。"""

    kind: _ScanCommitPermissionKind
    reason: str = ""

    @property
    def allowed(self) -> bool:
        """回傳本輪是否允許寫入 scan commit side effect。"""

        return self.kind == _ScanCommitPermissionKind.ALLOWED


def commit_guarded_protective_skip(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    result: ProtectiveSkipScanResult,
    commit_guard: ScanCommitGuard,
) -> ScanCommitOutcome:
    """執行 guarded protective skip finalize，並回傳 typed outcome。"""

    _validate_protective_skip_result_for_target(target=target, result=result)
    permission = _classify_scan_commit_permission(
        app=app,
        target_id=target.id,
        commit_guard=commit_guard,
    )
    if not permission.allowed:
        return _commit_rejection_outcome(target_id=target.id, permission=permission)
    finalize_result: ScanFinalizeResult = record_guarded_skipped_scan(
        app=app,
        target=target,
        metadata=dict(result.metadata),
        commit_guard=commit_guard,
    )
    return ScanCommitOutcome(
        kind=ScanCommitOutcomeKind.SKIP_COMMITTED,
        target_id=target.id,
        side_effects=_side_effects_for_protective_skip(finalize_result),
        reason=str(finalize_result.scan_summary.get("skip_reason") or ""),
        scan_run_id=finalize_result.scan_run_id,
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

    _validate_success_scan_result_for_target(target=target, result=result)
    permission = _classify_scan_commit_permission(
        app=app,
        target_id=target.id,
        commit_guard=commit_guard,
    )
    if not permission.allowed:
        return _commit_rejection_outcome(target_id=target.id, permission=permission)
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
        side_effects=_side_effects_for_success(finalize_result),
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
    if isinstance(result, GuardedScanFailureFinalizeRejected):
        return _failure_rejection_outcome(target_id=request.target_id, result=result)
    decision = result.decision
    return ScanCommitOutcome(
        kind=ScanCommitOutcomeKind.FAILURE_COMMITTED,
        target_id=request.target_id,
        side_effects=_side_effects_for_failure(result),
        reason=decision.reason,
        scan_run_id=result.scan_run_id,
        request_runtime_restart=(decision.recovery_action == SCHEDULER_RUNTIME_RESTART_ACTION),
        discard_page=decision.discard_page,
        failure_decision=decision,
        runtime_failure_notification_count=result.runtime_failure_notification_count,
    )


def _side_effects_for_success(result: ScanFinalizeResult) -> ScanCommitSideEffects:
    """從 success finalize result 建立實際 side-effect 摘要。"""

    return ScanCommitSideEffects(
        wrote_scan_run=result.scan_run_id > 0,
        wrote_latest_scan=result.scan_run_id > 0,
        wrote_match_history=bool(result.history_entries),
        enqueued_match_notification_outbox=bool(result.notification_payloads),
        updated_scope_state=not result.baseline_mode or bool(result.match_results),
        updated_runtime_state=True,
    )


def _side_effects_for_protective_skip(
    result: ScanFinalizeResult,
) -> ScanCommitSideEffects:
    """從 protective skip finalize result 建立實際 side-effect 摘要。"""

    return ScanCommitSideEffects(
        wrote_scan_run=result.scan_run_id > 0,
        cleared_latest_scan=True,
        updated_runtime_state=True,
    )


def _side_effects_for_failure(
    result: GuardedScanFailureFinalizeResult,
) -> ScanCommitSideEffects:
    """從 guarded failure finalize result 建立實際 side-effect 摘要。"""

    return ScanCommitSideEffects(
        wrote_scan_run=result.scan_run_id > 0,
        enqueued_runtime_failure_notification_outbox=(
            result.runtime_failure_notification_count > 0
        ),
        updated_runtime_state=True,
    )


def _validate_protective_skip_result_for_target(
    *,
    target: TargetDescriptor,
    result: ProtectiveSkipScanResult,
) -> None:
    """確認 protective skip result 屬於本次要 commit 的 target。"""

    if result.target_id != target.id:
        raise WorkerFailure(
            "scan_result_target_mismatch",
            "scanner returned protective skip result for a different target",
        )


def _validate_success_scan_result_for_target(
    *,
    target: TargetDescriptor,
    result: SuccessScanResult,
) -> None:
    """確認 success result 與 item identity 都屬於本次 target。"""

    if result.target_id != target.id:
        raise WorkerFailure(
            "scan_result_target_mismatch",
            "scanner returned success result for a different target",
        )
    expected_item_kind = _expected_item_kind_for_target(target)
    for item in result.items:
        if item.group_id != target.group_id:
            raise WorkerFailure(
                "scan_result_target_mismatch",
                "scanner returned item for a different group",
            )
        if item.item_kind != expected_item_kind:
            raise WorkerFailure(
                "scan_result_target_mismatch",
                "scanner returned item kind that does not match target kind",
            )
        if item.raw_target_kind and item.raw_target_kind != target.target_kind.value:
            raise WorkerFailure(
                "scan_result_target_mismatch",
                "scanner returned item raw target kind that does not match target",
            )
        if (
            target.target_kind == TargetKind.COMMENTS
            and item.parent_post_id != target.parent_post_id
        ):
            raise WorkerFailure(
                "scan_result_target_mismatch",
                "scanner returned comment item for a different parent post",
            )


def _expected_item_kind_for_target(target: TargetDescriptor) -> ItemKind:
    """回傳 target kind 對應的 normalized item kind。"""

    if target.target_kind == TargetKind.COMMENTS:
        return ItemKind.COMMENT
    return ItemKind.POST


def _classify_scan_commit_permission(
    *,
    app: ApplicationContext,
    target_id: str,
    commit_guard: ScanCommitGuard | None,
) -> _ScanCommitPermission:
    """判斷 target 是否仍允許本輪 coordinator 寫入 scan side effect。"""

    begin_scan_commit_transaction(app)
    target = app.repositories.targets.get(target_id)
    if target is None:
        return _ScanCommitPermission(
            _ScanCommitPermissionKind.TARGET_INACTIVE,
            "target_missing_before_commit",
        )
    if not target.enabled or target.paused:
        return _ScanCommitPermission(
            _ScanCommitPermissionKind.TARGET_INACTIVE,
            "target_inactive_before_commit",
        )
    runtime_state = app.repositories.runtime_states.get(target_id)
    if runtime_state is None:
        return _ScanCommitPermission(
            _ScanCommitPermissionKind.GUARD_MISMATCH,
            "runtime_state_missing_before_commit",
        )
    if runtime_state.desired_state != TargetDesiredState.ACTIVE:
        return _ScanCommitPermission(
            _ScanCommitPermissionKind.TARGET_INACTIVE,
            "target_inactive_before_commit",
        )
    return _classify_runtime_commit_guard(runtime_state, commit_guard)


def _classify_runtime_commit_guard(
    runtime_state: TargetRuntimeState,
    commit_guard: ScanCommitGuard | None,
) -> _ScanCommitPermission:
    """比對 running runtime owner 是否仍符合本輪 commit guard。"""

    if commit_guard is None:
        return _ScanCommitPermission(_ScanCommitPermissionKind.ALLOWED)
    if runtime_state.runtime_status != TargetRuntimeStatus.RUNNING:
        return _ScanCommitPermission(
            _ScanCommitPermissionKind.GUARD_MISMATCH,
            "runtime_not_running_before_commit",
        )
    if runtime_state.active_worker_id != commit_guard.worker_id:
        return _ScanCommitPermission(
            _ScanCommitPermissionKind.GUARD_MISMATCH,
            "owner_changed_before_commit",
        )
    if runtime_state.last_started_at != commit_guard.started_at:
        return _ScanCommitPermission(
            _ScanCommitPermissionKind.GUARD_MISMATCH,
            "scan_started_at_changed_before_commit",
        )
    if commit_guard.page_id and runtime_state.active_page_id != commit_guard.page_id:
        return _ScanCommitPermission(
            _ScanCommitPermissionKind.GUARD_MISMATCH,
            "page_owner_changed_before_commit",
        )
    return _ScanCommitPermission(_ScanCommitPermissionKind.ALLOWED)


def _commit_rejection_outcome(
    *,
    target_id: str,
    permission: _ScanCommitPermission,
) -> ScanCommitOutcome:
    """將內部 permission rejection 轉成既有 public commit outcome。"""

    return ScanCommitOutcome(
        kind=_outcome_kind_for_commit_rejection(permission),
        target_id=target_id,
        reason=permission.reason,
    )


def _failure_rejection_outcome(
    *,
    target_id: str,
    result: GuardedScanFailureFinalizeRejected,
) -> ScanCommitOutcome:
    """將 failure finalize 的 typed rejection 轉成 public outcome。"""

    kind = (
        ScanCommitOutcomeKind.TARGET_INACTIVE
        if result.kind == GuardedScanFailureFinalizeRejectedKind.TARGET_INACTIVE
        else ScanCommitOutcomeKind.GUARD_MISMATCH
    )
    return ScanCommitOutcome(kind=kind, target_id=target_id, reason=result.reason)


def _outcome_kind_for_commit_rejection(
    permission: _ScanCommitPermission,
) -> ScanCommitOutcomeKind:
    """依 permission kind 選擇既有 outcome enum，不新增 public enum。"""

    if permission.kind == _ScanCommitPermissionKind.TARGET_INACTIVE:
        return ScanCommitOutcomeKind.TARGET_INACTIVE
    return ScanCommitOutcomeKind.GUARD_MISMATCH


__all__ = [
    "FailureScanCommitRequest",
    "commit_failure_request_for_db_async",
    "commit_guarded_protective_skip",
    "commit_success",
]
