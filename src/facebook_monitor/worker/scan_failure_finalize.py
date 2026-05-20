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
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.scan_failures import PROFILE_SESSION_FAILURE_REASONS
from facebook_monitor.core.user_messages import format_failure_message


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


def record_scan_failure_for_db(
    *,
    db_path: Path,
    target: TargetDescriptor | None,
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
    """用 DB path 記錄標準失敗 scan run；未知 target 時不寫入。"""

    if target is None:
        return 0
    with SqliteApplicationContext(db_path) as app:
        return record_scan_failure(
            app=app,
            target=target,
            reason=reason,
            message=message,
            worker_path=worker_path,
            worker_mode=worker_mode,
            exception_class=exception_class,
            retryable=retryable,
            profile_lease_state=profile_lease_state,
            page_reused=page_reused,
            scan_request_id=scan_request_id,
            runtime_action=runtime_action,
            retry_streak=retry_streak,
            retry_limit=retry_limit,
            force_record=force_record,
        )


def format_scan_failure_message(reason: str, message: str) -> str:
    """建立一致的 scan failure error_message。"""

    return format_failure_message(reason, message)
