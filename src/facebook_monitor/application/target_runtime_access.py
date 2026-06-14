"""Target runtime repository access helpers。

職責：集中 runtime 子服務共用的 target 存在檢查與 state 初始化，不承載
queue、owner、recovery 或 scan result 決策。
"""

from __future__ import annotations

from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.persistence.repositories.target_runtime_state import (
    TargetRuntimeStateRepository,
)
from facebook_monitor.persistence.repositories.targets import TargetRepository


class TargetRuntimeAccess:
    """提供 target runtime 子服務共用的 repository access。"""

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

    def require_target(self, target_id: str) -> None:
        """確認 target 存在。"""

        if self.targets.get(target_id) is None:
            raise ValueError(f"Target not found: {target_id}")

    def target_is_active(self, target_id: str) -> bool:
        """確認 target 存在且仍是可掃描狀態。"""

        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        return target.enabled and not target.paused


__all__ = ["TargetRuntimeAccess"]
