"""Thin coordinator for resident scan commit helpers.

職責：把既有 guarded scan commit helper 的結果轉成 typed outcome。
本模組不擁有 scanner、Playwright page、scheduler policy 或 notification sender。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.scan_failure_policy import SCHEDULER_RUNTIME_RESTART_ACTION
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcome
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcomeKind
from facebook_monitor.worker.scan_finalize import ScanFinalizeResult
from facebook_monitor.worker.scan_failure_finalize import record_guarded_scan_failure_for_db_async
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import mark_target_idle_for_scan_commit
from facebook_monitor.worker.scan_finalize import record_skipped_scan


def commit_idle_after_existing_success_finalize(
    *,
    app: ApplicationContext,
    target_id: str,
    commit_guard: ScanCommitGuard,
) -> ScanCommitOutcome:
    """scanner/finalize 成功後，以既有 helper 做 guarded idle commit。"""

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


def commit_skipped_existing_finalize(
    *,
    app: ApplicationContext,
    target_id: str,
    target: TargetDescriptor,
    metadata: dict[str, Any],
    commit_guard: ScanCommitGuard,
) -> ScanCommitOutcome:
    """以既有 protective skip finalize 寫入，並回傳 typed outcome。"""

    result: ScanFinalizeResult = record_skipped_scan(
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


async def commit_failure_for_db_async(
    *,
    db_path: Path,
    target_id: str,
    reason: str,
    message: str,
    source: ScanFailureSource,
    worker_path: str,
    commit_guard: ScanCommitGuard,
    worker_mode: WorkerMode = WorkerMode.HEADLESS,
    exception_class: str = "",
    profile_lease_state: str = "",
    page_reused: bool | None = None,
    scan_request_id: str = "",
    runtime_error_message: str | None = None,
) -> ScanCommitOutcome:
    """以既有 guarded failure finalize 寫入 DB，並回傳 typed outcome。"""

    decision = await record_guarded_scan_failure_for_db_async(
        db_path=db_path,
        target_id=target_id,
        reason=reason,
        message=message,
        source=source,
        worker_path=worker_path,
        commit_guard=commit_guard,
        worker_mode=worker_mode,
        exception_class=exception_class,
        profile_lease_state=profile_lease_state,
        page_reused=page_reused,
        scan_request_id=scan_request_id,
        runtime_error_message=runtime_error_message,
    )
    if decision is None:
        return ScanCommitOutcome(
            kind=ScanCommitOutcomeKind.GUARD_MISMATCH,
            target_id=target_id,
            reason="scan_failure_guard_mismatch",
        )
    return ScanCommitOutcome(
        kind=ScanCommitOutcomeKind.FAILURE_COMMITTED,
        target_id=target_id,
        reason=decision.reason,
        request_runtime_restart=(
            decision.recovery_action == SCHEDULER_RUNTIME_RESTART_ACTION
        ),
        discard_page=decision.discard_page,
        failure_decision=decision,
    )


__all__ = [
    "commit_failure_for_db_async",
    "commit_idle_after_existing_success_finalize",
    "commit_skipped_existing_finalize",
]
