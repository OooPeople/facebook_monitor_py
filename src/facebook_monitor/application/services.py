"""Application service facade。

職責：保留既有 `app.services.targets.*` public API，同時把實際職責委派給
target registry/config/runtime/monitoring command services。
"""

from __future__ import annotations

from datetime import datetime

from facebook_monitor.application.scan_recording_service import RecordScanRequest
from facebook_monitor.application.scan_recording_service import ScanApplicationService
from facebook_monitor.application.target_config_service import TargetConfigService
from facebook_monitor.application.target_monitoring_commands import TargetMonitoringCommands
from facebook_monitor.application.target_registry_service import TargetRegistryService
from facebook_monitor.application.target_registry_service import clean_facebook_group_name
from facebook_monitor.application.target_requests import DEFAULT_WEBUI_FIXED_REFRESH_SECONDS
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UNSET_CONFIG_VALUE
from facebook_monitor.application.target_requests import UnsetConfigValue
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.application.target_requests import UpdateTargetConfigRequest
from facebook_monitor.application.target_requests import UpdateTargetStatusRequest
from facebook_monitor.application.target_runtime_service import TargetRuntimeService
from facebook_monitor.core.models import GlobalNotificationSettings
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.persistence.repositories.seen_items import SeenItemRepository
from facebook_monitor.persistence.repositories.target_configs import TargetConfigRepository
from facebook_monitor.persistence.repositories.target_runtime_state import (
    TargetRuntimeStateRepository,
)
from facebook_monitor.persistence.repositories.targets import TargetRepository


