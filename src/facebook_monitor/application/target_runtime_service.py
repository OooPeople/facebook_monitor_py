"""Target runtime application service。

職責：管理 scheduler/executor 對單一 target 的 queue/running/idle/error 狀態。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from datetime import timedelta

from facebook_monitor.core.scan_failure_policy import ScanFailureDecision
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
from facebook_monitor.core.scan_failure_policy import decide_scan_failure
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.user_messages import format_failure_message
from facebook_monitor.core.user_messages import format_failure_retry_exhausted_message
from facebook_monitor.core.user_messages import format_runtime_skip_message
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
        if existing_state.desired_state != TargetDesiredState.ACTIVE:
            skipped_state = replace(
                existing_state,
                last_skip_reason=(
                    "scan_guard_skipped: target_not_active "
                    f"desired_state={existing_state.desired_state.value}"
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

    def record_target_heartbeat(
        self,
        target_id: str,
        *,
        worker_id: str = "",
        page_id: str = "",
    ) -> TargetRuntimeState:
        """刷新 running target heartbeat，供長掃描與 stale recovery 區分。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        if existing_state.runtime_status != TargetRuntimeStatus.RUNNING:
            return existing_state
        if worker_id and existing_state.active_worker_id != worker_id:
            return existing_state
        now = utc_now()
        state = replace(
            existing_state,
            active_page_id=page_id or existing_state.active_page_id,
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

    def set_target_display_next_due_at(
        self,
        target_id: str,
        due_at: datetime | None,
    ) -> TargetRuntimeState | None:
        """更新 UI 顯示用 next due；不作為 scheduler 排程來源。"""

        if self.targets.get(target_id) is None:
            return None
        existing_state = self.ensure_runtime_state(target_id)
        if existing_state.display_next_due_at == due_at:
            return existing_state
        state = replace(
            existing_state,
            display_next_due_at=due_at,
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def reset_target_desired_state(
        self,
        target_id: str,
        desired_state: TargetDesiredState,
    ) -> TargetRuntimeState:
        """重設 target runtime state，供啟停 command 對齊 desired state。"""

        self._require_target(target_id)
        state = TargetRuntimeState(
            target_id=target_id,
            desired_state=desired_state,
            runtime_status=TargetRuntimeStatus.IDLE,
            display_next_due_at=None,
        )
        self.runtime_states.save(state)
        return state

    def restart_target_runtime(self, target_id: str) -> TargetRuntimeState:
        """套用 target「開始」時需要的 runtime reset 與立即掃描要求。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
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
            updated_at=now,
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
            scan_requested_at=_scan_request_after_current_attempt(existing_state),
            last_finished_at=utc_now(),
            last_heartbeat_at=utc_now(),
            last_error="",
            last_skip_reason="",
            enqueue_reason="",
            active_worker_id="",
            active_page_id="",
            consecutive_failure_reason="",
            consecutive_failure_count=0,
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def mark_target_retriable_failure(
        self,
        target_id: str,
        decision: ScanFailureDecision,
    ) -> TargetRuntimeState:
        """記錄本輪可重試失敗，讓 target 回到 idle 供下一輪排程。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        now = utc_now()
        state = replace(
            existing_state,
            runtime_status=TargetRuntimeStatus.IDLE,
            scan_requested_at=_scan_request_after_current_attempt(existing_state),
            last_finished_at=now,
            last_heartbeat_at=now,
            last_error="",
            last_skip_reason="",
            enqueue_reason="",
            active_worker_id="",
            active_page_id="",
            consecutive_failure_reason=decision.reason,
            consecutive_failure_count=decision.retry_streak,
            updated_at=now,
        )
        self.runtime_states.save(state)
        return state

    def mark_target_error(
        self,
        target_id: str,
        error: str,
        *,
        failure_reason: str = "",
        failure_count: int = 0,
    ) -> TargetRuntimeState:
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
            consecutive_failure_reason=failure_reason,
            consecutive_failure_count=max(failure_count, 0),
            updated_at=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def decide_scan_failure(
        self,
        target_id: str,
        reason: str,
        *,
        source: ScanFailureSource,
    ) -> ScanFailureDecision:
        """依目前 runtime streak 決定本輪 scan failure 的處置。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        return decide_scan_failure(
            reason,
            source=source,
            previous_failure_reason=existing_state.consecutive_failure_reason,
            previous_failure_count=existing_state.consecutive_failure_count,
        )

    def apply_scan_failure_decision(
        self,
        target_id: str,
        decision: ScanFailureDecision,
        error: str,
    ) -> TargetRuntimeState:
        """依共用 failure decision 更新 target runtime state。"""

        if decision.target_action == "idle":
            if decision.counts_toward_streak:
                return self.mark_target_retriable_failure(target_id, decision)
            return self.mark_target_idle(target_id)
        resolved_error = error
        if decision.counts_toward_streak:
            resolved_error = format_failure_retry_exhausted_message(
                decision.reason,
                retry_streak=decision.retry_streak,
                retry_limit=decision.retry_limit,
            )
        return self.mark_target_error(
            target_id,
            resolved_error,
            failure_reason=decision.reason if decision.counts_toward_streak else "",
            failure_count=decision.retry_streak if decision.counts_toward_streak else 0,
        )

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
                last_error=format_failure_message(
                    "stale_running",
                    f"worker heartbeat expired after {int(stale_after)} seconds",
                ),
                last_skip_reason="",
                enqueue_reason="",
                active_worker_id="",
                active_page_id="",
                consecutive_failure_reason="",
                consecutive_failure_count=0,
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
                last_skip_reason=format_runtime_skip_message(
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

    def clear_target_scan_request_if_not_newer(
        self,
        target_id: str,
        consumed_at: datetime | None,
    ) -> TargetRuntimeState:
        """清除已入隊的 scan request，但保留入隊後新送出的 request。"""

        if consumed_at is None:
            return self.clear_target_scan_request(target_id)
        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        if (
            existing_state.scan_requested_at is not None
            and existing_state.scan_requested_at > consumed_at
        ):
            return existing_state
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


def _scan_request_after_current_attempt(
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
