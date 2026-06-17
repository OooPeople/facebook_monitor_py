"""Target runtime ownership service。

職責：處理 queue/running ownership、page reload、heartbeat 與 scan guard skip 寫回。
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime

from facebook_monitor.application.target_runtime_access import TargetRuntimeAccess
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now


@dataclass(frozen=True)
class QueueAdmissionResult:
    """描述 queue admission 是否由本輪 conditional update 實際提交。"""

    committed: bool
    state: TargetRuntimeState


class TargetRuntimeOwnershipService:
    """協調 target running ownership 與 owner guarded runtime writes。"""

    def __init__(self, access: TargetRuntimeAccess) -> None:
        self._access = access

    def mark_target_queued(self, target_id: str, reason: str) -> TargetRuntimeState:
        """Legacy convenience API；queue admission 失敗時直接報錯。"""

        result = self.try_mark_target_queued(target_id, reason)
        if not result.committed:
            raise RuntimeError(f"target was not queued: {target_id}")
        return result.state

    def try_mark_target_queued(self, target_id: str, reason: str) -> QueueAdmissionResult:
        """嘗試將 target 放入 queue，並回報本輪 DB admission 是否成功。"""

        self._access.require_target(target_id)
        self._access.ensure_runtime_state(target_id)
        state = self._access.runtime_states.mark_queued_if_not_running(
            target_id,
            reason=reason,
            enqueued_at=utc_now(),
        )
        if state is None:
            return QueueAdmissionResult(
                committed=False,
                state=self._access.ensure_runtime_state(target_id),
            )
        return QueueAdmissionResult(committed=True, state=state)

    def mark_target_running(
        self,
        target_id: str,
        worker_id: str,
        *,
        page_id: str = "",
    ) -> TargetRuntimeState:
        """標記單一 target 正由 scheduler/worker 掃描中。"""

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
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
        self._access.runtime_states.save(state)
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

        self._access.require_target(target_id)
        self._access.ensure_runtime_state(target_id)
        now = utc_now()
        claimed_state = self._access.runtime_states.try_mark_running(
            target_id,
            worker_id=worker_id,
            page_id=page_id,
            started_at=now,
        )
        if claimed_state is not None:
            return claimed_state
        existing_state = self._access.ensure_runtime_state(target_id)
        if existing_state.runtime_status == TargetRuntimeStatus.RUNNING:
            self._access.runtime_states.record_scan_guard_skip(
                target_id,
                reason=(
                    "scan_guard_skipped: target_already_running "
                    f"active_worker_id={existing_state.active_worker_id}"
                ),
                skipped_at=now,
            )
            return None
        if existing_state.desired_state != TargetDesiredState.ACTIVE:
            self._access.runtime_states.record_scan_guard_skip(
                target_id,
                reason=(
                    "scan_guard_skipped: target_not_active "
                    f"desired_state={existing_state.desired_state.value}"
                ),
                skipped_at=now,
            )
            return None
        self._access.runtime_states.record_scan_guard_skip(
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

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
        now = utc_now()
        state = replace(
            existing_state,
            active_page_id=page_id or existing_state.active_page_id,
            last_page_reloaded_at=reloaded_at or now,
            last_heartbeat_at=now,
            updated_at=now,
        )
        self._access.runtime_states.save(state)
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

        self._access.require_target(target_id)
        now = utc_now()
        return self._access.runtime_states.mark_page_reloaded_if_running_owner(
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

        self._access.require_target(target_id)
        existing_state = self._access.ensure_runtime_state(target_id)
        if existing_state.runtime_status != TargetRuntimeStatus.RUNNING:
            return existing_state
        state = self._access.runtime_states.record_heartbeat_if_running(
            target_id,
            worker_id=worker_id,
            page_id=page_id,
            heartbeat_at=utc_now(),
        )
        return state or self._access.ensure_runtime_state(target_id)

    def guarded_record_target_heartbeat(
        self,
        target_id: str,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """以 running owner guard 刷新 heartbeat；stale owner 回傳 None。"""

        self._access.require_target(target_id)
        return self._access.runtime_states.record_heartbeat_if_running_owner(
            target_id,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
            heartbeat_at=utc_now(),
        )

    def record_scan_guard_skip(self, target_id: str, reason: str) -> TargetRuntimeState:
        """記錄 target 被 queue/executor guard 擋下的原因。"""

        self._access.require_target(target_id)
        self._access.ensure_runtime_state(target_id)
        state = self._access.runtime_states.record_scan_guard_skip(
            target_id,
            reason=reason,
            skipped_at=utc_now(),
        )
        return state or self._access.ensure_runtime_state(target_id)


__all__ = ["TargetRuntimeOwnershipService"]
