"""Application service composition façade。

職責：提供 `TargetApplicationService` 作為 target application façade，並把
實際職責委派給 target registry/config/runtime/monitoring command services。
"""

from __future__ import annotations

from datetime import datetime

from facebook_monitor.application import target_requests as _target_requests
from facebook_monitor.application.target_config_service import TargetConfigService
from facebook_monitor.application.target_cover_image_refresh_service import (
    CoverImageRefreshRequestResult,
    TargetCoverImageRefreshService,
)
from facebook_monitor.application.target_monitoring_commands import (
    ResetTargetNotificationStateResult,
)
from facebook_monitor.application.target_monitoring_commands import TargetMonitoringCommands
from facebook_monitor.application.target_registry_service import TargetRegistryService
from facebook_monitor.application.target_runtime_service import QueueAdmissionResult
from facebook_monitor.application.target_runtime_service import ScanSkipDecision
from facebook_monitor.application.target_runtime_service import StaleRunningRecovery
from facebook_monitor.application.target_runtime_service import TargetRuntimeService
from facebook_monitor.core.models import TargetCoverImageRefreshState
from facebook_monitor.core.models import TargetCoverImageRefreshResult
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.scan_failure_policy import ScanFailureDecision
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
from facebook_monitor.persistence.repositories.notification_outbox import (
    NotificationOutboxRepository,
)
from facebook_monitor.persistence.repositories.seen_items import SeenItemRepository
from facebook_monitor.persistence.repositories.scan_scope_state import ScanScopeStateRepository
from facebook_monitor.persistence.repositories.target_configs import TargetConfigRepository
from facebook_monitor.persistence.repositories.target_cover_image_refresh import (
    TargetCoverImageRefreshRepository,
)
from facebook_monitor.persistence.repositories.dedupe_state import DedupeStateRepository
from facebook_monitor.persistence.repositories.logical_items import LogicalItemRepository
from facebook_monitor.persistence.repositories.notification_dedupe import (
    NotificationDedupeRepository,
)
from facebook_monitor.persistence.repositories.target_runtime_state import (
    TargetRuntimeStateRepository,
)
from facebook_monitor.persistence.repositories.targets import TargetRepository