class TargetApplicationService:
    """相容 facade：委派 target registry/config/runtime/monitoring services。"""

    def __init__(
        self,
        targets: TargetRepository,
        configs: TargetConfigRepository,
        runtime_states: TargetRuntimeStateRepository,
        seen_items: SeenItemRepository,
    ) -> None:
        self.targets = targets
        self.configs = configs
        self.runtime_states = runtime_states
        self.seen_items = seen_items
        self.config_service = TargetConfigService(targets=targets, configs=configs)
        self.runtime_service = TargetRuntimeService(
            targets=targets,
            runtime_states=runtime_states,
        )
        self.registry_service = TargetRegistryService(
            targets=targets,
            configs=self.config_service,
            runtime=self.runtime_service,
        )
        self.monitoring_commands = TargetMonitoringCommands(
            targets=targets,
            runtime_states=runtime_states,
            seen_items=seen_items,
            registry=self.registry_service,
            configs=self.config_service,
            runtime=self.runtime_service,
        )

    def normalize_target_names(self, target: TargetDescriptor) -> TargetDescriptor:
        """清理已保存 target 名稱並寫回，避免通知數前綴散到各輸出面。"""

        return self.registry_service.normalize_target_names(target)

    def delete_target(self, target_id: str) -> None:
        """刪除單一 target；target-scoped config 由 SQLite FK 一併清除。"""

        return self.registry_service.delete_target(target_id)

    def refresh_target_group_name(self, target_id: str, group_name: str) -> TargetDescriptor:
        """以 scheduler metadata refresh 結果補齊 target 顯示名稱。"""

        return self.registry_service.refresh_target_group_name(target_id, group_name)

    def update_target_name(self, target_id: str, name: str) -> TargetDescriptor:
        """更新使用者自訂 target 顯示名稱。"""

        return self.registry_service.update_target_name(target_id, name)

    def upsert_group_posts_target(
        self,
        request: UpsertGroupPostsTargetRequest,
    ) -> TargetDescriptor:
        """建立或更新 group posts target。"""

        return self.registry_service.upsert_group_posts_target(request)

    def upsert_comments_target(
        self,
        request: UpsertCommentsTargetRequest,
    ) -> TargetDescriptor:
        """建立或更新 comments target。"""

        return self.registry_service.upsert_comments_target(request)

    def get_config_for_target(self, target: TargetDescriptor) -> TargetConfig:
        """讀取單一 target 的 config。"""

        return self.config_service.get_config_for_target(target)

    def save_config_for_target(
        self,
        target: TargetDescriptor,
        config: TargetConfig,
    ) -> TargetConfig:
        """保存單一 target 的 config。"""

        return self.config_service.save_config_for_target(target, config)

    def update_target_config(self, request: UpdateTargetConfigRequest) -> TargetConfig:
        """更新單一 target 監視設定。"""

        return self.config_service.update_target_config(request)

    def apply_global_notification_settings(
        self,
        settings: GlobalNotificationSettings,
    ) -> int:
        """將通知預設值套用到所有既有 target config。"""

        return self.config_service.apply_global_notification_settings(settings)

    def ensure_runtime_state(self, target_id: str) -> TargetRuntimeState:
        """確保 target 已有 runtime state，供 scheduler/UI 查詢。"""

        return self.runtime_service.ensure_runtime_state(target_id)

    def mark_target_queued(self, target_id: str, reason: str) -> TargetRuntimeState:
        """標記單一 target 已進入 executor queue，等待 worker slot。"""

        return self.runtime_service.mark_target_queued(target_id, reason)

    def mark_target_running(
        self,
        target_id: str,
        worker_id: str,
        *,
        page_id: str = "",
    ) -> TargetRuntimeState:
        """標記單一 target 正由 scheduler/worker 掃描中。"""

        return self.runtime_service.mark_target_running(target_id, worker_id, page_id=page_id)

    def try_mark_target_running(
        self,
        target_id: str,
        worker_id: str,
        *,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """嘗試取得單一 target scan lock；已 running 時記錄 skip reason。"""

        return self.runtime_service.try_mark_target_running(
            target_id,
            worker_id,
            page_id=page_id,
        )

    def mark_target_page_reloaded(
        self,
        target_id: str,
        *,
        page_id: str = "",
        reloaded_at: datetime | None = None,
    ) -> TargetRuntimeState:
        """記錄 resident page 已完成 reload/goto，供 UI 診斷 page ownership。"""

        return self.runtime_service.mark_target_page_reloaded(
            target_id,
            page_id=page_id,
            reloaded_at=reloaded_at,
        )

    def record_scan_guard_skip(self, target_id: str, reason: str) -> TargetRuntimeState:
        """記錄 target 被 queue/executor guard 擋下的原因。"""

        return self.runtime_service.record_scan_guard_skip(target_id, reason)

    def mark_target_idle(self, target_id: str) -> TargetRuntimeState:
        """標記單一 target 已完成本輪掃描並回到 idle。"""

        return self.runtime_service.mark_target_idle(target_id)

    def mark_target_error(self, target_id: str, error: str) -> TargetRuntimeState:
        """標記單一 target 本輪掃描發生錯誤。"""

        return self.runtime_service.mark_target_error(target_id, error)

    def recover_stale_running_targets(
        self,
        *,
        stale_after_seconds: float,
        now: datetime | None = None,
    ) -> tuple[TargetRuntimeState, ...]:
        """將 heartbeat 過舊的 running target 標成 error。"""

        return self.runtime_service.recover_stale_running_targets(
            stale_after_seconds=stale_after_seconds,
            now=now,
        )

    def recover_stale_queued_targets(
        self,
        *,
        stale_after_seconds: float,
        now: datetime | None = None,
    ) -> tuple[TargetRuntimeState, ...]:
        """將排隊過久的 target 回復 idle。"""

        return self.runtime_service.recover_stale_queued_targets(
            stale_after_seconds=stale_after_seconds,
            now=now,
        )

    def request_target_scan(self, target_id: str) -> TargetRuntimeState:
        """要求 scheduler 下一輪立即掃描 target，不修改 seen 狀態。"""

        return self.runtime_service.request_target_scan(target_id)

    def clear_target_scan_request(self, target_id: str) -> TargetRuntimeState:
        """清除已被 scheduler 消化的立即掃描要求。"""

        return self.runtime_service.clear_target_scan_request(target_id)

    def update_target_status(self, request: UpdateTargetStatusRequest) -> TargetDescriptor:
        """更新 target 啟停狀態。"""

        return self.monitoring_commands.update_target_status(request)

    def restart_target_monitoring(self, target_id: str) -> TargetDescriptor:
        """對齊 userscript「開始」：清 seen scope、啟用並要求立即掃描。"""

        return self.monitoring_commands.restart_target_monitoring(target_id)

    def pause_target_monitoring(self, target_id: str) -> TargetDescriptor:
        """對齊 userscript「停止」：停止排程但保留 seen/history。"""

        return self.monitoring_commands.pause_target_monitoring(target_id)

    def pause_all_targets_for_webui_startup(
        self,
        *,
        default_fixed_refresh_sec: int = DEFAULT_WEBUI_FIXED_REFRESH_SECONDS,
    ) -> None:
        """Web UI 啟動時停止所有 target，並補齊固定掃描間隔設定。"""

        return self.monitoring_commands.pause_all_targets_for_webui_startup(
            default_fixed_refresh_sec=default_fixed_refresh_sec,
        )


__all__ = [
    "DEFAULT_WEBUI_FIXED_REFRESH_SECONDS",
    "RecordScanRequest",
    "ScanApplicationService",
    "TargetApplicationService",
    "TargetConfigPatch",
    "UNSET_CONFIG_VALUE",
    "UnsetConfigValue",
    "UpdateTargetConfigRequest",
    "UpdateTargetStatusRequest",
    "UpsertCommentsTargetRequest",
    "UpsertGroupPostsTargetRequest",
    "clean_facebook_group_name",
]
