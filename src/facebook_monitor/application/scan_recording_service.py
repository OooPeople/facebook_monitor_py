"""Scan recording application service。

職責：集中 scan success/failure 的 ScanRun 寫入入口。worker finalize 應透過
此 service 紀錄結果，不直接操作 ScanRunRepository。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.repositories.scan_runs import ScanRunRepository


@dataclass(frozen=True)
class RecordScanRequest:
    """記錄 scan run 所需輸入。"""

    target_id: str
    status: ScanStatus
    item_count: int = 0
    matched_count: int = 0
    error_message: str = ""
    worker_mode: WorkerMode = WorkerMode.HEADLESS
    metadata: dict[str, Any] = field(default_factory=dict)


class ScanRecordingService:
    """協調 scan run repository。"""

    def __init__(self, scan_runs: ScanRunRepository) -> None:
        self.scan_runs = scan_runs

    def record_scan(self, request: RecordScanRequest) -> int:
        """記錄一輪 scan 結果並回傳 row id。"""

        now = utc_now()
        return self.scan_runs.add(
            ScanRun(
                target_id=request.target_id,
                status=request.status,
                started_at=now,
                finished_at=now,
                item_count=request.item_count,
                matched_count=request.matched_count,
                error_message=request.error_message,
                worker_mode=request.worker_mode,
                metadata=request.metadata,
            )
        )


__all__ = ["RecordScanRequest", "ScanRecordingService"]
