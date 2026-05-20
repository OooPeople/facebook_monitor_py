"""Target monitoring command service。

職責：處理 Web UI/console 的開始、停止、狀態更新與啟動整理語義。
"""

from __future__ import annotations

from dataclasses import replace

from facebook_monitor.application.target_config_service import TargetConfigService
from facebook_monitor.application.target_registry_service import TargetRegistryService
from facebook_monitor.application.target_requests import UpdateTargetStatusRequest
from facebook_monitor.application.target_runtime_service import TargetRuntimeService
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.repositories.notification_outbox import (
    NotificationOutboxRepository,
)
from facebook_monitor.persistence.repositories.scan_scope_state import ScanScopeStateRepository
from facebook_monitor.persistence.repositories.seen_items import SeenItemRepository
from facebook_monitor.persistence.repositories.target_runtime_state import (
    TargetRuntimeStateRepository,
)
from facebook_monitor.persistence.repositories.targets import TargetRepository


class TargetMonitoringCommands:
    """協調 target monitoring command state transition。"""

    def __init__(
        self,
        *,
        targets: TargetRepository,
        runtime_states: TargetRuntimeStateRepository,
        seen_items: SeenItemRepository,
        scan_scope_state: ScanScopeStateRepository,
        notification_outbox: NotificationOutboxRepository,
        registry: TargetRegistryService,
        configs: TargetConfigService,
        runtime: TargetRuntimeService,
    ) -> None:
        self.targets = targets
        self.runtime_states = runtime_states
        self.seen_items = seen_items
        self.scan_scope_state = scan_scope_state
        self.notification_outbox = notification_outbox
        self.registry = registry
        self.configs = configs
        self.runtime = runtime

    def update_target_status(self, request: UpdateTargetStatusRequest) -> TargetDescriptor:
        """更新 target 啟停狀態，供 UI/console 與未來 scheduler 共用。"""

        target = self.targets.get(request.target_id)
        if target is None:
            raise ValueError(f"Target not found: {request.target_id}")

        updated_target = replace(
            target,
            enabled=request.enabled,
            paused=request.paused,
            updated_at=utc_now(),
        )
        self.targets.save(updated_target)
        self.runtime.reset_target_desired_state(
            target.id,
            (
                TargetDesiredState.ACTIVE
                if request.enabled and not request.paused
                else TargetDesiredState.STOPPED
            ),
        )
        return updated_target

    def restart_target_monitoring(self, target_id: str) -> TargetDescriptor:
        """執行 target「開始」語義：清 runtime 去重狀態並要求立即掃描。"""

        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        if target.target_kind not in {TargetKind.POSTS, TargetKind.COMMENTS}:
            raise ValueError(f"Unsupported target kind: {target.target_kind.value}")
        target = self.registry.normalize_target_names(target)

        self.seen_items.clear_scope(target.scope_id)
        self.scan_scope_state.mark_initialized(target.scope_id)
        self.notification_outbox.clear_by_target(target.id)
        updated_target = replace(
            target,
            enabled=True,
            paused=False,
            updated_at=utc_now(),
        )
        self.targets.save(updated_target)
        self.runtime.restart_target_runtime(target_id)
        return updated_target

    def pause_target_monitoring(self, target_id: str) -> TargetDescriptor:
        """執行 target「停止」語義：停止排程但保留 seen/history。"""

        return self.update_target_status(
            UpdateTargetStatusRequest(
                target_id=target_id,
                enabled=True,
                paused=True,
            )
        )

    def pause_all_targets_for_webui_startup(
        self,
        *,
        default_fixed_refresh_sec: int | float = 0,
    ) -> None:
        """Web UI 啟動時停止所有 target，不改變 refresh mode。"""

        for target in self.targets.list_all():
            target = self.registry.normalize_target_names(target)
            self.pause_target_monitoring(target.id)
