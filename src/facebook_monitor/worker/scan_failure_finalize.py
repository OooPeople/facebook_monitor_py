"""Shared scan failure finalize。

職責：統一 worker / scheduler failure scan run 的寫入格式，避免各路徑
用不同 metadata shape 記錄錯誤。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
from pathlib import Path
from typing import Any

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.scan_recording_service import RecordScanRequest
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.scan_failure_policy import ScanFailureDecision
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
from facebook_monitor.core.scan_failures import PROFILE_SESSION_FAILURE_REASONS
from facebook_monitor.core.scan_failures import TARGET_STOPPED_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.core.user_messages import format_failure_message
from facebook_monitor.core.user_messages import format_failure_retry_exhausted_message
from facebook_monitor.notifications.outbox_runtime_failure_enqueue import (
    queue_runtime_failure_notifications_after_commit,
)
from facebook_monitor.persistence.sqlite_retry import run_sqlite_operation_with_retry
from facebook_monitor.persistence.sqlite_retry import run_sqlite_operation_with_retry_async
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.scan_commit_guard import ScanCommitGuard
from facebook_monitor.worker.scan_commit_guard import begin_scan_commit_transaction
from facebook_monitor.worker.scan_finalize import ensure_target_allows_scan_commit


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanFailureMetadata:
    """保存失敗 scan run 的標準 metadata。"""

    worker_mode: WorkerMode
    worker_path: str
    target_kind: str
    reason: str
    exception_class: str = ""
    retryable: bool = False
    profile_lease_state: str = ""
    page_reused: bool | None = None
    scan_request_id: str = ""
    runtime_action: str = ""
    retry_streak: int = 0
    retry_limit: int = 0
    auto_restart: bool = False
    recovery_action: str = ""
    raw_failure_detail: str = ""

    def to_metadata(self) -> dict[str, Any]:
        """轉成 scan run JSON metadata。"""

        metadata: dict[str, Any] = {
            "worker": self.worker_path,
            "worker_mode": self.worker_mode.value,
            "target_kind": self.target_kind,
            "reason": self.reason,
            "exception_class": self.exception_class,
            "retryable": self.retryable,
            "profile_lease_state": self.profile_lease_state,
            "scan_request_id": self.scan_request_id,
        }
        if self.page_reused is not None:
            metadata["page_reused"] = self.page_reused
        if self.runtime_action:
            metadata["runtime_action"] = self.runtime_action
        if self.recovery_action:
            metadata["recovery_action"] = self.recovery_action
        if self.auto_restart:
            metadata["auto_restart"] = True
        if self.retry_limit > 0:
            metadata["retry_streak"] = max(self.retry_streak, 0)
            metadata["retry_limit"] = self.retry_limit
        raw_failure_detail = self.raw_failure_detail.strip()
        if raw_failure_detail:
            metadata["raw_failure_detail"] = raw_failure_detail
        return metadata


@dataclass(frozen=True)
class GuardedScanFailureFinalizeResult:
    """保存 guarded failure finalize 實際完成的 side effect 摘要。"""

    decision: ScanFailureDecision
    scan_run_id: int = 0
    runtime_failure_notification_count: int = 0

    @property
    def failure_scan_run_written(self) -> bool:
        """回傳本次 finalize 是否新增 failure scan run。"""

        return self.scan_run_id > 0


class GuardedScanFailureFinalizeRejectedKind(StrEnum):
    """guarded failure finalize 在寫入前被拒絕的分類。"""

    TARGET_INACTIVE = "target_inactive"
    GUARD_MISMATCH = "guard_mismatch"


@dataclass(frozen=True)
class GuardedScanFailureFinalizeRejected:
    """保存 failure finalize 未寫入時的 guard rejection reason。"""

    kind: GuardedScanFailureFinalizeRejectedKind
    reason: str


GuardedScanFailureFinalizeOutcome = (
    GuardedScanFailureFinalizeResult | GuardedScanFailureFinalizeRejected
)


def record_scan_failure(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    reason: str,
    message: str,
    worker_path: str,
    worker_mode: WorkerMode = WorkerMode.HEADLESS,
    exception_class: str = "",
    retryable: bool = False,
    profile_lease_state: str = "",
    page_reused: bool | None = None,
    scan_request_id: str = "",
    runtime_action: str = "",
    retry_streak: int = 0,
    retry_limit: int = 0,
    auto_restart: bool = False,
    recovery_action: str = "",
    force_record: bool = False,
    error_message_override: str = "",
) -> int:
    """透過 application context 記錄一筆標準失敗 scan run。"""

    if reason in PROFILE_SESSION_FAILURE_REASONS:
        app.repositories.app_settings.mark_profile_needs_login(
            reason=reason,
            source=worker_path,
        )
    error_message = error_message_override or format_scan_failure_message(reason, message)
    latest = app.repositories.scan_runs.latest_by_target(target.id)
    if (
        not force_record
        and latest is not None
        and latest.status == ScanStatus.FAILED
        and latest.error_message == error_message
    ):
        return 0
    return app.services.scans.record_scan(
        RecordScanRequest(
            target_id=target.id,
            status=ScanStatus.FAILED,
            error_message=error_message,
            worker_mode=worker_mode,
            metadata=ScanFailureMetadata(
                worker_mode=worker_mode,
                worker_path=worker_path,
                target_kind=target.target_kind.value,
                reason=reason,
                exception_class=exception_class,
                retryable=retryable,
                profile_lease_state=profile_lease_state,
                page_reused=page_reused,
                scan_request_id=scan_request_id,
                runtime_action=runtime_action,
                retry_streak=retry_streak,
                retry_limit=retry_limit,
                auto_restart=auto_restart,
                recovery_action=recovery_action,
                raw_failure_detail=message,
            ).to_metadata(),
        )
    )


def record_guarded_scan_failure_result(
    *,
    app: ApplicationContext,
    target_id: str,
    reason: str,
    message: str,
    source: ScanFailureSource,
    worker_path: str,
    commit_guard: ScanCommitGuard | None,
    worker_mode: WorkerMode = WorkerMode.HEADLESS,
    exception_class: str = "",
    profile_lease_state: str = "",
    page_reused: bool | None = None,
    scan_request_id: str = "",
    runtime_error_message: str | None = None,
) -> GuardedScanFailureFinalizeOutcome:
    """在同一 transaction 內確認 guard，並回傳 failure side-effect 摘要。"""

    begin_scan_commit_transaction(app)
    target = app.repositories.targets.get(target_id)
    if target is None:
        return GuardedScanFailureFinalizeRejected(
            GuardedScanFailureFinalizeRejectedKind.TARGET_INACTIVE,
            "target_missing_before_commit",
        )
    try:
        ensure_target_allows_scan_commit(
            app=app,
            target=target,
            commit_guard=commit_guard,
        )
    except WorkerFailure as exc:
        return _classify_guarded_scan_failure_rejection(
            app=app,
            target_id=target.id,
            commit_guard=commit_guard,
            fallback_reason=exc.reason,
        )
    decision = app.services.targets.decide_scan_failure(
        target_id,
        reason,
        source=source,
    )
    scan_error_message = format_scan_failure_run_message(
        reason=decision.reason,
        message=message,
        decision=decision,
    )
    scan_run_id = record_scan_failure(
        app=app,
        target=target,
        reason=decision.reason,
        message=message,
        worker_path=worker_path,
        worker_mode=worker_mode,
        exception_class=exception_class,
        retryable=decision.retryable,
        profile_lease_state=profile_lease_state,
        page_reused=page_reused,
        scan_request_id=scan_request_id,
        runtime_action=decision.runtime_action,
        retry_streak=decision.retry_streak,
        retry_limit=decision.retry_limit,
        auto_restart=decision.auto_restart,
        recovery_action=decision.recovery_action,
        force_record=decision.counts_toward_streak or decision.terminal,
        error_message_override=scan_error_message,
    )
    runtime_message = runtime_error_message or format_scan_failure_message(
        decision.reason,
        message,
    )
    if commit_guard is None:
        updated_state = app.services.targets.force_apply_scan_failure_decision(
            target_id,
            decision,
            runtime_message,
        )
    else:
        guarded_state = app.services.targets.guarded_apply_scan_failure_decision(
            target_id,
            decision,
            runtime_message,
            worker_id=commit_guard.worker_id,
            started_at=commit_guard.started_at,
            page_id=commit_guard.page_id,
        )
        if guarded_state is None:
            raise WorkerFailure(
                TARGET_STOPPED_REASON,
                "target stopped before failure runtime commit",
            )
        updated_state = guarded_state
    runtime_failure_notification_count = 0
    if decision.terminal and scan_run_id > 0:
        config = app.services.targets.get_config_for_target(target)
        entries = queue_runtime_failure_notifications_after_commit(
            app=app,
            target=target,
            config=config,
            scan_run_id=scan_run_id,
            reason=decision.reason,
            failure_count=decision.notification_failure_count,
            error_message=updated_state.last_error or runtime_message,
            failure_source=source,
        )
        runtime_failure_notification_count = len(entries)
    return GuardedScanFailureFinalizeResult(
        decision=decision,
        scan_run_id=scan_run_id,
        runtime_failure_notification_count=runtime_failure_notification_count,
    )


def record_guarded_scan_failure_decision(
    *,
    app: ApplicationContext,
    target_id: str,
    reason: str,
    message: str,
    source: ScanFailureSource,
    worker_path: str,
    commit_guard: ScanCommitGuard | None,
    worker_mode: WorkerMode = WorkerMode.HEADLESS,
    exception_class: str = "",
    profile_lease_state: str = "",
    page_reused: bool | None = None,
    scan_request_id: str = "",
    runtime_error_message: str | None = None,
) -> ScanFailureDecision | None:
    """Decision-only wrapper：保留既有 guarded failure finalize 回傳語義。"""

    result = record_guarded_scan_failure_result(
        app=app,
        target_id=target_id,
        reason=reason,
        message=message,
        source=source,
        worker_path=worker_path,
        commit_guard=commit_guard,
        worker_mode=worker_mode,
        exception_class=exception_class,
        profile_lease_state=profile_lease_state,
        page_reused=page_reused,
        scan_request_id=scan_request_id,
        runtime_error_message=runtime_error_message,
    )
    if isinstance(result, GuardedScanFailureFinalizeRejected):
        return None
    return result.decision


def record_guarded_scan_failure_decision_for_db(
    *,
    db_path: Path,
    target_id: str,
    reason: str,
    message: str,
    source: ScanFailureSource,
    worker_path: str,
    commit_guard: ScanCommitGuard | None,
    worker_mode: WorkerMode = WorkerMode.HEADLESS,
    exception_class: str = "",
    profile_lease_state: str = "",
    page_reused: bool | None = None,
    scan_request_id: str = "",
    runtime_error_message: str | None = None,
) -> ScanFailureDecision | None:
    """用 DB path 執行 decision-only guarded failure finalize。"""

    def operation() -> ScanFailureDecision | None:
        with SqliteApplicationContext(db_path) as app:
            return record_guarded_scan_failure_decision(
                app=app,
                target_id=target_id,
                reason=reason,
                message=message,
                source=source,
                worker_path=worker_path,
                commit_guard=commit_guard,
                worker_mode=worker_mode,
                exception_class=exception_class,
                profile_lease_state=profile_lease_state,
                page_reused=page_reused,
                scan_request_id=scan_request_id,
                runtime_error_message=runtime_error_message,
            )

    return run_sqlite_operation_with_retry(
        operation,
        operation_name="record_guarded_scan_failure_decision",
        logger=logger,
    )


async def record_guarded_scan_failure_result_for_db_async(
    *,
    db_path: Path,
    target_id: str,
    reason: str,
    message: str,
    source: ScanFailureSource,
    worker_path: str,
    commit_guard: ScanCommitGuard | None,
    worker_mode: WorkerMode = WorkerMode.HEADLESS,
    exception_class: str = "",
    profile_lease_state: str = "",
    page_reused: bool | None = None,
    scan_request_id: str = "",
    runtime_error_message: str | None = None,
) -> GuardedScanFailureFinalizeOutcome:
    """async resident 用 result-returning guarded failure finalize。"""

    def operation() -> GuardedScanFailureFinalizeOutcome:
        with SqliteApplicationContext(db_path) as app:
            return record_guarded_scan_failure_result(
                app=app,
                target_id=target_id,
                reason=reason,
                message=message,
                source=source,
                worker_path=worker_path,
                commit_guard=commit_guard,
                worker_mode=worker_mode,
                exception_class=exception_class,
                profile_lease_state=profile_lease_state,
                page_reused=page_reused,
                scan_request_id=scan_request_id,
                runtime_error_message=runtime_error_message,
            )

    return await run_sqlite_operation_with_retry_async(
        operation,
        operation_name="record_guarded_scan_failure_result",
        logger=logger,
    )


def _classify_guarded_scan_failure_rejection(
    *,
    app: ApplicationContext,
    target_id: str,
    commit_guard: ScanCommitGuard | None,
    fallback_reason: str,
) -> GuardedScanFailureFinalizeRejected:
    """把 failure guard rejection 分成 inactive 與 guard mismatch。"""

    target = app.repositories.targets.get(target_id)
    if target is None:
        return GuardedScanFailureFinalizeRejected(
            GuardedScanFailureFinalizeRejectedKind.TARGET_INACTIVE,
            "target_missing_before_commit",
        )
    if not target.enabled or target.paused:
        return GuardedScanFailureFinalizeRejected(
            GuardedScanFailureFinalizeRejectedKind.TARGET_INACTIVE,
            "target_inactive_before_commit",
        )
    runtime_state = app.repositories.runtime_states.get(target_id)
    if runtime_state is None:
        return GuardedScanFailureFinalizeRejected(
            GuardedScanFailureFinalizeRejectedKind.GUARD_MISMATCH,
            "runtime_state_missing_before_commit",
        )
    if runtime_state.desired_state != TargetDesiredState.ACTIVE:
        return GuardedScanFailureFinalizeRejected(
            GuardedScanFailureFinalizeRejectedKind.TARGET_INACTIVE,
            "target_inactive_before_commit",
        )
    if commit_guard is None:
        return GuardedScanFailureFinalizeRejected(
            GuardedScanFailureFinalizeRejectedKind.GUARD_MISMATCH,
            fallback_reason or "scan_failure_guard_mismatch",
        )
    if runtime_state.runtime_status != TargetRuntimeStatus.RUNNING:
        return GuardedScanFailureFinalizeRejected(
            GuardedScanFailureFinalizeRejectedKind.GUARD_MISMATCH,
            "runtime_not_running_before_commit",
        )
    if runtime_state.active_worker_id != commit_guard.worker_id:
        return GuardedScanFailureFinalizeRejected(
            GuardedScanFailureFinalizeRejectedKind.GUARD_MISMATCH,
            "owner_changed_before_commit",
        )
    if runtime_state.last_started_at != commit_guard.started_at:
        return GuardedScanFailureFinalizeRejected(
            GuardedScanFailureFinalizeRejectedKind.GUARD_MISMATCH,
            "scan_started_at_changed_before_commit",
        )
    if commit_guard.page_id and runtime_state.active_page_id != commit_guard.page_id:
        return GuardedScanFailureFinalizeRejected(
            GuardedScanFailureFinalizeRejectedKind.GUARD_MISMATCH,
            "page_owner_changed_before_commit",
        )
    return GuardedScanFailureFinalizeRejected(
        GuardedScanFailureFinalizeRejectedKind.GUARD_MISMATCH,
        fallback_reason or "scan_failure_guard_mismatch",
    )


def record_active_targets_runtime_failure_notifications_for_db(
    *,
    db_path: Path,
    reason: str,
    message: str,
    worker_path: str,
    worker_mode: WorkerMode = WorkerMode.HEADLESS,
    exception_class: str = "",
) -> int:
    """為 resident 全域錯誤通知目前 active targets，回傳新增 scan run 數。"""

    def operation() -> int:
        with SqliteApplicationContext(db_path) as app:
            return record_active_targets_runtime_failure_notifications(
                app=app,
                reason=reason,
                message=message,
                worker_path=worker_path,
                worker_mode=worker_mode,
                exception_class=exception_class,
            )

    return run_sqlite_operation_with_retry(
        operation,
        operation_name="record_active_targets_runtime_failure_notifications",
        logger=logger,
    )


def record_active_targets_runtime_failure_notifications(
    *,
    app: ApplicationContext,
    reason: str,
    message: str,
    worker_path: str,
    worker_mode: WorkerMode = WorkerMode.HEADLESS,
    exception_class: str = "",
) -> int:
    """將全域 resident failure 轉成每個 active target 的 scan run 與通知。"""

    normalized_reason = str(reason or UNKNOWN_REASON).strip() or UNKNOWN_REASON
    scan_run_count = 0
    begin_scan_commit_transaction(app)
    for target in app.repositories.targets.list_enabled():
        if target.target_kind not in {TargetKind.POSTS, TargetKind.COMMENTS}:
            continue
        if not _target_allows_active_runtime_failure(app, target.id):
            continue
        decision = app.services.targets.decide_scan_failure(
            target.id,
            normalized_reason,
            source="unknown_exception",
        )
        runtime_message = format_scan_failure_message(decision.reason, message)
        scan_error_message = format_scan_failure_run_message(
            reason=decision.reason,
            message=message,
            decision=decision,
        )
        scan_run_id = record_scan_failure(
            app=app,
            target=target,
            reason=decision.reason,
            message=message,
            worker_path=worker_path,
            worker_mode=worker_mode,
            exception_class=exception_class,
            retryable=decision.retryable,
            runtime_action=decision.runtime_action,
            retry_streak=decision.retry_streak,
            retry_limit=decision.retry_limit,
            auto_restart=decision.auto_restart,
            recovery_action=decision.recovery_action,
            force_record=decision.counts_toward_streak or decision.terminal,
            error_message_override=scan_error_message,
        )
        updated_state = app.services.targets.force_apply_scan_failure_decision(
            target.id,
            decision,
            runtime_message,
        )
        if scan_run_id <= 0:
            continue
        scan_run_count += 1
        if not decision.terminal:
            continue
        config = app.services.targets.get_config_for_target(target)
        queue_runtime_failure_notifications_after_commit(
            app=app,
            target=target,
            config=config,
            scan_run_id=scan_run_id,
            reason=decision.reason,
            failure_count=decision.notification_failure_count,
            error_message=updated_state.last_error or runtime_message,
            target_stopped=True,
            failure_source="unknown_exception",
        )
    return scan_run_count


def _target_allows_active_runtime_failure(
    app: ApplicationContext,
    target_id: str,
) -> bool:
    """確認全域 runtime failure 仍可套用到目前 active target。"""

    target = app.repositories.targets.get(target_id)
    if target is None or not target.enabled or target.paused:
        return False
    runtime_state = app.services.targets.ensure_runtime_state(target_id)
    return (
        runtime_state.desired_state == TargetDesiredState.ACTIVE
        and runtime_state.runtime_status != TargetRuntimeStatus.ERROR
    )


def format_scan_failure_message(reason: str, message: str) -> str:
    """建立一致的 scan failure error_message。"""

    return format_failure_message(reason, message)


def format_scan_failure_run_message(
    *,
    reason: str,
    message: str,
    decision: ScanFailureDecision,
) -> str:
    """依 failure decision 建立 scan run 顯示訊息。"""

    if decision.terminal and decision.counts_toward_streak:
        return format_failure_retry_exhausted_message(
            reason,
            retry_streak=decision.retry_streak,
            retry_limit=decision.retry_limit,
        )
    return format_scan_failure_message(reason, message)
