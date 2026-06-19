"""Resident failure commit outcome decision helpers.

職責：把 failure commit outcome 轉成 executor 後續動作與 public result，
不執行 DB、page pool 或 runtime restart side effects。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.core.scan_failure_policy import SCHEDULER_RUNTIME_RESTART_ACTION
from facebook_monitor.core.scan_failure_policy import ScanFailureDecision
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
from facebook_monitor.worker.attempt_outcomes import ResidentAttemptOutcome
from facebook_monitor.worker.attempt_outcomes import ResidentAttemptOutcomeKind
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.errors import classify_playwright_exception
from facebook_monitor.worker.errors import classify_wrapped_playwright_exception
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcome
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcomeKind


@dataclass(frozen=True)
class ResidentFailureRecordDecision:
    """保存 exception branch 要交給 failure finalize 的純決策。"""

    reason: str
    message: str
    source: ScanFailureSource
    exception_class: str
    owner_changed_reason: str
    request_runtime_restart: bool = True
    include_page_counts_in_log: bool = True
    include_page_counts_in_result: bool = False


@dataclass(frozen=True)
class ResidentFailureAttemptDecision:
    """保存 failure commit 後 executor 需要執行的後續決策。"""

    outcome: ResidentAttemptOutcome
    failure_decision: ScanFailureDecision | None
    discard_page: bool = False
    request_runtime_restart: bool = False
    owner_changed: bool = False


def decide_resident_failure_attempt(
    *,
    target_id: str,
    commit_outcome: ScanCommitOutcome,
    owner_changed_reason: str,
    source: ScanFailureSource,
    exception_class: str,
    request_runtime_restart: bool,
    opened_page: bool,
    reused_page: bool,
    include_page_counts_in_result: bool,
) -> ResidentFailureAttemptDecision:
    """依 failure commit outcome 建立 executor failure 後續決策。"""

    decision = commit_outcome.failure_decision
    if decision is None:
        target_inactive = commit_outcome.kind == ScanCommitOutcomeKind.TARGET_INACTIVE
        kind = (
            ResidentAttemptOutcomeKind.TARGET_INACTIVE
            if target_inactive
            else ResidentAttemptOutcomeKind.OWNER_CHANGED
        )
        reason = commit_outcome.reason if target_inactive else owner_changed_reason
        return ResidentFailureAttemptDecision(
            outcome=ResidentAttemptOutcome.skipped(
                target_id=target_id,
                kind=kind,
                reason=reason,
            ),
            failure_decision=None,
            owner_changed=True,
        )

    outcome_factory = (
        ResidentAttemptOutcome.runtime_restart_requested
        if decision.recovery_action == SCHEDULER_RUNTIME_RESTART_ACTION
        else ResidentAttemptOutcome.failed
    )
    page_counts = (
        {
            "opened_page": opened_page,
            "reused_page": reused_page,
        }
        if include_page_counts_in_result
        else {}
    )
    return ResidentFailureAttemptDecision(
        outcome=outcome_factory(
            target_id=target_id,
            reason=decision.reason,
            source=source,
            exception_class=exception_class,
            request_runtime_restart=request_runtime_restart,
            **page_counts,
        ),
        failure_decision=decision,
        discard_page=decision.discard_page,
        request_runtime_restart=(
            request_runtime_restart
            and decision.recovery_action == SCHEDULER_RUNTIME_RESTART_ACTION
        ),
    )


def failure_record_decision_for_worker_failure(
    exc: WorkerFailure,
) -> ResidentFailureRecordDecision:
    """把 scanner WorkerFailure 分類成 failure finalize request。"""

    return ResidentFailureRecordDecision(
        reason=exc.reason,
        message=str(exc),
        source="worker_failure",
        exception_class=exc.__class__.__name__,
        owner_changed_reason="worker_failure_owner_changed",
        include_page_counts_in_result=True,
    )


def failure_record_decision_for_runtime_restart_cancellation() -> ResidentFailureRecordDecision:
    """runtime restart cancellation 仍記 scheduler_runtime，但不再重複 request。"""

    return ResidentFailureRecordDecision(
        reason=SCHEDULER_RUNTIME_REASON,
        message="browser runtime restart requested",
        source="unknown_exception",
        exception_class="CancelledError",
        owner_changed_reason="runtime_restart_cancel_owner_changed",
        request_runtime_restart=False,
        include_page_counts_in_log=False,
    )


def failure_record_decision_for_playwright_exception(
    exc: Exception,
) -> ResidentFailureRecordDecision:
    """把 Playwright exception 分類成 failure finalize request。"""

    return ResidentFailureRecordDecision(
        reason=classify_playwright_exception(exc),
        message=str(exc),
        source="playwright",
        exception_class=exc.__class__.__name__,
        owner_changed_reason="playwright_failure_owner_changed",
    )


def failure_record_decision_for_unknown_exception(
    exc: Exception,
) -> ResidentFailureRecordDecision:
    """把一般 exception 分類成 failure finalize request。"""

    reason = classify_wrapped_playwright_exception(exc)
    source: ScanFailureSource = (
        "playwright" if reason != UNKNOWN_REASON else "unknown_exception"
    )
    return ResidentFailureRecordDecision(
        reason=reason,
        message=str(exc),
        source=source,
        exception_class=exc.__class__.__name__,
        owner_changed_reason="unknown_failure_owner_changed",
    )


__all__ = [
    "ResidentFailureRecordDecision",
    "ResidentFailureAttemptDecision",
    "decide_resident_failure_attempt",
    "failure_record_decision_for_playwright_exception",
    "failure_record_decision_for_runtime_restart_cancellation",
    "failure_record_decision_for_unknown_exception",
    "failure_record_decision_for_worker_failure",
]
