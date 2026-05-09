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
) -> int:
    """透過 application context 記錄一筆標準失敗 scan run。"""

    error_message = format_scan_failure_message(reason, message)
    latest = app.repositories.scan_runs.latest_by_target(target.id)
    if (
        latest is not None
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
        )


def format_scan_failure_message(reason: str, message: str) -> str:
    """建立一致的 scan failure error_message。"""

    return f"{reason}: {message}"
