"""Target runtime scan outcome service。

職責：處理 scan success、skip 與 failure decision 寫回。
"""

from __future__ import annotations

from datetime import datetime

from facebook_monitor.application.target_runtime_access import TargetRuntimeAccess
from facebook_monitor.application.target_runtime_decisions import ScanSkipDecision
from facebook_monitor.application.target_runtime_transitions import error_state
from facebook_monitor.application.target_runtime_transitions import failure_decision_state
from facebook_monitor.application.target_runtime_transitions import idle_state
from facebook_monitor.application.target_runtime_transitions import retriable_failure_state
from facebook_monitor.application.target_runtime_transitions import scan_skipped_state
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failure_policy import ScanFailureDecision
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
from facebook_monitor.core.scan_failure_policy import decide_scan_failure


class TargetRuntimeOutcomeService:
    """協調 target scan outcome runtime writes。"""

    def __init__(self, access: TargetRuntimeAccess) -> None:
        self._access = access

    def mark_target_idle(self, target_id: str) -> TargetRuntimeState:
        """標記單一 target 已完成本輪掃描並回到 idle。"""

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
        state = idle_state(existing_state, now=utc_now())
        self._access.runtime_states.save(state)
        return state

    def force_mark_target_idle(self, target_id: str) -> TargetRuntimeState:
        """無條件將 target 標回 idle；呼叫端必須顯式接受覆寫 owner。"""

        return self.mark_target_idle(target_id)

    def mark_target_idle_if_not_running(self, target_id: str) -> TargetRuntimeState | None:
        """只在 row 不是 running owner 時將 target 標回 idle。"""

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
        state = idle_state(existing_state, now=utc_now())
        return self._access.runtime_states.save_if_not_running(state)

    def guarded_mark_target_idle(
        self,
        target_id: str,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """以 running owner guard 將 target 標回 idle；stale owner 回傳 None。"""

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
        state = idle_state(existing_state, now=utc_now())
        return self._access.runtime_states.save_if_running_owner(
            state,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def decide_scan_skip(
        self,
        target_id: str,
        reason: str,
        *,
        skip_limit: int,
    ) -> ScanSkipDecision:
        """依目前 skipped scan streak 決定本輪 skip 是否要升級成失敗。"""

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
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

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
        state = scan_skipped_state(existing_state, decision, now=utc_now())
        self._access.runtime_states.save(state)
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

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
        state = scan_skipped_state(existing_state, decision, now=utc_now())
        return self._access.runtime_states.save_if_running_owner(
            state,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def mark_target_retriable_failure(
        self,
        target_id: str,
        decision: ScanFailureDecision,
    ) -> TargetRuntimeState:
        """記錄本輪可重試失敗，讓 target 回到 idle 供下一輪排程。"""

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
        state = retriable_failure_state(existing_state, decision, now=utc_now())
        self._access.runtime_states.save(state)
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

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
        state = retriable_failure_state(existing_state, decision, now=utc_now())
        return self._access.runtime_states.save_if_running_owner(
            state,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def mark_target_error(
        self,
        target_id: str,
        error: str,
        *,
        failure_reason: str = "",
        failure_count: int = 0,
    ) -> TargetRuntimeState:
        """標記單一 target 本輪掃描發生錯誤。"""

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
        state = error_state(
            existing_state,
            error,
            failure_reason=failure_reason,
            failure_count=failure_count,
            now=utc_now(),
        )
        self._access.runtime_states.save(state)
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

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
        state = error_state(
            existing_state,
            error,
            failure_reason=failure_reason,
            failure_count=failure_count,
            now=utc_now(),
        )
        return self._access.runtime_states.save_if_running_owner(
            state,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def decide_scan_failure(
        self,
        target_id: str,
        reason: str,
        *,
        source: ScanFailureSource,
    ) -> ScanFailureDecision:
        """依目前 runtime streak 決定本輪 scan failure 的處置。"""

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
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

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
        state = failure_decision_state(
            existing_state,
            decision,
            error,
            now=utc_now(),
        )
        self._access.runtime_states.save(state)
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

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
        state = failure_decision_state(
            existing_state,
            decision,
            error,
            now=utc_now(),
        )
        return self._access.runtime_states.save_if_running_owner(
            state,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )


__all__ = ["TargetRuntimeOutcomeService"]
