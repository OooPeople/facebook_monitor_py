"""Target runtime recovery helpers。

職責：提供正式 resident main 與 one-shot fallback scheduler 共用的 runtime state
修復規則，避免正式 worker 從 fallback scheduler loop 匯入 helper。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_runtime_service import StaleRunningRecovery
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.scan_failure_policy import ScanFailureDecision
from facebook_monitor.core.scan_failures import STALE_RUNNING_REASON
from facebook_monitor.notifications.outbox_service import (
    queue_runtime_failure_notifications_after_commit,
)
from facebook_monitor.worker.scan_failure_finalize import record_scan_failure


@dataclass(frozen=True)
class RunningRecoveryAction:
    """保存 resident process 需要套用的 stale running 資源回收動作。"""

    target_id: str
    worker_id: str
    started_at: datetime
    page_id: str
    decision: ScanFailureDecision
    state: TargetRuntimeState
    scan_run_id: int = 0

    @property
    def terminal(self) -> bool:
        """回傳本次 recovery 是否已停止 target 自動恢復。"""

        return self.decision.terminal

    @property
    def owner_key(self) -> str:
        """回傳 resident queue / executor 使用的 attempt owner token。"""

        return build_recovery_owner_key(
            worker_id=self.worker_id,
            started_at=self.started_at,
            page_id=self.page_id,
        )


@dataclass(frozen=True)
class RuntimeRecoverySummary:
    """保存一次 runtime recovery 的 DB 與 resident cleanup 結果。"""

    queued_count: int = 0
    running_actions: tuple[RunningRecoveryAction, ...] = ()

    @property
    def recovered_count(self) -> int:
        """回傳本輪修復的 runtime state 筆數。"""

        return self.queued_count + len(self.running_actions)


def recover_stale_running_targets(db_path: Path, stale_after_seconds: float) -> int:
    """修復過舊的 running runtime state，回傳修復筆數。"""

    return len(
        recover_stale_runtime_targets_detailed(
            db_path,
            stale_after_seconds,
        ).running_actions
    )


def recover_stale_queued_targets(db_path: Path, stale_after_seconds: float) -> int:
    """修復過舊的 queued runtime state，回傳修復筆數。"""

    with SqliteApplicationContext(db_path) as app:
        return len(
            app.services.targets.recover_stale_queued_targets(
                stale_after_seconds=stale_after_seconds,
            )
        )


def recover_stale_runtime_targets(db_path: Path, stale_after_seconds: float) -> int:
    """修復所有會讓 target 卡住的過舊 runtime state。"""

    return recover_stale_runtime_targets_detailed(
        db_path,
        stale_after_seconds,
    ).recovered_count


def recover_stale_runtime_targets_detailed(
    db_path: Path,
    stale_after_seconds: float,
) -> RuntimeRecoverySummary:
    """修復 stale runtime state，並回傳 resident process 需套用的 recovery actions。"""

    with SqliteApplicationContext(db_path) as app:
        queued = app.services.targets.recover_stale_queued_targets(
            stale_after_seconds=stale_after_seconds,
        )
        running = app.services.targets.recover_stale_running_targets(
            stale_after_seconds=stale_after_seconds,
        )
        actions = tuple(
            _record_stale_running_failure(
                app=app,
                recovery=recovery,
            )
            for recovery in running
        )
        return RuntimeRecoverySummary(
            queued_count=len(queued),
            running_actions=tuple(action for action in actions if action is not None),
        )


def _record_stale_running_failure(
    *,
    app: ApplicationContext,
    recovery: StaleRunningRecovery,
) -> RunningRecoveryAction | None:
    """為 stale running recovery 記錄 scan run，terminal 時排 runtime failure 通知。"""

    target = app.repositories.targets.get(recovery.state.target_id)
    if target is None:
        return None
    detail = f"worker heartbeat expired after {int(recovery.stale_after_seconds)} seconds"
    scan_run_id = record_scan_failure(
        app=app,
        target=target,
        reason=STALE_RUNNING_REASON,
        message=detail,
        worker_path="runtime_recovery",
        retryable=recovery.decision.retryable,
        runtime_action=recovery.decision.runtime_action,
        retry_streak=recovery.decision.retry_streak,
        retry_limit=recovery.decision.retry_limit,
        force_record=recovery.decision.counts_toward_streak,
    )
    if recovery.decision.terminal and scan_run_id > 0:
        config = app.services.targets.get_config_for_target(target)
        queue_runtime_failure_notifications_after_commit(
            app=app,
            target=target,
            config=config,
            scan_run_id=scan_run_id,
            reason=recovery.decision.reason,
            failure_count=max(recovery.decision.retry_streak, 1),
            error_message=recovery.state.last_error,
        )
    return RunningRecoveryAction(
        target_id=recovery.state.target_id,
        worker_id=recovery.previous_worker_id,
        started_at=recovery.previous_started_at,
        page_id=recovery.previous_page_id,
        decision=recovery.decision,
        state=recovery.state,
        scan_run_id=scan_run_id,
    )


def build_recovery_owner_key(
    *,
    worker_id: str,
    started_at: datetime,
    page_id: str,
) -> str:
    """建立 resident in-memory ownership guard token。"""

    return f"{worker_id}|{started_at.isoformat()}|{page_id}"
