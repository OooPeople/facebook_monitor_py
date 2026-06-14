"""Target runtime scan request and display patch service。

職責：處理 scan-once request 與 UI 顯示用 runtime 欄位更新。
"""

from __future__ import annotations

from datetime import datetime

from facebook_monitor.application.target_runtime_access import TargetRuntimeAccess
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import utc_now


class TargetRuntimeRequestService:
    """更新 target runtime 的 request/display patch 欄位。"""

    def __init__(self, access: TargetRuntimeAccess) -> None:
        self._access = access

    def set_target_display_next_due_at(
        self,
        target_id: str,
        due_at: datetime | None,
    ) -> TargetRuntimeState | None:
        """更新 UI 顯示用 next due；不作為 scheduler 排程來源。"""

        if self._access.targets.get(target_id) is None:
            return None
        existing_state = self._access.ensure_runtime_state(target_id)
        if existing_state.display_next_due_at == due_at:
            return existing_state
        return self._access.runtime_states.set_display_next_due_at(
            target_id,
            due_at=due_at,
            updated_at=utc_now(),
        )

    def request_target_scan(self, target_id: str) -> TargetRuntimeState:
        """要求 scheduler 下一輪立即掃描 target，不修改 seen 狀態。"""

        self._access.require_target(target_id)
        self._access.ensure_runtime_state(target_id)
        state = self._access.runtime_states.set_scan_requested_at(
            target_id,
            requested_at=utc_now(),
            updated_at=utc_now(),
        )
        if state is None:
            return self._access.ensure_runtime_state(target_id)
        return state

    def clear_target_scan_request(self, target_id: str) -> TargetRuntimeState:
        """清除已被 scheduler 消化的立即掃描要求。"""

        self._access.require_target(target_id)
        self._access.ensure_runtime_state(target_id)
        state = self._access.runtime_states.set_scan_requested_at(
            target_id,
            requested_at=None,
            updated_at=utc_now(),
        )
        if state is None:
            return self._access.ensure_runtime_state(target_id)
        return state

    def clear_target_scan_request_if_not_newer(
        self,
        target_id: str,
        consumed_at: datetime | None,
    ) -> TargetRuntimeState:
        """清除已入隊的 scan request，但保留入隊後新送出的 request。"""

        if consumed_at is None:
            return self.clear_target_scan_request(target_id)
        self._access.require_target(target_id)
        self._access.ensure_runtime_state(target_id)
        state = self._access.runtime_states.clear_scan_request_if_not_newer(
            target_id,
            consumed_at=consumed_at,
            updated_at=utc_now(),
        )
        if state is None:
            return self._access.ensure_runtime_state(target_id)
        return state


__all__ = ["TargetRuntimeRequestService"]
