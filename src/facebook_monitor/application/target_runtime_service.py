"""Target runtime application service。

職責：保留 scheduler/executor 對單一 target runtime state 的正式 façade。
具體子職責委派給 access / ownership / lifecycle / outcome / request / recovery services。
"""

from __future__ import annotations

from datetime import datetime

from facebook_monitor.application.target_runtime_access import TargetRuntimeAccess
from facebook_monitor.application.target_runtime_decisions import ScanSkipDecision
from facebook_monitor.application.target_runtime_lifecycle import (
    TargetRuntimeLifecycleService,
)
from facebook_monitor.application.target_runtime_ownership import (
    TargetRuntimeOwnershipService,
)
from facebook_monitor.application.target_runtime_outcomes import (
    TargetRuntimeOutcomeService,
)
from facebook_monitor.application.target_runtime_recovery import StaleRunningRecovery
from facebook_monitor.application.target_runtime_recovery import (
    TargetRuntimeRecoveryService,
)
from facebook_monitor.application.target_runtime_requests import (
    TargetRuntimeRequestService,
)
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.scan_failure_policy import ScanFailureDecision
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
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
        self._access = TargetRuntimeAccess(targets, runtime_states)
        self._ownership = TargetRuntimeOwnershipService(self._access)
        self._lifecycle = TargetRuntimeLifecycleService(self._access)
        self._outcomes = TargetRuntimeOutcomeService(self._access)
        self._requests = TargetRuntimeRequestService(self._access)
        self._recovery = TargetRuntimeRecoveryService(self._access)

    def ensure_runtime_state(self, target_id: str) -> TargetRuntimeState:
        """確保 target 已有 runtime state，供 scheduler/UI 查詢。"""

        return self._access.ensure_runtime_state(target_id)

    def mark_target_queued(self, target_id: str, reason: str) -> TargetRuntimeState:
        """標記單一 target 已進入 executor queue，等待 worker slot。"""

        return self._ownership.mark_target_queued(target_id, reason)

    def mark_target_running(
        self,
        target_id: str,
        worker_id: str,
        *,
        page_id: str = "",
    ) -> TargetRuntimeState:
        """標記單一 target 正由 scheduler/worker 掃描中。"""

        return self._ownership.mark_target_running(
            target_id,
            worker_id,
            page_id=page_id,
        )

    def force_mark_target_running(
        self,
        target_id: str,
        worker_id: str,
        *,
        page_id: str = "",
    ) -> TargetRuntimeState:
        """無條件覆寫 running ownership；只供 maintenance / fallback 顯式使用。"""

        return self._ownership.force_mark_target_running(
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

        return self._ownership.try_claim_target_running(
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

        return self._ownership.mark_target_page_reloaded(
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
        """無條件記錄 page reload；呼叫端必須已確認不需要 owner guard。"""

        return self._ownership.force_mark_target_page_reloaded(
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

        return self._ownership.guarded_mark_target_page_reloaded(
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

        return self._ownership.record_target_heartbeat(
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
        """以 running owner guard 刷新 heartbeat；stale owner 回傳 None。"""

        return self._ownership.guarded_record_target_heartbeat(
            target_id,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def record_scan_guard_skip(self, target_id: str, reason: str) -> TargetRuntimeState:
        """記錄 target 被 queue/executor guard 擋下的原因。"""

        return self._ownership.record_scan_guard_skip(target_id, reason)

    def set_target_display_next_due_at(
        self,
        target_id: str,
        due_at: datetime | None,
    ) -> TargetRuntimeState | None:
        """更新 UI 顯示用 next due；不作為 scheduler 排程來源。"""

        return self._requests.set_target_display_next_due_at(target_id, due_at)

    def reset_target_desired_state(
        self,
        target_id: str,
        desired_state: TargetDesiredState,
    ) -> TargetRuntimeState:
        """重設 target runtime state，供啟停 command 對齊 desired state。"""

        return self._lifecycle.reset_target_desired_state(target_id, desired_state)

    def restart_target_runtime(self, target_id: str) -> TargetRuntimeState:
        """套用 target「開始」時需要的 runtime reset 與立即掃描要求。"""

        return self._lifecycle.restart_target_runtime(target_id)

    def force_request_target_retry_after_runtime_restart(
        self,
        target_id: str,
    ) -> TargetRuntimeState:
        """runtime restart recovery：無條件清 owner 並要求新 runtime 補掃。"""

        return self._lifecycle.force_request_target_retry_after_runtime_restart(target_id)

    def record_guarded_target_retry_after_sqlite_lock(
        self,
        target_id: str,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """DB lock 中止後的補掃寫回；只有 running owner 相符時才更新。"""

        return self._lifecycle.record_guarded_target_retry_after_sqlite_lock(
            target_id,
            worker_id=worker_id,
            started_at=started_at,
            page_id=page_id,
        )

    def record_non_running_target_retry_after_sqlite_lock(
        self,
        target_id: str,
    ) -> TargetRuntimeState | None:
        """DB lock 發生於 claim 前時，只允許非 running row 留下補掃要求。"""

        return self._lifecycle.record_non_running_target_retry_after_sqlite_lock(target_id)

    def mark_target_idle(self, target_id: str) -> TargetRuntimeState:
        """標記單一 target 已完成本輪掃描並回到 idle。"""

        return self._outcomes.mark_target_idle(target_id)

    def force_mark_target_idle(self, target_id: str) -> TargetRuntimeState:
        """無條件將 target 標回 idle；呼叫端必須顯式接受覆寫 owner。"""

        return self._outcomes.force_mark_target_idle(target_id)

    def mark_target_idle_if_not_running(self, target_id: str) -> TargetRuntimeState | None:
        """只在 row 不是 running owner 時將 target 標回 idle。"""

        return self._outcomes.mark_target_idle_if_not_running(target_id)

    def guarded_mark_target_idle(
        self,
        target_id: str,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """以 running owner guard 將 target 標回 idle；stale owner 回傳 None。"""

        return self._outcomes.guarded_mark_target_idle(
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
        """依目前 skipped scan streak 決定本輪 skip 是否要升級成失敗。"""

        return self._outcomes.decide_scan_skip(
            target_id,
            reason,
            skip_limit=skip_limit,
        )

    def apply_scan_skip_decision(
        self,
        target_id: str,
        decision: ScanSkipDecision,
    ) -> TargetRuntimeState:
        """記錄保護性 skipped scan 並回 idle，保留既有 failure streak。"""

        return self._outcomes.apply_scan_skip_decision(target_id, decision)

    def force_apply_scan_skip_decision(
        self,
        target_id: str,
        decision: ScanSkipDecision,
    ) -> TargetRuntimeState:
        """無條件套用 skipped scan decision；呼叫端必須顯式接受覆寫 owner。"""

        return self._outcomes.force_apply_scan_skip_decision(target_id, decision)

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

        return self._outcomes.guarded_apply_scan_skip_decision(
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
        """記錄本輪可重試失敗，讓 target 回到 idle 供下一輪排程。"""

        return self._outcomes.mark_target_retriable_failure(target_id, decision)

    def force_mark_target_retriable_failure(
        self,
        target_id: str,
        decision: ScanFailureDecision,
    ) -> TargetRuntimeState:
        """無條件記錄可重試失敗；呼叫端必須顯式接受覆寫 owner。"""

        return self._outcomes.force_mark_target_retriable_failure(target_id, decision)

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

        return self._outcomes.guarded_mark_target_retriable_failure(
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

        return self._outcomes.mark_target_error(
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
        """無條件將 target 標記為 error；呼叫端必須顯式接受覆寫 owner。"""

        return self._outcomes.force_mark_target_error(
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

        return self._outcomes.guarded_mark_target_error(
            target_id,
            error,
            failure_reason=failure_reason,
            failure_count=failure_count,
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

        return self._outcomes.decide_scan_failure(
            target_id,
            reason,
            source=source,
        )

    def apply_scan_failure_decision(
        self,
        target_id: str,
        decision: ScanFailureDecision,
        error: str,
    ) -> TargetRuntimeState:
        """依共用 failure decision 更新 target runtime state。"""

        return self._outcomes.apply_scan_failure_decision(target_id, decision, error)

    def force_apply_scan_failure_decision(
        self,
        target_id: str,
        decision: ScanFailureDecision,
        error: str,
    ) -> TargetRuntimeState:
        """無條件套用 failure decision；呼叫端必須顯式接受覆寫 owner。"""

        return self._outcomes.force_apply_scan_failure_decision(
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
        """以 running owner guard 套用 failure decision；stale owner 回傳 None。"""

        return self._outcomes.guarded_apply_scan_failure_decision(
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
        """修復 heartbeat 過舊的 running target，避免永久卡住。"""

        return self._recovery.recover_stale_running_targets(
            stale_after_seconds=stale_after_seconds,
            now=now,
        )

    def recover_stale_queued_targets(
        self,
        *,
        stale_after_seconds: float,
        now: datetime | None = None,
    ) -> tuple[TargetRuntimeState, ...]:
        """將排隊過久的 target 回復 idle，避免 scheduler 永久跳過。"""

        return self._recovery.recover_stale_queued_targets(
            stale_after_seconds=stale_after_seconds,
            now=now,
        )

    def request_target_scan(self, target_id: str) -> TargetRuntimeState:
        """要求 scheduler 下一輪立即掃描 target，不修改 seen 狀態。"""

        return self._requests.request_target_scan(target_id)

    def clear_target_scan_request(self, target_id: str) -> TargetRuntimeState:
        """清除已被 scheduler 消化的立即掃描要求。"""

        return self._requests.clear_target_scan_request(target_id)

    def clear_target_scan_request_if_not_newer(
        self,
        target_id: str,
        consumed_at: datetime | None,
    ) -> TargetRuntimeState:
        """清除已入隊的 scan request，但保留入隊後新送出的 request。"""

        return self._requests.clear_target_scan_request_if_not_newer(
            target_id,
            consumed_at,
        )


__all__ = ["ScanSkipDecision", "StaleRunningRecovery", "TargetRuntimeService"]
