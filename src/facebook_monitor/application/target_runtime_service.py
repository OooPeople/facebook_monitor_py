"""Target runtime application service。

職責：管理 scheduler/executor 對單一 target 的 queue/running/idle/error 狀態。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from datetime import timedelta

from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.repositories.target_runtime_state import (
    TargetRuntimeStateRepository,
)
from facebook_monitor.persistence.repositories.targets import TargetRepository


class TargetRuntimeService:
    """協調 target runtime state repository。"""

    def __init__(
        self,
        targets: TargetRepository,
        runtime_states: TargetRuntimeStateRepository,
    ) -> None:
        self.targets = targets
        self.runtime_states = runtime_states

    def ensure_runtime_state(self, target_id: str) -> TargetRuntimeState:
        """確保 target 已有 runtime state，供 scheduler/UI 查詢。"""

        existing_state = self.runtime_states.get(target_id)
        if existing_state:
            return existing_state
        target = self.targets.get(target_id)
        desired_state = (
            TargetDesiredState.ACTIVE
            if target is not None and target.enabled and not target.paused
            else TargetDesiredState.STOPPED
        )
        state = TargetRuntimeState(target_id=target_id, desired_state=desired_state)
        self.runtime_states.save(state)
        return state

    def mark_target_queued(self, target_id: str, reason: str) -> TargetRuntimeState:
        """標記單一 target 已進入 executor queue，等待 worker slot。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        state = replace(
            existing_state,
            runtime_status=TargetRuntimeStatus.QUEUED,
            last_enqueued_at=utc_now(),
            last_error="",
            last_skip_reason="",
            enqueue_reason=reason,
            active_worker_id="",
            active_page_id="",
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def mark_target_running(
        self,
        target_id: str,
        worker_id: str,
        *,
        page_id: str = "",
    ) -> TargetRuntimeState:
        """標記單一 target 正由 scheduler/worker 掃描中。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        state = replace(
            existing_state,
            runtime_status=TargetRuntimeStatus.RUNNING,
            last_started_at=utc_now(),
            last_heartbeat_at=utc_now(),
            last_error="",
            last_skip_reason="",
            active_worker_id=worker_id,
            active_page_id=page_id,
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def try_mark_target_running(
        self,
        target_id: str,
        worker_id: str,
        *,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """嘗試取得單一 target scan lock；已 running 時記錄 skip reason。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        if existing_state.runtime_status == TargetRuntimeStatus.RUNNING:
            skipped_state = replace(
                existing_state,
                last_skip_reason=(
                    "scan_guard_skipped: target_already_running "
                    f"active_worker_id={existing_state.active_worker_id}"
                ),
                scan_guard_count=existing_state.scan_guard_count + 1,
                updated_at=utc_now(),
            )
            self.runtime_states.save(skipped_state)
            return None
        return self.mark_target_running(target_id, worker_id, page_id=page_id)

    def mark_target_page_reloaded(
        self,
        target_id: str,
        *,
        page_id: str = "",
        reloaded_at: datetime | None = None,
    ) -> TargetRuntimeState:
        """記錄 resident page 已完成 reload/goto，供 UI 診斷 page ownership。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        now = utc_now()
        state = replace(
            existing_state,
            active_page_id=page_id or existing_state.active_page_id,
            last_page_reloaded_at=reloaded_at or now,
            last_heartbeat_at=now,
            updated_at=now,
        )
        self.runtime_states.save(state)
        return state

    def record_scan_guard_skip(self, target_id: str, reason: str) -> TargetRuntimeState:
        """記錄 target 被 queue/executor guard 擋下的原因。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        state = replace(
            existing_state,
            last_skip_reason=reason,
            scan_guard_count=existing_state.scan_guard_count + 1,
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def mark_target_idle(self, target_id: str) -> TargetRuntimeState:
        """標記單一 target 已完成本輪掃描並回到 idle。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        state = replace(
            existing_state,
            runtime_status=TargetRuntimeStatus.IDLE,
            scan_requested_at=None,
            last_finished_at=utc_now(),
            last_heartbeat_at=utc_now(),
            last_error="",
            last_skip_reason="",
            enqueue_reason="",
            active_worker_id="",
            active_page_id="",
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def mark_target_error(self, target_id: str, error: str) -> TargetRuntimeState:
        """標記單一 target 本輪掃描發生錯誤。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        state = replace(
            existing_state,
            runtime_status=TargetRuntimeStatus.ERROR,
            scan_requested_at=None,
            last_finished_at=utc_now(),
            last_heartbeat_at=utc_now(),
            last_error=error,
            last_skip_reason="",
            enqueue_reason="",
            active_worker_id="",
            active_page_id="",
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def recover_stale_running_targets(
        self,
        *,
        stale_after_seconds: float,
        now: datetime | None = None,
    ) -> tuple[TargetRuntimeState, ...]:
        """將 heartbeat 過舊的 running target 標成 error，避免永久卡住。"""

        current_time = now or utc_now()
        stale_after = max(stale_after_seconds, 1)
        recovered: list[TargetRuntimeState] = []
        for state in self.runtime_states.list_all():
            if state.runtime_status != TargetRuntimeStatus.RUNNING:
                continue
            heartbeat_at = state.last_heartbeat_at or state.updated_at
            if current_time - heartbeat_at <= timedelta(seconds=stale_after):
                continue
            recovered_state = replace(
                state,
                runtime_status=TargetRuntimeStatus.ERROR,
                scan_requested_at=None,
                last_finished_at=current_time,
                last_error=(
                    "stale_running: worker heartbeat expired "
                    f"after {int(stale_after)} seconds"
                ),
                last_skip_reason="",
                enqueue_reason="",
                active_worker_id="",
                active_page_id="",
                updated_at=current_time,
            )
            self.runtime_states.save(recovered_state)
            recovered.append(recovered_state)
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
        recovered: list[TargetRuntimeState] = []
        for state in self.runtime_states.list_all():
            if state.runtime_status != TargetRuntimeStatus.QUEUED:
                continue
            enqueued_at = state.last_enqueued_at or state.updated_at
            if current_time - enqueued_at <= timedelta(seconds=stale_after):
                continue
            recovered_state = replace(
                state,
                runtime_status=TargetRuntimeStatus.IDLE,
                last_error="",
                last_skip_reason=(
                    "stale_queued_recovered: executor queue wait expired "
                    f"after {int(stale_after)} seconds"
                ),
                enqueue_reason="",
                active_worker_id="",
                active_page_id="",
                updated_at=current_time,
            )
            self.runtime_states.save(recovered_state)
            recovered.append(recovered_state)
        return tuple(recovered)

    def request_target_scan(self, target_id: str) -> TargetRuntimeState:
        """要求 scheduler 下一輪立即掃描 target，不修改 seen 狀態。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        state = replace(
            existing_state,
            scan_requested_at=utc_now(),
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def clear_target_scan_request(self, target_id: str) -> TargetRuntimeState:
        """清除已被 scheduler 消化的立即掃描要求。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        state = replace(
            existing_state,
            scan_requested_at=None,
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def _require_target(self, target_id: str) -> None:
        """確認 target 存在。"""

        if self.targets.get(target_id) is None:
            raise ValueError(f"Target not found: {target_id}")
