"""Target runtime stale recovery service。

職責：修復 stale running / queued runtime state，避免 target 永久卡在不可排程狀態。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta

from facebook_monitor.application.target_runtime_access import TargetRuntimeAccess
from facebook_monitor.application.target_runtime_transitions import (
    failure_decision_state,
)
from facebook_monitor.application.target_runtime_transitions import (
    stale_running_inactive_recovered_state,
)
from facebook_monitor.application.target_runtime_transitions import (
    stale_queued_recovered_state,
)
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failure_policy import ScanFailureDecision
from facebook_monitor.core.scan_failure_policy import decide_scan_failure
from facebook_monitor.core.scan_failures import STALE_RUNNING_REASON
from facebook_monitor.core.user_messages import format_failure_message


@dataclass(frozen=True)
class StaleRunningRecovery:
    """描述一次 stale running recovery 決策與舊 attempt owner。"""

    state: TargetRuntimeState
    previous_worker_id: str
    previous_started_at: datetime
    previous_page_id: str
    previous_heartbeat_at: datetime | None
    decision: ScanFailureDecision | None
    stale_after_seconds: float
    record_failure: bool = True


class TargetRuntimeRecoveryService:
    """執行 target runtime stale state recovery。"""

    def __init__(self, access: TargetRuntimeAccess) -> None:
        self._access = access

    def recover_stale_running_targets(
        self,
        *,
        stale_after_seconds: float,
        now: datetime | None = None,
    ) -> tuple[StaleRunningRecovery, ...]:
        """修復 heartbeat 過舊的 running target，避免永久卡住。"""

        current_time = now or utc_now()
        stale_after = max(stale_after_seconds, 1)
        stale_before = current_time - timedelta(seconds=stale_after)
        recovered: list[StaleRunningRecovery] = []
        for state in self._access.runtime_states.list_stale_running_candidates(
            stale_before=stale_before,
        ):
            heartbeat_at = state.last_heartbeat_at or state.updated_at
            if state.last_started_at is None:
                continue
            worker_id = state.active_worker_id
            started_at = state.last_started_at
            page_id = state.active_page_id
            if state.desired_state != TargetDesiredState.ACTIVE:
                decision = None
                record_failure = False
                recovered_state = stale_running_inactive_recovered_state(
                    state,
                    stale_after_seconds=stale_after,
                    now=current_time,
                )
            else:
                decision = self._decide_stale_running_failure(state)
                record_failure = True
                runtime_message = format_failure_message(
                    STALE_RUNNING_REASON,
                    f"worker heartbeat expired after {int(stale_after)} seconds",
                )
                recovered_state = failure_decision_state(
                    state,
                    decision,
                    runtime_message,
                    now=current_time,
                )
            committed_state = (
                self._access.runtime_states.save_stale_running_state_if_unchanged(
                    recovered_state,
                    worker_id=worker_id,
                    started_at=started_at,
                    page_id=page_id,
                    stale_before=stale_before,
                )
            )
            if committed_state is not None:
                recovered.append(
                    StaleRunningRecovery(
                        state=committed_state,
                        previous_worker_id=worker_id,
                        previous_started_at=started_at,
                        previous_page_id=page_id,
                        previous_heartbeat_at=heartbeat_at,
                        decision=decision,
                        stale_after_seconds=stale_after,
                        record_failure=record_failure,
                    )
                )
        return tuple(recovered)

    def recover_stale_queued_targets(
        self,
        *,
        stale_after_seconds: float,
        now: datetime | None = None,
    ) -> tuple[TargetRuntimeState, ...]:
        """將排隊過久的 target 回復 idle，避免 scheduler 永久跳過。"""

        current_time = now or utc_now()
        stale_after = max(stale_after_seconds, 1)
        stale_before = current_time - timedelta(seconds=stale_after)
        recovered: list[TargetRuntimeState] = []
        for state in self._access.runtime_states.list_stale_queued_candidates(
            stale_before=stale_before,
        ):
            recovered_state = stale_queued_recovered_state(
                state,
                stale_after_seconds=stale_after,
                now=current_time,
            )
            committed_state = (
                self._access.runtime_states.save_stale_queued_state_if_unchanged(
                    recovered_state,
                    expected_enqueued_at=state.last_enqueued_at,
                    expected_updated_at=state.updated_at,
                    stale_before=stale_before,
                )
            )
            if committed_state is not None:
                recovered.append(committed_state)
        return tuple(recovered)

    def _decide_stale_running_failure(
        self,
        state: TargetRuntimeState,
    ) -> ScanFailureDecision:
        """依目前 runtime streak 決定 stale running recovery 的 failure decision。"""

        self._access.require_target(state.target_id)
        current_state = self._access.ensure_runtime_state(state.target_id)
        return decide_scan_failure(
            STALE_RUNNING_REASON,
            source="runtime_recovery",
            previous_failure_reason=current_state.consecutive_failure_reason,
            previous_failure_count=current_state.consecutive_failure_count,
        )


__all__ = ["StaleRunningRecovery", "TargetRuntimeRecoveryService"]
