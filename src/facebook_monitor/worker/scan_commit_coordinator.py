"""Coordinator for resident scan commit helpers.

職責：集中 formal resident scan commit side effects、guard rejection 分類，
並轉成 typed outcome。
本模組不擁有 scanner、Playwright page、scheduler policy 或通知投遞策略。
"""

from __future__ import annotations

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.scan_failure_policy import SCHEDULER_RUNTIME_RESTART_ACTION
from facebook_monitor.core.scan_failures import TARGET_STOPPED_REASON
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcome
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcomeKind
from facebook_monitor.worker.scan_commit_permissions import ScanCommitPermission
from facebook_monitor.worker.scan_commit_permissions import ScanCommitPermissionKind
from facebook_monitor.worker.scan_commit_permissions import classify_scan_commit_permission
from facebook_monitor.worker.scan_commit_requests import FailureScanCommitRequest
from facebook_monitor.worker.scan_commit_side_effects import side_effects_for_failure
from facebook_monitor.worker.scan_commit_side_effects import side_effects_for_protective_skip
from facebook_monitor.worker.scan_commit_side_effects import side_effects_for_success
from facebook_monitor.worker.scan_commit_validation import (
    validate_protective_skip_result_for_target,
)
from facebook_monitor.worker.scan_commit_validation import (
    validate_success_scan_result_for_target,
)
from facebook_monitor.worker.scan_failure_finalize import (
    GuardedScanFailureFinalizeRejected,
    GuardedScanFailureFinalizeRejectedKind,
    record_guarded_scan_failure_result_for_db_async,
)
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import finalize_scan_items
from facebook_monitor.worker.scan_finalize import mark_target_idle_for_scan_commit
from facebook_monitor.worker.scan_finalize import record_guarded_skipped_scan
from facebook_monitor.worker.scan_pipeline_results import ProtectiveSkipScanResult
from facebook_monitor.worker.scan_pipeline_results import SuccessScanResult


def commit_guarded_protective_skip(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    result: ProtectiveSkipScanResult,
    commit_guard: ScanCommitGuard,
) -> ScanCommitOutcome:
    """執行 guarded protective skip finalize，並回傳 typed outcome。"""

    validate_protective_skip_result_for_target(target=target, result=result)
    permission = classify_scan_commit_permission(
        app=app,
        target_id=target.id,
        commit_guard=commit_guard,
    )
    if not permission.allowed:
        return _commit_rejection_outcome(target_id=target.id, permission=permission)
    finalize_result = record_guarded_skipped_scan(
        app=app,
        target=target,
        metadata=dict(result.metadata),
        commit_guard=commit_guard,
    )
    return ScanCommitOutcome(
        kind=ScanCommitOutcomeKind.SKIP_COMMITTED,
        target_id=target.id,
        side_effects=side_effects_for_protective_skip(finalize_result),
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
) -> ScanCommitOutcome:
    """以 coordinator 擁有 success finalize 與 guarded idle commit。"""

    validate_success_scan_result_for_target(target=target, result=result)
    permission = classify_scan_commit_permission(
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
        side_effects=side_effects_for_success(finalize_result),
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
        side_effects=side_effects_for_failure(result),
        reason=decision.reason,
        scan_run_id=result.scan_run_id,
        request_runtime_restart=(decision.recovery_action == SCHEDULER_RUNTIME_RESTART_ACTION),
        discard_page=decision.discard_page,
        failure_decision=decision,
        runtime_failure_notification_count=result.runtime_failure_notification_count,
    )


def _commit_rejection_outcome(
    *,
    target_id: str,
    permission: ScanCommitPermission,
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
    permission: ScanCommitPermission,
) -> ScanCommitOutcomeKind:
    """依 permission kind 選擇既有 outcome enum，不新增 public enum。"""

    if permission.kind == ScanCommitPermissionKind.TARGET_INACTIVE:
        return ScanCommitOutcomeKind.TARGET_INACTIVE
    return ScanCommitOutcomeKind.GUARD_MISMATCH


__all__ = [
    "commit_failure_request_for_db_async",
    "commit_guarded_protective_skip",
    "commit_success",
]
