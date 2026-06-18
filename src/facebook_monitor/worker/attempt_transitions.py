"""Side-effect-free resident attempt terminal transitions.

職責：把 commit outcome 與 attempt identity 映射成 internal outcome 與
cleanup plan；不執行 DB、page、queue 或 scheduler side effects。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.worker.attempt_cleanup import ResidentAttemptCleanupPlan
from facebook_monitor.worker.attempt_outcomes import ResidentAttemptOutcome
from facebook_monitor.worker.attempt_outcomes import ResidentAttemptOutcomeKind
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcome
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcomeKind


@dataclass(frozen=True)
class ResidentAttemptTerminalTransition:
    """保存 terminal transition 的純資料結果。"""

    outcome: ResidentAttemptOutcome
    cleanup_plan: ResidentAttemptCleanupPlan


def transition_from_scan_commit_outcome(
    *,
    target_id: str,
    owner_key: str,
    page_id: str,
    commit_outcome: ScanCommitOutcome,
    opened_page: bool,
    reused_page: bool,
) -> ResidentAttemptTerminalTransition:
    """依 scan commit outcome 建立 terminal attempt outcome 與 cleanup plan。"""

    cleanup_plan = ResidentAttemptCleanupPlan.for_attempt(
        target_id=target_id,
        owner_key=owner_key,
        page_id=page_id,
    )
    if commit_outcome.kind == ScanCommitOutcomeKind.IDLE_COMMITTED:
        outcome = ResidentAttemptOutcome.succeeded(
            target_id=target_id,
            opened_page=opened_page,
            reused_page=reused_page,
        )
    elif commit_outcome.kind == ScanCommitOutcomeKind.SUCCESS_COMMITTED:
        raise NotImplementedError(
            "SUCCESS_COMMITTED requires a scanner-owned finalize migration plan"
        )
    elif commit_outcome.kind == ScanCommitOutcomeKind.SKIP_COMMITTED:
        outcome = ResidentAttemptOutcome.skipped(
            target_id=target_id,
            kind=ResidentAttemptOutcomeKind.SKIPPED,
            reason=commit_outcome.reason,
        )
    elif commit_outcome.kind == ScanCommitOutcomeKind.FAILURE_COMMITTED:
        outcome = ResidentAttemptOutcome.failed(
            target_id=target_id,
            reason=commit_outcome.reason,
            request_runtime_restart=commit_outcome.request_runtime_restart,
            opened_page=opened_page,
            reused_page=reused_page,
        )
    elif commit_outcome.kind == ScanCommitOutcomeKind.SQLITE_LOCK_RETRY:
        outcome = ResidentAttemptOutcome.skipped(
            target_id=target_id,
            kind=ResidentAttemptOutcomeKind.SQLITE_LOCK_RETRY,
            reason=commit_outcome.reason,
            opened_page=opened_page,
            reused_page=reused_page,
        )
    elif commit_outcome.kind == ScanCommitOutcomeKind.TARGET_INACTIVE:
        outcome = ResidentAttemptOutcome.skipped(
            target_id=target_id,
            kind=ResidentAttemptOutcomeKind.TARGET_INACTIVE,
            reason=commit_outcome.reason,
        )
    else:
        outcome = ResidentAttemptOutcome.skipped(
            target_id=target_id,
            kind=ResidentAttemptOutcomeKind.OWNER_CHANGED,
            reason=commit_outcome.reason or "scan_commit_guard_mismatch",
        )
    return ResidentAttemptTerminalTransition(
        outcome=outcome,
        cleanup_plan=cleanup_plan,
    )


def transition_from_attempt_outcome(
    *,
    target_id: str,
    owner_key: str,
    page_id: str,
    outcome: ResidentAttemptOutcome,
) -> ResidentAttemptTerminalTransition:
    """把非 scan-commit branch 的 terminal outcome 包成 transition。"""

    return ResidentAttemptTerminalTransition(
        outcome=outcome,
        cleanup_plan=ResidentAttemptCleanupPlan.for_attempt(
            target_id=target_id,
            owner_key=owner_key,
            page_id=page_id,
        ),
    )


__all__ = [
    "ResidentAttemptTerminalTransition",
    "transition_from_attempt_outcome",
    "transition_from_scan_commit_outcome",
]
