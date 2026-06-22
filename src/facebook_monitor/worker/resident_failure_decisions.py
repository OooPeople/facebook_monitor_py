"""Resident attempt failure and exception decision helpers.

職責：把 attempt exception taxonomy 與 failure commit outcome 轉成 executor 後續
動作與 public result，不執行 DB、page pool 或 runtime restart side effects。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
import sqlite3

from playwright.async_api import Error as AsyncPlaywrightError
from playwright.async_api import TimeoutError as AsyncPlaywrightTimeoutError

from facebook_monitor.persistence.sqlite_retry import is_sqlite_lock_error
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


class ResidentAttemptExceptionDecisionKind(StrEnum):
    """resident attempt exception branch 的純分類。"""

    RECORD_FAILURE = "record_failure"
    SCHEDULER_STOPPING_CANCELLATION = "scheduler_stopping_cancellation"
    PRE_ADMISSION_FAILURE = "pre_admission_failure"
    SQLITE_LOCK_RETRY = "sqlite_lock_retry"
    PROPAGATE = "propagate"


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


@dataclass(frozen=True)
class ResidentAttemptExceptionDecision:
    """保存 exception taxonomy 對 terminal path 的純決策。"""

    kind: ResidentAttemptExceptionDecisionKind
    failure_record_decision: ResidentFailureRecordDecision | None
    reason: str
    exception_class: str
    outcome_kind: ResidentAttemptOutcomeKind
    reraise: bool

    def __post_init__(self) -> None:
        """防止未來直接建立不完整 terminal decision。"""

        _validate_resident_attempt_exception_decision(self)

    @classmethod
    def record_failure(
        cls,
        failure_record_decision: ResidentFailureRecordDecision,
    ) -> ResidentAttemptExceptionDecision:
        """建立需要走 guarded failure finalize 的分類結果。"""

        return cls(
            kind=ResidentAttemptExceptionDecisionKind.RECORD_FAILURE,
            failure_record_decision=failure_record_decision,
            reason="",
            exception_class=failure_record_decision.exception_class,
            outcome_kind=ResidentAttemptOutcomeKind.TARGET_INACTIVE,
            reraise=False,
        )

    @classmethod
    def pre_admission_failure(
        cls,
        *,
        reason: str,
        exception_class: str,
        outcome_kind: ResidentAttemptOutcomeKind,
        reraise: bool,
    ) -> ResidentAttemptExceptionDecision:
        """建立 claim running 前的 terminal classification。"""

        return cls(
            kind=ResidentAttemptExceptionDecisionKind.PRE_ADMISSION_FAILURE,
            failure_record_decision=None,
            reason=reason,
            exception_class=exception_class,
            outcome_kind=outcome_kind,
            reraise=reraise,
        )

    @classmethod
    def sqlite_lock_retry(cls, exception_class: str) -> ResidentAttemptExceptionDecision:
        """建立 SQLite lock retry classification。"""

        return cls(
            kind=ResidentAttemptExceptionDecisionKind.SQLITE_LOCK_RETRY,
            failure_record_decision=None,
            reason="database_locked",
            exception_class=exception_class,
            outcome_kind=ResidentAttemptOutcomeKind.TARGET_INACTIVE,
            reraise=False,
        )

    @classmethod
    def scheduler_stopping_cancellation(cls) -> ResidentAttemptExceptionDecision:
        """建立 scheduler stopping cancellation classification。"""

        return cls(
            kind=ResidentAttemptExceptionDecisionKind.SCHEDULER_STOPPING_CANCELLATION,
            failure_record_decision=None,
            reason="",
            exception_class="CancelledError",
            outcome_kind=ResidentAttemptOutcomeKind.TARGET_INACTIVE,
            reraise=True,
        )

    @classmethod
    def propagate(cls) -> ResidentAttemptExceptionDecision:
        """建立應維持原例外傳播的分類結果。"""

        return cls(
            kind=ResidentAttemptExceptionDecisionKind.PROPAGATE,
            failure_record_decision=None,
            reason="",
            exception_class="",
            outcome_kind=ResidentAttemptOutcomeKind.TARGET_INACTIVE,
            reraise=True,
        )


def _validate_resident_attempt_exception_decision(
    decision: ResidentAttemptExceptionDecision,
) -> None:
    """依 kind dispatch 到窄化 validator，避免寬鬆 terminal decision。"""

    _EXCEPTION_DECISION_VALIDATORS[decision.kind](decision)


def _validate_record_failure_exception_decision(
    decision: ResidentAttemptExceptionDecision,
) -> None:
    """驗證 guarded failure finalize decision 的不變量。"""

    record = decision.failure_record_decision
    if record is None:
        raise ValueError("record_failure decision requires failure record")
    if decision.reason:
        raise ValueError("record_failure decision must not include reason")
    if decision.exception_class != record.exception_class:
        raise ValueError("record_failure decision exception class mismatch")
    if decision.outcome_kind != ResidentAttemptOutcomeKind.TARGET_INACTIVE:
        raise ValueError("record_failure decision must use target inactive placeholder")
    if decision.reraise:
        raise ValueError("record_failure decision must not re-raise")


def _validate_pre_admission_exception_decision(
    decision: ResidentAttemptExceptionDecision,
) -> None:
    """驗證 claim running 前 terminal decision 的不變量。"""

    _require_no_failure_record(decision)
    if not decision.reason or not decision.exception_class:
        raise ValueError("pre_admission_failure decision requires reason/class")
    if decision.outcome_kind not in {
        ResidentAttemptOutcomeKind.CANCELLED,
        ResidentAttemptOutcomeKind.TARGET_INACTIVE,
    }:
        raise ValueError("pre_admission_failure decision has unsupported outcome kind")


def _validate_sqlite_lock_exception_decision(
    decision: ResidentAttemptExceptionDecision,
) -> None:
    """驗證 SQLite lock retry decision 的不變量。"""

    _require_no_failure_record(decision)
    if decision.reason != "database_locked" or not decision.exception_class:
        raise ValueError("sqlite_lock_retry decision requires lock reason/class")
    _require_target_inactive_placeholder(decision)
    if decision.reraise:
        raise ValueError("sqlite_lock_retry decision must not re-raise")


def _validate_scheduler_cancel_exception_decision(
    decision: ResidentAttemptExceptionDecision,
) -> None:
    """驗證 scheduler stopping cancellation decision 的不變量。"""

    _require_no_failure_record(decision)
    if decision.reason:
        raise ValueError("scheduler cancellation decision must not include reason")
    if decision.exception_class != "CancelledError" or not decision.reraise:
        raise ValueError("scheduler cancellation decision must re-raise")
    _require_target_inactive_placeholder(decision)


def _validate_propagate_exception_decision(
    decision: ResidentAttemptExceptionDecision,
) -> None:
    """驗證 propagate decision 的不變量。"""

    _require_no_failure_record(decision)
    if decision.reason or decision.exception_class:
        raise ValueError("propagate decision must not include terminal metadata")
    if not decision.reraise:
        raise ValueError("propagate decision must re-raise")
    _require_target_inactive_placeholder(decision)


def _require_no_failure_record(decision: ResidentAttemptExceptionDecision) -> None:
    """確認非 failure finalize decision 不帶 failure record。"""

    if decision.failure_record_decision is not None:
        raise ValueError(f"{decision.kind} decision must not include failure record")


def _require_target_inactive_placeholder(
    decision: ResidentAttemptExceptionDecision,
) -> None:
    """確認不使用 outcome_kind 的分支維持無害 placeholder。"""

    if decision.outcome_kind != ResidentAttemptOutcomeKind.TARGET_INACTIVE:
        raise ValueError(f"{decision.kind} decision has invalid outcome placeholder")


_EXCEPTION_DECISION_VALIDATORS = {
    ResidentAttemptExceptionDecisionKind.RECORD_FAILURE: (
        _validate_record_failure_exception_decision
    ),
    ResidentAttemptExceptionDecisionKind.PRE_ADMISSION_FAILURE: (
        _validate_pre_admission_exception_decision
    ),
    ResidentAttemptExceptionDecisionKind.SQLITE_LOCK_RETRY: (
        _validate_sqlite_lock_exception_decision
    ),
    ResidentAttemptExceptionDecisionKind.SCHEDULER_STOPPING_CANCELLATION: (
        _validate_scheduler_cancel_exception_decision
    ),
    ResidentAttemptExceptionDecisionKind.PROPAGATE: (
        _validate_propagate_exception_decision
    ),
}


def decide_resident_attempt_exception(
    exc: BaseException,
    *,
    has_commit_guard: bool,
    runtime_restart_requested: bool,
) -> ResidentAttemptExceptionDecision:
    """將 resident attempt exception 分類成後續 terminal path。"""

    if isinstance(exc, WorkerFailure):
        return ResidentAttemptExceptionDecision.record_failure(
            failure_record_decision_for_worker_failure(exc)
        )
    if isinstance(exc, asyncio.CancelledError):
        if runtime_restart_requested:
            return ResidentAttemptExceptionDecision.record_failure(
                failure_record_decision_for_runtime_restart_cancellation()
            )
        if not has_commit_guard:
            return ResidentAttemptExceptionDecision.pre_admission_failure(
                reason="scheduler_cancel_before_running",
                exception_class="CancelledError",
                outcome_kind=ResidentAttemptOutcomeKind.CANCELLED,
                reraise=True,
            )
        return ResidentAttemptExceptionDecision.scheduler_stopping_cancellation()
    if isinstance(exc, sqlite3.OperationalError):
        if is_sqlite_lock_error(exc):
            return ResidentAttemptExceptionDecision.sqlite_lock_retry(
                exc.__class__.__name__
            )
        return ResidentAttemptExceptionDecision.propagate()
    if isinstance(exc, (AsyncPlaywrightTimeoutError, AsyncPlaywrightError)):
        return ResidentAttemptExceptionDecision.record_failure(
            failure_record_decision_for_playwright_exception(exc)
        )
    if isinstance(exc, Exception):
        return ResidentAttemptExceptionDecision.record_failure(
            failure_record_decision_for_unknown_exception(exc)
        )
    return ResidentAttemptExceptionDecision.propagate()


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
            request_runtime_restart and decision.recovery_action == SCHEDULER_RUNTIME_RESTART_ACTION
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
    source: ScanFailureSource = "playwright" if reason != UNKNOWN_REASON else "unknown_exception"
    return ResidentFailureRecordDecision(
        reason=reason,
        message=str(exc),
        source=source,
        exception_class=exc.__class__.__name__,
        owner_changed_reason="unknown_failure_owner_changed",
    )


__all__ = [
    "ResidentAttemptExceptionDecision",
    "ResidentAttemptExceptionDecisionKind",
    "ResidentFailureRecordDecision",
    "ResidentFailureAttemptDecision",
    "decide_resident_attempt_exception",
    "decide_resident_failure_attempt",
    "failure_record_decision_for_playwright_exception",
    "failure_record_decision_for_runtime_restart_cancellation",
    "failure_record_decision_for_unknown_exception",
    "failure_record_decision_for_worker_failure",
]
