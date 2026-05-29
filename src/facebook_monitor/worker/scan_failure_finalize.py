"""Shared scan failure finalize。

職責：統一 worker / scheduler failure scan run 的寫入格式，避免各路徑
用不同 metadata shape 記錄錯誤。
"""

from __future__ import annotations

from dataclasses import dataclass
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
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.core.user_messages import format_failure_message
from facebook_monitor.notifications.outbox_service import (
    enqueue_runtime_failure_notifications,
    queue_runtime_failure_notifications_after_commit,
)
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import begin_scan_commit_transaction
from facebook_monitor.worker.scan_finalize import ensure_target_allows_scan_commit


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
        if self.retry_limit > 0:
            metadata["retry_streak"] = max(self.retry_streak, 0)
            metadata["retry_limit"] = self.retry_limit
        raw_failure_detail = self.raw_failure_detail.strip()
        if raw_failure_detail:
            metadata["raw_failure_detail"] = raw_failure_detail
        return metadata


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
    force_record: bool = False,
) -> int:
    """透過 application context 記錄一筆標準失敗 scan run。"""

    if reason in PROFILE_SESSION_FAILURE_REASONS:
        app.repositories.app_settings.mark_profile_needs_login(
            reason=reason,
            source=worker_path,
        )
    error_message = format_scan_failure_message(reason, message)
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
                raw_failure_detail=message,
            ).to_metadata(),
        )
    )


def record_guarded_scan_failure(
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
    """在同一 transaction 內確認 attempt guard、記錄 failure 並更新 runtime。"""

    begin_scan_commit_transaction(app)
    target = app.repositories.targets.get(target_id)
    if target is None:
        return None
    try:
        ensure_target_allows_scan_commit(
            app=app,
            target=target,
            commit_guard=commit_guard,
        )
    except WorkerFailure:
        return None
    decision = app.services.targets.decide_scan_failure(
        target_id,
        reason,
        source=source,
    )
    scan_run_id = record_scan_failure(
        app=app,
        target=target,
        reason=reason,
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
        force_record=decision.counts_toward_streak,
    )
    runtime_message = runtime_error_message or format_scan_failure_message(
        decision.reason,
        message,
    )
    if commit_guard is None:
        updated_state = app.services.targets.apply_scan_failure_decision(
            target_id,
            decision,
            runtime_message,
        )
    else:
        guarded_state = app.services.targets.apply_scan_failure_decision_if_owner(
            target_id,
            decision,
            runtime_message,
            worker_id=commit_guard.worker_id,
            started_at=commit_guard.started_at,
            page_id=commit_guard.page_id,
        )
        if guarded_state is None:
            return None
        updated_state = guarded_state
    if decision.terminal and scan_run_id > 0:
        config = app.services.targets.get_config_for_target(target)
        failure_count = (
            decision.retry_streak
            if decision.counts_toward_streak
            else max(decision.retry_streak, 1)
        )
        queue_runtime_failure_notifications_after_commit(
            app=app,
            target=target,
            config=config,
            scan_run_id=scan_run_id,
            reason=decision.reason,
            failure_count=failure_count,
            error_message=updated_state.last_error or runtime_message,
        )
    return decision


def record_guarded_scan_failure_for_db(
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
    """用 DB path 執行 guarded failure finalize；stale attempt 回傳 None。"""

    with SqliteApplicationContext(db_path) as app:
        return record_guarded_scan_failure(
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

    with SqliteApplicationContext(db_path) as app:
        return record_active_targets_runtime_failure_notifications(
            app=app,
            reason=reason,
            message=message,
            worker_path=worker_path,
            worker_mode=worker_mode,
            exception_class=exception_class,
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
    for target in app.repositories.targets.list_enabled():
        if target.target_kind not in {TargetKind.POSTS, TargetKind.COMMENTS}:
            continue
        runtime_state = app.services.targets.ensure_runtime_state(target.id)
        if runtime_state.desired_state != TargetDesiredState.ACTIVE:
            continue
        if runtime_state.runtime_status == TargetRuntimeStatus.ERROR:
            continue
        decision = app.services.targets.decide_scan_failure(
            target.id,
            normalized_reason,
            source="unknown_exception",
        )
        runtime_message = format_scan_failure_message(normalized_reason, message)
        scan_run_id = record_scan_failure(
            app=app,
            target=target,
            reason=normalized_reason,
            message=message,
            worker_path=worker_path,
            worker_mode=worker_mode,
            exception_class=exception_class,
            retryable=decision.retryable,
            runtime_action=decision.runtime_action,
            retry_streak=decision.retry_streak,
            retry_limit=decision.retry_limit,
            force_record=decision.counts_toward_streak,
        )
        updated_state = app.services.targets.apply_scan_failure_decision(
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
        enqueue_runtime_failure_notifications(
            app=app,
            target=target,
            config=config,
            scan_run_id=scan_run_id,
            reason=normalized_reason,
            failure_count=(
                decision.retry_streak
                if decision.counts_toward_streak
                else max(decision.retry_streak, 1)
            ),
            error_message=updated_state.last_error or runtime_message,
            target_stopped=True,
        )
    return scan_run_count


def format_scan_failure_message(reason: str, message: str) -> str:
    """建立一致的 scan failure error_message。"""

    return format_failure_message(reason, message)
