"""Target runtime state transition builders。

職責：集中純 runtime state 建構規則，讓 TargetRuntimeService 專注於 target
存在性檢查、guarded/force repository write 與 recovery orchestration。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.scan_failure_policy import ScanFailureDecision
from facebook_monitor.core.user_messages import format_failure_retry_exhausted_message
from facebook_monitor.core.user_messages import format_runtime_skip_message
from facebook_monitor.application.target_runtime_decisions import ScanSkipDecision


def retry_requested_state(
    existing_state: TargetRuntimeState,
    *,
    now: datetime,
) -> TargetRuntimeState:
    """建立 recovery 補掃 state，保留 failure streak 並清除本輪 owner。"""

    return replace(
        existing_state,
        runtime_status=TargetRuntimeStatus.IDLE,
        scan_requested_at=now,
        enqueue_reason="",
        active_worker_id="",
        active_page_id="",
        updated_at=now,
    )


def idle_state(
    existing_state: TargetRuntimeState,
    *,
    now: datetime,
) -> TargetRuntimeState:
    """建立成功完成掃描後的 idle runtime state。"""

    return replace(
        existing_state,
        runtime_status=TargetRuntimeStatus.IDLE,
        scan_requested_at=scan_request_after_current_attempt(existing_state),
        last_finished_at=now,
        last_heartbeat_at=now,
        last_error="",
        last_skip_reason="",
        enqueue_reason="",
        active_worker_id="",
        active_page_id="",
        consecutive_failure_reason="",
        consecutive_failure_count=0,
        consecutive_scan_skip_reason="",
        consecutive_scan_skip_count=0,
        updated_at=now,
    )


def scan_skipped_state(
    existing_state: TargetRuntimeState,
    decision: ScanSkipDecision,
    *,
    now: datetime,
) -> TargetRuntimeState:
    """建立 skipped scan 後的 idle state，避免誤清未恢復的 failure streak。"""

    return replace(
        existing_state,
        runtime_status=TargetRuntimeStatus.IDLE,
        scan_requested_at=scan_request_after_current_attempt(existing_state),
        last_finished_at=now,
        last_heartbeat_at=now,
        last_error="",
        last_skip_reason=(
            f"{decision.reason}: skip {decision.skip_streak}/{decision.skip_limit}"
        ),
        enqueue_reason="",
        active_worker_id="",
        active_page_id="",
        consecutive_scan_skip_reason=decision.reason,
        consecutive_scan_skip_count=decision.skip_streak,
        updated_at=now,
    )


def retriable_failure_state(
    existing_state: TargetRuntimeState,
    decision: ScanFailureDecision,
    *,
    now: datetime,
) -> TargetRuntimeState:
    """建立可重試失敗後回到 idle 的 runtime state。"""

    return replace(
        existing_state,
        runtime_status=TargetRuntimeStatus.IDLE,
        scan_requested_at=(
            now if decision.auto_restart else scan_request_after_current_attempt(existing_state)
        ),
        last_finished_at=now,
        last_heartbeat_at=now,
        last_error="",
        last_skip_reason=(
            f"{decision.recovery_action}: retry "
            f"{decision.retry_streak}/{decision.retry_limit}"
            if decision.recovery_action
            else ""
        ),
        enqueue_reason="",
        active_worker_id="",
        active_page_id="",
        consecutive_failure_reason=decision.reason,
        consecutive_failure_count=decision.retry_streak,
        consecutive_scan_skip_reason="",
        consecutive_scan_skip_count=0,
        updated_at=now,
    )


def error_state(
    existing_state: TargetRuntimeState,
    error: str,
    *,
    failure_reason: str,
    failure_count: int,
    now: datetime,
) -> TargetRuntimeState:
    """建立掃描失敗後的 error runtime state。"""

    return replace(
        existing_state,
        runtime_status=TargetRuntimeStatus.ERROR,
        scan_requested_at=None,
        last_finished_at=now,
        last_heartbeat_at=now,
        last_error=error,
        last_skip_reason="",
        enqueue_reason="",
        active_worker_id="",
        active_page_id="",
        consecutive_failure_reason=failure_reason,
        consecutive_failure_count=max(failure_count, 0),
        consecutive_scan_skip_reason="",
        consecutive_scan_skip_count=0,
        updated_at=now,
    )


def failure_decision_state(
    existing_state: TargetRuntimeState,
    decision: ScanFailureDecision,
    error: str,
    *,
    now: datetime,
) -> TargetRuntimeState:
    """依 failure decision 建立 runtime state，供一般與 stale recovery 共用。"""

    if decision.target_action == "idle":
        if decision.counts_toward_streak:
            return retriable_failure_state(existing_state, decision, now=now)
        return idle_state(existing_state, now=now)
    resolved_error = error
    if decision.counts_toward_streak:
        resolved_error = format_failure_retry_exhausted_message(
            decision.reason,
            retry_streak=decision.retry_streak,
            retry_limit=decision.retry_limit,
        )
    return error_state(
        existing_state,
        resolved_error,
        failure_reason=decision.reason if decision.counts_toward_streak else "",
        failure_count=decision.retry_streak if decision.counts_toward_streak else 0,
        now=now,
    )


def stale_queued_recovered_state(
    state: TargetRuntimeState,
    *,
    stale_after_seconds: float,
    now: datetime,
) -> TargetRuntimeState:
    """建立 queued 過久後回到 idle 的 recovery state。"""

    return replace(
        state,
        runtime_status=TargetRuntimeStatus.IDLE,
        last_error="",
        last_skip_reason=format_runtime_skip_message(
            "stale_queued_recovered: executor queue wait expired "
            f"after {int(stale_after_seconds)} seconds"
        ),
        enqueue_reason="",
        active_worker_id="",
        active_page_id="",
        updated_at=now,
    )


def stale_running_inactive_recovered_state(
    state: TargetRuntimeState,
    *,
    stale_after_seconds: float,
    now: datetime,
) -> TargetRuntimeState:
    """建立 inactive target 的 stale running owner cleanup state。"""

    return replace(
        state,
        runtime_status=TargetRuntimeStatus.IDLE,
        scan_requested_at=None,
        last_error="",
        last_skip_reason=(
            "stale_running_inactive_recovered: running owner expired "
            f"after {int(stale_after_seconds)} seconds"
        ),
        enqueue_reason="",
        active_worker_id="",
        active_page_id="",
        updated_at=now,
    )


def scan_request_after_current_attempt(
    state: TargetRuntimeState,
) -> datetime | None:
    """保留掃描進行中才新送出的 scan-once 要求。"""

    if state.scan_requested_at is None:
        return None
    if state.last_enqueued_at is None and state.last_started_at is None:
        return state.scan_requested_at
    if state.last_enqueued_at is not None and state.scan_requested_at > state.last_enqueued_at:
        return state.scan_requested_at
    if state.last_started_at is not None and state.scan_requested_at > state.last_started_at:
        return state.scan_requested_at
    return None


__all__ = [
    "error_state",
    "failure_decision_state",
    "idle_state",
    "retriable_failure_state",
    "retry_requested_state",
    "scan_request_after_current_attempt",
    "scan_skipped_state",
    "stale_running_inactive_recovered_state",
    "stale_queued_recovered_state",
]
