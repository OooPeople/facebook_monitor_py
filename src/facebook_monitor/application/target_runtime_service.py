"""Target runtime application service。

職責：管理 scheduler/executor 對單一 target 的 queue/running/idle/error 狀態。
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
from datetime import timedelta

from facebook_monitor.core.scan_failure_policy import ScanFailureDecision
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
from facebook_monitor.core.scan_failure_policy import decide_scan_failure
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.scan_failures import STALE_RUNNING_REASON
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.user_messages import format_failure_message
from facebook_monitor.core.user_messages import format_failure_retry_exhausted_message
from facebook_monitor.core.user_messages import format_runtime_skip_message
from facebook_monitor.persistence.repositories.target_runtime_state import (
    TargetRuntimeStateRepository,
)
from facebook_monitor.persistence.repositories.targets import TargetRepository


@dataclass(frozen=True)
class StaleRunningRecovery:
    """描述一次 stale running recovery 決策與舊 attempt owner。"""

    state: TargetRuntimeState
    previous_worker_id: str
    previous_started_at: datetime
    previous_page_id: str
    previous_heartbeat_at: datetime | None
    decision: ScanFailureDecision
    stale_after_seconds: float


@dataclass(frozen=True)
class ScanSkipDecision:
    """描述一次保護性 skipped scan 是否需要升級成 recoverable failure。"""

    reason: str
    skip_streak: int
    skip_limit: int

    @property
    def escalate(self) -> bool:
        """回傳本次 skip 是否已達升級門檻。"""

        return self.skip_streak >= self.skip_limit


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
        self.ensure_runtime_state(target_id)
        state = self.runtime_states.mark_queued_if_not_running(
            target_id,
            reason=reason,
            enqueued_at=utc_now(),
        )
        if state is None:
            return self.ensure_runtime_state(target_id)
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

    def force_mark_target_running(
        self,
        target_id: str,
        worker_id: str,
        *,
        page_id: str = "",
    ) -> TargetRuntimeState:
        """無條件覆寫 running ownership；只供 maintenance / fallback 顯式使用。"""

        return self.mark_target_running(target_id, worker_id, page_id=page_id)

    def try_claim_target_running(
        self,
        target_id: str,
        worker_id: str,
        *,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """嘗試取得 running ownership；失敗時不得覆蓋既有 owner。"""

        self._require_target(target_id)
        self.ensure_runtime_state(target_id)
        now = utc_now()
        claimed_state = self.runtime_states.try_mark_running(
            target_id,
            worker_id=worker_id,
            page_id=page_id,
            started_at=now,
        )
        if claimed_state is not None:
            return claimed_state
        existing_state = self.ensure_runtime_state(target_id)
        if existing_state.runtime_status == TargetRuntimeStatus.RUNNING:
            self.runtime_states.record_scan_guard_skip(
                target_id,
                reason=(
                    "scan_guard_skipped: target_already_running "
                    f"active_worker_id={existing_state.active_worker_id}"
                ),
                skipped_at=now,
            )
            return None
        if existing_state.desired_state != TargetDesiredState.ACTIVE:
            self.runtime_states.record_scan_guard_skip(
                target_id,
                reason=(
                    "scan_guard_skipped: target_not_active "
                    f"desired_state={existing_state.desired_state.value}"
                ),
                skipped_at=now,
            )
            return None
        self.runtime_states.record_scan_guard_skip(
            target_id,
            reason="scan_guard_skipped: target_claim_conflict",
            skipped_at=now,
        )
        return None

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

    def force_mark_target_page_reloaded(
        self,
        target_id: str,
        *,
        page_id: str = "",
        reloaded_at: datetime | None = None,
    ) -> TargetRuntimeState:
        """無條件記錄 page reload；呼叫端必須已確認不需要 owner guard。"""

        return self.mark_target_page_reloaded(
            target_id,
            page_id=page_id,
            reloaded_at=reloaded_at,
        )

    def guarded_mark_target_page_reloaded(
        self,
        target_id: str,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
        reloaded_at: datetime | None = None,
    ) -> TargetRuntimeState | None:
        """以 running owner guard 記錄 page reload；stale owner 回傳 None。"""

        self._require_target(target_id)
        now = utc_now()
        return self.runtime_states.mark_page_reloaded_if_running_owner(
            target_id,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
            reloaded_at=reloaded_at or now,
            heartbeat_at=now,
        )

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
        state = self.runtime_states.record_heartbeat_if_running(
            target_id,
            worker_id=worker_id,
            page_id=page_id,
            heartbeat_at=utc_now(),
        )
        return state or self.ensure_runtime_state(target_id)

    def guarded_record_target_heartbeat(
        self,
        target_id: str,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """以 running owner guard 刷新 heartbeat；stale owner 回傳 None。"""

        self._require_target(target_id)
        return self.runtime_states.record_heartbeat_if_running_owner(
            target_id,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
            heartbeat_at=utc_now(),
        )

    def record_scan_guard_skip(self, target_id: str, reason: str) -> TargetRuntimeState:
        """記錄 target 被 queue/executor guard 擋下的原因。"""

        self._require_target(target_id)
        self.ensure_runtime_state(target_id)
        state = self.runtime_states.record_scan_guard_skip(
            target_id,
            reason=reason,
            skipped_at=utc_now(),
        )
        return state or self.ensure_runtime_state(target_id)

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
        state = self.runtime_states.set_display_next_due_at(
            target_id,
            due_at=due_at,
            updated_at=utc_now(),
        )
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
            consecutive_scan_skip_reason="",
            consecutive_scan_skip_count=0,
            updated_at=now,
        )
        self.runtime_states.save(state)
        return state

    def force_request_target_retry_after_runtime_restart(
        self,
        target_id: str,
    ) -> TargetRuntimeState:
        """runtime restart recovery：無條件清 owner 並要求新 runtime 補掃。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        state = self._retry_requested_state(existing_state)
        self.runtime_states.save(state)
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

        if not self._target_is_active(target_id):
            return None
        existing_state = self.ensure_runtime_state(target_id)
        state = self._retry_requested_state(existing_state)
        return self.runtime_states.save_if_running_owner(
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

        if not self._target_is_active(target_id):
            return None
        existing_state = self.ensure_runtime_state(target_id)
        state = self._retry_requested_state(existing_state)
        return self.runtime_states.save_if_not_running(state)

    def _retry_requested_state(
        self,
        existing_state: TargetRuntimeState,
    ) -> TargetRuntimeState:
        """建立 recovery 補掃 state，保留 failure streak 並清除本輪 owner。"""

        now = utc_now()
        return replace(
            existing_state,
            runtime_status=TargetRuntimeStatus.IDLE,
            scan_requested_at=now,
            enqueue_reason="",
            active_worker_id="",
            active_page_id="",
            updated_at=now,
        )

    def mark_target_idle(self, target_id: str) -> TargetRuntimeState:
        """標記單一 target 已完成本輪掃描並回到 idle。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        state = self._idle_state(existing_state)
        self.runtime_states.save(state)
        return state

    def force_mark_target_idle(self, target_id: str) -> TargetRuntimeState:
        """無條件將 target 標回 idle；呼叫端必須顯式接受覆寫 owner。"""

        return self.mark_target_idle(target_id)

    def mark_target_idle_if_not_running(self, target_id: str) -> TargetRuntimeState | None:
        """只在 row 不是 running owner 時將 target 標回 idle。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        state = self._idle_state(existing_state)
        return self.runtime_states.save_if_not_running(state)

    def guarded_mark_target_idle(
        self,
        target_id: str,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """以 running owner guard 將 target 標回 idle；stale owner 回傳 None。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        state = self._idle_state(existing_state)
        return self.runtime_states.save_if_running_owner(
            state,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def _idle_state(self, existing_state: TargetRuntimeState) -> TargetRuntimeState:
        """建立成功完成掃描後的 idle runtime state。"""

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
            consecutive_failure_reason="",
            consecutive_failure_count=0,
            consecutive_scan_skip_reason="",
            consecutive_scan_skip_count=0,
            updated_at=now,
        )
        return state

    def decide_scan_skip(
        self,
        target_id: str,
        reason: str,
        *,
        skip_limit: int,
    ) -> ScanSkipDecision:
        """依目前 skipped scan streak 決定本輪 skip 是否要升級成失敗。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        normalized_reason = str(reason or "").strip()
        previous_count = (
            existing_state.consecutive_scan_skip_count
            if existing_state.consecutive_scan_skip_reason == normalized_reason
            else 0
        )
        return ScanSkipDecision(
            reason=normalized_reason,
            skip_streak=max(previous_count, 0) + 1,
            skip_limit=max(int(skip_limit), 1),
        )

    def apply_scan_skip_decision(
        self,
        target_id: str,
        decision: ScanSkipDecision,
    ) -> TargetRuntimeState:
        """記錄保護性 skipped scan 並回 idle，保留既有 failure streak。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        state = self._scan_skipped_state(existing_state, decision, now=utc_now())
        self.runtime_states.save(state)
        return state

    def force_apply_scan_skip_decision(
        self,
        target_id: str,
        decision: ScanSkipDecision,
    ) -> TargetRuntimeState:
        """無條件套用 skipped scan decision；呼叫端必須顯式接受覆寫 owner。"""

        return self.apply_scan_skip_decision(target_id, decision)

    def guarded_apply_scan_skip_decision(
        self,
        target_id: str,
        decision: ScanSkipDecision,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """以 running owner guard 套用 skipped scan decision；stale owner 回傳 None。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        state = self._scan_skipped_state(existing_state, decision, now=utc_now())
        return self.runtime_states.save_if_running_owner(
            state,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def _scan_skipped_state(
        self,
        existing_state: TargetRuntimeState,
        decision: ScanSkipDecision,
        *,
        now: datetime,
    ) -> TargetRuntimeState:
        """建立 skipped scan 後的 idle state，避免誤清未恢復的 failure streak。"""

        return replace(
            existing_state,
            runtime_status=TargetRuntimeStatus.IDLE,
            scan_requested_at=_scan_request_after_current_attempt(existing_state),
            last_finished_at=now,
            last_heartbeat_at=now,
            last_error="",
            last_skip_reason=(
                f"{decision.reason}: skip "
                f"{decision.skip_streak}/{decision.skip_limit}"
            ),
            enqueue_reason="",
            active_worker_id="",
            active_page_id="",
            consecutive_scan_skip_reason=decision.reason,
            consecutive_scan_skip_count=decision.skip_streak,
            updated_at=now,
        )

    def mark_target_retriable_failure(
        self,
        target_id: str,
        decision: ScanFailureDecision,
    ) -> TargetRuntimeState:
        """記錄本輪可重試失敗，讓 target 回到 idle 供下一輪排程。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        now = utc_now()
        state = self._retriable_failure_state(existing_state, decision, now=now)
        self.runtime_states.save(state)
        return state

    def force_mark_target_retriable_failure(
        self,
        target_id: str,
        decision: ScanFailureDecision,
    ) -> TargetRuntimeState:
        """無條件記錄可重試失敗；呼叫端必須顯式接受覆寫 owner。"""

        return self.mark_target_retriable_failure(target_id, decision)

    def guarded_mark_target_retriable_failure(
        self,
        target_id: str,
        decision: ScanFailureDecision,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """以 running owner guard 記錄可重試失敗；stale owner 回傳 None。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        state = self._retriable_failure_state(existing_state, decision, now=utc_now())
        return self.runtime_states.save_if_running_owner(
            state,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def _retriable_failure_state(
        self,
        existing_state: TargetRuntimeState,
        decision: ScanFailureDecision,
        *,
        now: datetime,
    ) -> TargetRuntimeState:
        """建立可重試失敗後回到 idle 的 runtime state。"""

        state = replace(
            existing_state,
            runtime_status=TargetRuntimeStatus.IDLE,
            scan_requested_at=(
                now
                if decision.auto_restart
                else _scan_request_after_current_attempt(existing_state)
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
        state = self._error_state(
            existing_state,
            error,
            failure_reason=failure_reason,
            failure_count=failure_count,
        )
        self.runtime_states.save(state)
        return state

    def force_mark_target_error(
        self,
        target_id: str,
        error: str,
        *,
        failure_reason: str = "",
        failure_count: int = 0,
    ) -> TargetRuntimeState:
        """無條件將 target 標記為 error；呼叫端必須顯式接受覆寫 owner。"""

        return self.mark_target_error(
            target_id,
            error,
            failure_reason=failure_reason,
            failure_count=failure_count,
        )

    def guarded_mark_target_error(
        self,
        target_id: str,
        error: str,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
        failure_reason: str = "",
        failure_count: int = 0,
    ) -> TargetRuntimeState | None:
        """以 running owner guard 將 target 標記為 error；stale owner 回傳 None。"""

        self._require_target(target_id)
        existing_state = self.ensure_runtime_state(target_id)
        state = self._error_state(
            existing_state,
            error,
            failure_reason=failure_reason,
            failure_count=failure_count,
        )
        return self.runtime_states.save_if_running_owner(
            state,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def _error_state(
        self,
        existing_state: TargetRuntimeState,
        error: str,
        *,
        failure_reason: str,
        failure_count: int,
    ) -> TargetRuntimeState:
        """建立掃描失敗後的 error runtime state。"""

        now = utc_now()
        state = replace(
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

        existing_state = self.ensure_runtime_state(target_id)
        state = self._failure_decision_state(
            existing_state,
            decision,
            error,
            now=utc_now(),
        )
        self.runtime_states.save(state)
        return state

    def force_apply_scan_failure_decision(
        self,
        target_id: str,
        decision: ScanFailureDecision,
        error: str,
    ) -> TargetRuntimeState:
        """無條件套用 failure decision；呼叫端必須顯式接受覆寫 owner。"""

        return self.apply_scan_failure_decision(target_id, decision, error)

    def guarded_apply_scan_failure_decision(
        self,
        target_id: str,
        decision: ScanFailureDecision,
        error: str,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """以 running owner guard 套用 failure decision；stale owner 回傳 None。"""

        existing_state = self.ensure_runtime_state(target_id)
        state = self._failure_decision_state(
            existing_state,
            decision,
            error,
            now=utc_now(),
        )
        return self.runtime_states.save_if_running_owner(
            state,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def _failure_decision_state(
        self,
        existing_state: TargetRuntimeState,
        decision: ScanFailureDecision,
        error: str,
        *,
        now: datetime,
    ) -> TargetRuntimeState:
        """依 failure decision 建立 runtime state，供一般與 stale recovery 共用。"""

        if decision.target_action == "idle":
            if decision.counts_toward_streak:
                return self._retriable_failure_state(existing_state, decision, now=now)
            return self._idle_state(existing_state)
        resolved_error = error
        if decision.counts_toward_streak:
            resolved_error = format_failure_retry_exhausted_message(
                decision.reason,
                retry_streak=decision.retry_streak,
                retry_limit=decision.retry_limit,
            )
        return self._error_state(
            existing_state,
            resolved_error,
            failure_reason=decision.reason if decision.counts_toward_streak else "",
            failure_count=decision.retry_streak if decision.counts_toward_streak else 0,
        )

    def recover_stale_running_targets(
        self,
        *,
        stale_after_seconds: float,
        now: datetime | None = None,
    ) -> tuple[StaleRunningRecovery, ...]:
        """修復 heartbeat 過舊的 running target，避免永久卡住。"""

        current_time = now or utc_now()
        stale_after = max(stale_after_seconds, 1)
        recovered: list[StaleRunningRecovery] = []
        for state in self.runtime_states.list_all():
            if state.runtime_status != TargetRuntimeStatus.RUNNING:
                continue
            heartbeat_at = state.last_heartbeat_at or state.updated_at
            if current_time - heartbeat_at <= timedelta(seconds=stale_after):
                continue
            stale_before = current_time - timedelta(seconds=stale_after)
            if state.last_started_at is None:
                continue
            worker_id = state.active_worker_id
            started_at = state.last_started_at
            page_id = state.active_page_id
            decision = self.decide_scan_failure(
                state.target_id,
                STALE_RUNNING_REASON,
                source="runtime_recovery",
            )
            runtime_message = format_failure_message(
                STALE_RUNNING_REASON,
                f"worker heartbeat expired after {int(stale_after)} seconds",
            )
            recovered_state = self._failure_decision_state(
                state,
                decision,
                runtime_message,
                now=current_time,
            )
            committed_state = self.runtime_states.save_stale_running_state_if_unchanged(
                recovered_state,
                worker_id=worker_id,
                started_at=started_at,
                page_id=page_id,
                stale_before=stale_before,
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
        self.ensure_runtime_state(target_id)
        state = self.runtime_states.set_scan_requested_at(
            target_id,
            requested_at=utc_now(),
            updated_at=utc_now(),
        )
        if state is None:
            return self.ensure_runtime_state(target_id)
        return state

    def clear_target_scan_request(self, target_id: str) -> TargetRuntimeState:
        """清除已被 scheduler 消化的立即掃描要求。"""

        self._require_target(target_id)
        self.ensure_runtime_state(target_id)
        state = self.runtime_states.set_scan_requested_at(
            target_id,
            requested_at=None,
            updated_at=utc_now(),
        )
        if state is None:
            return self.ensure_runtime_state(target_id)
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
        self.ensure_runtime_state(target_id)
        state = self.runtime_states.clear_scan_request_if_not_newer(
            target_id,
            consumed_at=consumed_at,
            updated_at=utc_now(),
        )
        if state is None:
            return self.ensure_runtime_state(target_id)
        return state

    def _require_target(self, target_id: str) -> None:
        """確認 target 存在。"""

        if self.targets.get(target_id) is None:
            raise ValueError(f"Target not found: {target_id}")

    def _target_is_active(self, target_id: str) -> bool:
        """確認 target 存在且仍是可掃描狀態。"""

        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        return target.enabled and not target.paused


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
