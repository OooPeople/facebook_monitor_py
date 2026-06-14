"""Target runtime lifecycle service。

職責：處理 target 啟停 reset、runtime restart recovery 與 SQLite lock 補掃 state。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from facebook_monitor.application.target_runtime_access import TargetRuntimeAccess
from facebook_monitor.application.target_runtime_transitions import retry_requested_state
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now


class TargetRuntimeLifecycleService:
    """協調 target runtime 啟停與補掃 lifecycle writes。"""

    def __init__(self, access: TargetRuntimeAccess) -> None:
        self._access = access

    def reset_target_desired_state(
        self,
        target_id: str,
        desired_state: TargetDesiredState,
    ) -> TargetRuntimeState:
        """重設 target runtime state，供啟停 command 對齊 desired state。"""

        self._access.require_target(target_id)
        state = TargetRuntimeState(
            target_id=target_id,
            desired_state=desired_state,
            runtime_status=TargetRuntimeStatus.IDLE,
            display_next_due_at=None,
        )
        self._access.runtime_states.save(state)
        return state

    def restart_target_runtime(self, target_id: str) -> TargetRuntimeState:
        """套用 target「開始」時需要的 runtime reset 與立即掃描要求。"""

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
        now = utc_now()
        state = replace(
            existing_state,
            desired_state=TargetDesiredState.ACTIVE,
            runtime_status=TargetRuntimeStatus.IDLE,
            scan_requested_at=now,
            last_enqueued_at=None,
            last_started_at=None,
            last_finished_at=None,
            last_heartbeat_at=None,
            last_error="",
            last_skip_reason="",
            enqueue_reason="",
            active_worker_id="",
            active_page_id="",
            display_next_due_at=None,
            consecutive_failure_reason="",
            consecutive_failure_count=0,
            consecutive_scan_skip_reason="",
            consecutive_scan_skip_count=0,
            updated_at=now,
        )
        self._access.runtime_states.save(state)
        return state

    def force_request_target_retry_after_runtime_restart(
        self,
        target_id: str,
    ) -> TargetRuntimeState:
        """runtime restart recovery：無條件清 owner 並要求新 runtime 補掃。"""

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
        state = retry_requested_state(existing_state, now=utc_now())
        self._access.runtime_states.save(state)
        return state

    def record_guarded_target_retry_after_sqlite_lock(
        self,
        target_id: str,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """DB lock 中止後的補掃寫回；只有 running owner 相符時才更新。"""

        if not self._access.target_is_active(target_id):
            return None
        existing_state = self._access.ensure_runtime_state(target_id)
        state = retry_requested_state(existing_state, now=utc_now())
        return self._access.runtime_states.save_if_running_owner(
            state,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def record_non_running_target_retry_after_sqlite_lock(
        self,
        target_id: str,
    ) -> TargetRuntimeState | None:
        """DB lock 發生於 claim 前時，只允許非 running row 留下補掃要求。"""

        if not self._access.target_is_active(target_id):
            return None
        existing_state = self._access.ensure_runtime_state(target_id)
        state = retry_requested_state(existing_state, now=utc_now())
        return self._access.runtime_states.save_if_not_running(state)


__all__ = ["TargetRuntimeLifecycleService"]