class TargetApplicationService:
    """正式 composition façade：委派 target registry/config/runtime/monitoring services。"""

    def __init__(
        self,
        targets: TargetRepository,
        configs: TargetConfigRepository,
        cover_image_refreshes: TargetCoverImageRefreshRepository,
        runtime_states: TargetRuntimeStateRepository,
        dedupe_state: DedupeStateRepository,
        seen_items: SeenItemRepository,
        logical_items: LogicalItemRepository,
        scan_scope_state: ScanScopeStateRepository,
        notification_dedupe: NotificationDedupeRepository,
        notification_outbox: NotificationOutboxRepository,
    ) -> None:
        self.targets = targets
        self.configs = configs
        self.cover_image_refreshes = cover_image_refreshes
        self.runtime_states = runtime_states
        self.dedupe_state = dedupe_state
        self.seen_items = seen_items
        self.logical_items = logical_items
        self.scan_scope_state = scan_scope_state
        self.notification_dedupe = notification_dedupe
        self.notification_outbox = notification_outbox
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
        self.cover_image_refresh_service = TargetCoverImageRefreshService(
            targets=targets,
            cover_image_refreshes=cover_image_refreshes,
            registry=self.registry_service,
        )
        self.monitoring_commands = TargetMonitoringCommands(
            targets=targets,
            runtime_states=runtime_states,
            dedupe_state=dedupe_state,
            seen_items=seen_items,
            logical_items=logical_items,
            scan_scope_state=scan_scope_state,
            notification_dedupe=notification_dedupe,
            notification_outbox=notification_outbox,
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

    def refresh_target_group_metadata(
        self,
        target_id: str,
        *,
        group_name: str,
        group_cover_image_url: str = "",
        overwrite_name: bool = False,
    ) -> TargetDescriptor:
        """以 scheduler metadata refresh 結果補齊 target 顯示名稱與封面圖。"""

        return self.registry_service.refresh_target_group_metadata(
            target_id,
            group_name=group_name,
            group_cover_image_url=group_cover_image_url,
            overwrite_name=overwrite_name,
        )

    def refresh_target_group_cover_image(
        self,
        target_id: str,
        group_cover_image_url: str,
    ) -> TargetDescriptor:
        """只更新 target 社團封面圖 URL，不覆蓋名稱或 metadata status。"""

        return self.cover_image_refresh_service.refresh_target_cover_image_url(
            target_id,
            group_cover_image_url,
        )

    def request_target_cover_image_refresh(
        self,
        target_id: str,
        *,
        reported_url: str,
        min_interval_seconds: int,
    ) -> CoverImageRefreshRequestResult:
        """依 UI 壞圖 hint 排程 image-only cover refresh。"""

        return self.cover_image_refresh_service.request_refresh_for_current_url(
            target_id,
            reported_url=reported_url,
            min_interval_seconds=min_interval_seconds,
        )

    def list_pending_cover_image_refreshes(
        self,
        *,
        limit: int,
        exclude_target_ids: tuple[str, ...] = (),
    ) -> list[TargetCoverImageRefreshState]:
        """列出等待 resident worker 消化的 image-only cover refresh jobs。"""

        return self.cover_image_refresh_service.list_pending(
            limit=limit,
            exclude_target_ids=exclude_target_ids,
        )

    def mark_target_cover_image_refresh_attempted(
        self,
        target_id: str,
        *,
        reported_url: str | None = None,
        requested_at: datetime | None = None,
    ) -> bool:
        """記錄 target cover image refresh 已開始嘗試。"""

        return self.cover_image_refresh_service.mark_attempted(
            target_id,
            reported_url=reported_url,
            requested_at=requested_at,
        )

    def mark_target_cover_image_refresh_succeeded(
        self,
        target_id: str,
        *,
        resolved_url: str,
        changed: bool,
        result: TargetCoverImageRefreshResult | None = None,
        reported_url: str | None = None,
        requested_at: datetime | None = None,
    ) -> bool:
        """標記 target cover image refresh 成功。"""

        return self.cover_image_refresh_service.mark_succeeded(
            target_id,
            resolved_url=resolved_url,
            changed=changed,
            result=result,
            reported_url=reported_url,
            requested_at=requested_at,
        )

    def mark_target_cover_image_refresh_stale_skipped(
        self,
        target_id: str,
        *,
        current_url: str,
        reported_url: str | None = None,
        requested_at: datetime | None = None,
    ) -> bool:
        """現行圖片 URL 已非 UI 上報 URL 時，清除過期 cover refresh job。"""

        return self.cover_image_refresh_service.mark_stale_skipped(
            target_id,
            current_url=current_url,
            reported_url=reported_url,
            requested_at=requested_at,
        )

    def mark_target_cover_image_refresh_failed(
        self,
        target_id: str,
        error: str,
        *,
        result: TargetCoverImageRefreshResult = TargetCoverImageRefreshResult.FAILED,
        reported_url: str | None = None,
        requested_at: datetime | None = None,
    ) -> bool:
        """標記 target cover image refresh 失敗。"""

        return self.cover_image_refresh_service.mark_failed(
            target_id,
            error,
            result=result,
            reported_url=reported_url,
            requested_at=requested_at,
        )

    def mark_target_metadata_refresh_pending(self, target_id: str) -> TargetDescriptor:
        """標記 target 正等待 resident worker 補齊 metadata。"""

        return self.registry_service.mark_target_metadata_refresh_pending(target_id)

    def mark_target_metadata_refresh_failed(
        self,
        target_id: str,
        error: str,
    ) -> TargetDescriptor:
        """標記 target metadata 補齊失敗。"""

        return self.registry_service.mark_target_metadata_refresh_failed(target_id, error)

    def update_target_name(self, target_id: str, name: str) -> TargetDescriptor:
        """更新使用者自訂 target 顯示名稱。"""

        return self.registry_service.update_target_name(target_id, name)

    def upsert_group_posts_target(
        self,
        request: _target_requests.UpsertGroupPostsTargetRequest,
    ) -> TargetDescriptor:
        """建立或更新 group posts target。"""

        return self.registry_service.upsert_group_posts_target(request)

    def upsert_comments_target(
        self,
        request: _target_requests.UpsertCommentsTargetRequest,
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

    def update_target_config(
        self,
        request: _target_requests.UpdateTargetConfigRequest,
    ) -> TargetConfig:
        """更新單一 target 監視設定。"""

        return self.config_service.update_target_config(request)

    def ensure_runtime_state(self, target_id: str) -> TargetRuntimeState:
        """確保 target 已有 runtime state，供 scheduler/UI 查詢。"""

        return self.runtime_service.ensure_runtime_state(target_id)

    def mark_target_queued(self, target_id: str, reason: str) -> TargetRuntimeState:
        """Legacy convenience API；queue admission 失敗時直接報錯。"""

        return self.runtime_service.mark_target_queued(target_id, reason)

    def try_mark_target_queued(self, target_id: str, reason: str) -> QueueAdmissionResult:
        """嘗試排入 executor queue，回傳本輪 DB admission 是否成功。"""

        return self.runtime_service.try_mark_target_queued(target_id, reason)

    def mark_target_running(
        self,
        target_id: str,
        worker_id: str,
        *,
        page_id: str = "",
    ) -> TargetRuntimeState:
        """標記單一 target 正由 scheduler/worker 掃描中。"""

        return self.runtime_service.mark_target_running(target_id, worker_id, page_id=page_id)

    def force_mark_target_running(
        self,
        target_id: str,
        worker_id: str,
        *,
        page_id: str = "",
    ) -> TargetRuntimeState:
        """無條件覆寫 running ownership；只供顯式 force path 使用。"""

        return self.runtime_service.force_mark_target_running(
            target_id,
            worker_id,
            page_id=page_id,
        )

    def try_claim_target_running(
        self,
        target_id: str,
        worker_id: str,
        *,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """嘗試取得 running ownership；失敗時不得覆蓋既有 owner。"""

        return self.runtime_service.try_claim_target_running(
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

    def force_mark_target_page_reloaded(
        self,
        target_id: str,
        *,
        page_id: str = "",
        reloaded_at: datetime | None = None,
    ) -> TargetRuntimeState:
        """無條件記錄 resident page reload/goto。"""

        return self.runtime_service.force_mark_target_page_reloaded(
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
        """以 running owner guard 記錄 resident page reload/goto。"""

        return self.runtime_service.guarded_mark_target_page_reloaded(
            target_id,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
            reloaded_at=reloaded_at,
        )

    def record_target_heartbeat(
        self,
        target_id: str,
        *,
        worker_id: str = "",
        page_id: str = "",
    ) -> TargetRuntimeState:
        """刷新 running target heartbeat，供長掃描與 stale recovery 區分。"""

        return self.runtime_service.record_target_heartbeat(
            target_id,
            worker_id=worker_id,
            page_id=page_id,
        )

    def guarded_record_target_heartbeat(
        self,
        target_id: str,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """以 running owner guard 刷新 heartbeat。"""

        return self.runtime_service.guarded_record_target_heartbeat(
            target_id,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def record_scan_guard_skip(self, target_id: str, reason: str) -> TargetRuntimeState:
        """記錄 target 被 queue/executor guard 擋下的原因。"""

        return self.runtime_service.record_scan_guard_skip(target_id, reason)

    def set_target_display_next_due_at(
        self,
        target_id: str,
        due_at: datetime | None,
    ) -> TargetRuntimeState | None:
        """更新 UI 顯示用 next due；不作為 scheduler 排程來源。"""

        return self.runtime_service.set_target_display_next_due_at(target_id, due_at)

    def mark_target_idle(self, target_id: str) -> TargetRuntimeState:
        """標記單一 target 已完成本輪掃描並回到 idle。"""

        return self.runtime_service.mark_target_idle(target_id)

    def force_mark_target_idle(self, target_id: str) -> TargetRuntimeState:
        """無條件將 target 標回 idle；只供顯式 force path 使用。"""

        return self.runtime_service.force_mark_target_idle(target_id)

    def force_request_target_retry_after_runtime_restart(
        self,
        target_id: str,
    ) -> TargetRuntimeState:
        """runtime restart recovery：清 owner 並要求新 runtime 補掃。"""

        return self.runtime_service.force_request_target_retry_after_runtime_restart(
            target_id,
        )

    def record_guarded_target_retry_after_sqlite_lock(
        self,
        target_id: str,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """DB lock 中止後補掃；只有 running owner 相符時才更新。"""

        return self.runtime_service.record_guarded_target_retry_after_sqlite_lock(
            target_id,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def record_non_running_target_retry_after_sqlite_lock(
        self,
        target_id: str,
    ) -> TargetRuntimeState | None:
        """DB lock 發生於 claim 前時，只更新非 running row。"""

        return self.runtime_service.record_non_running_target_retry_after_sqlite_lock(
            target_id,
        )

    def mark_target_idle_if_not_running(
        self,
        target_id: str,
    ) -> TargetRuntimeState | None:
        """只在 row 不是 running owner 時將 target 標回 idle。"""

        return self.runtime_service.mark_target_idle_if_not_running(target_id)

    def guarded_mark_target_idle(
        self,
        target_id: str,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """以 running owner guard 將 target 標回 idle。"""

        return self.runtime_service.guarded_mark_target_idle(
            target_id,
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
        """依目前 skipped scan streak 決定是否升級為 failure。"""

        return self.runtime_service.decide_scan_skip(
            target_id,
            reason,
            skip_limit=skip_limit,
        )

    def apply_scan_skip_decision(
        self,
        target_id: str,
        decision: ScanSkipDecision,
    ) -> TargetRuntimeState:
        """記錄保護性 skipped scan 並回 idle。"""

        return self.runtime_service.apply_scan_skip_decision(target_id, decision)

    def force_apply_scan_skip_decision(
        self,
        target_id: str,
        decision: ScanSkipDecision,
    ) -> TargetRuntimeState:
        """無條件套用 skipped scan decision。"""

        return self.runtime_service.force_apply_scan_skip_decision(target_id, decision)

    def guarded_apply_scan_skip_decision(
        self,
        target_id: str,
        decision: ScanSkipDecision,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """以 running owner guard 套用 skipped scan decision。"""

        return self.runtime_service.guarded_apply_scan_skip_decision(
            target_id,
            decision,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def mark_target_retriable_failure(
        self,
        target_id: str,
        decision: ScanFailureDecision,
    ) -> TargetRuntimeState:
        """記錄可重試失敗並回 idle，保留 failure streak。"""

        return self.runtime_service.mark_target_retriable_failure(target_id, decision)

    def force_mark_target_retriable_failure(
        self,
        target_id: str,
        decision: ScanFailureDecision,
    ) -> TargetRuntimeState:
        """無條件記錄可重試失敗；只供顯式 force path 使用。"""

        return self.runtime_service.force_mark_target_retriable_failure(
            target_id,
            decision,
        )

    def guarded_mark_target_retriable_failure(
        self,
        target_id: str,
        decision: ScanFailureDecision,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """以 running owner guard 記錄可重試失敗。"""

        return self.runtime_service.guarded_mark_target_retriable_failure(
            target_id,
            decision,
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

        return self.runtime_service.mark_target_error(
            target_id,
            error,
            failure_reason=failure_reason,
            failure_count=failure_count,
        )

    def force_mark_target_error(
        self,
        target_id: str,
        error: str,
        *,
        failure_reason: str = "",
        failure_count: int = 0,
    ) -> TargetRuntimeState:
        """無條件將 target 標記為 error。"""

        return self.runtime_service.force_mark_target_error(
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
        """以 running owner guard 將 target 標記為 error。"""

        return self.runtime_service.guarded_mark_target_error(
            target_id,
            error,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
            failure_reason=failure_reason,
            failure_count=failure_count,
        )

    def decide_scan_failure(
        self,
        target_id: str,
        reason: str,
        *,
        source: ScanFailureSource,
    ) -> ScanFailureDecision:
        """依目前 runtime streak 決定 scan failure 處置。"""

        return self.runtime_service.decide_scan_failure(target_id, reason, source=source)

    def apply_scan_failure_decision(
        self,
        target_id: str,
        decision: ScanFailureDecision,
        error: str,
    ) -> TargetRuntimeState:
        """依共用 failure decision 更新 target runtime state。"""

        return self.runtime_service.apply_scan_failure_decision(target_id, decision, error)

    def force_apply_scan_failure_decision(
        self,
        target_id: str,
        decision: ScanFailureDecision,
        error: str,
    ) -> TargetRuntimeState:
        """無條件套用 failure decision。"""

        return self.runtime_service.force_apply_scan_failure_decision(
            target_id,
            decision,
            error,
        )

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
        """以 running owner guard 套用 failure decision。"""

        return self.runtime_service.guarded_apply_scan_failure_decision(
            target_id,
            decision,
            error,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def recover_stale_running_targets(
        self,
        *,
        stale_after_seconds: float,
        now: datetime | None = None,
    ) -> tuple[StaleRunningRecovery, ...]:
        """修復 heartbeat 過舊的 running target。"""

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

    def clear_target_scan_request_if_not_newer(
        self,
        target_id: str,
        consumed_at: datetime | None,
    ) -> TargetRuntimeState:
        """清除已入隊的 scan request，但保留入隊後新送出的 request。"""

        return self.runtime_service.clear_target_scan_request_if_not_newer(
            target_id,
            consumed_at,
        )

    def update_target_status(
        self,
        request: _target_requests.UpdateTargetStatusRequest,
    ) -> TargetDescriptor:
        """更新 target 啟停狀態。"""

        return self.monitoring_commands.update_target_status(request)

    def restart_target_monitoring(self, target_id: str) -> TargetDescriptor:
        """執行 target「開始」語義：恢復監看並要求立即掃描。"""

        return self.monitoring_commands.restart_target_monitoring(target_id)

    def reset_target_notification_state(
        self,
        target_id: str,
    ) -> ResetTargetNotificationStateResult:
        """重置單一 target 的通知與 seen 去重狀態，讓下一輪可重新通知。"""

        return self.monitoring_commands.reset_target_notification_state(target_id)

    def pause_target_monitoring(self, target_id: str) -> TargetDescriptor:
        """執行 target「停止」語義：停止排程但保留 seen/history。"""

        return self.monitoring_commands.pause_target_monitoring(target_id)

    def pause_all_targets_for_webui_startup(
        self,
        *,
        default_fixed_refresh_sec: int = (
            _target_requests.DEFAULT_WEBUI_FIXED_REFRESH_SECONDS
        ),
    ) -> None:
        """Web UI 啟動時停止所有 target，並補齊固定掃描間隔設定。"""

        return self.monitoring_commands.pause_all_targets_for_webui_startup(
            default_fixed_refresh_sec=default_fixed_refresh_sec,
        )


__all__ = [
    "CoverImageRefreshRequestResult",
    "TargetApplicationService",
]
