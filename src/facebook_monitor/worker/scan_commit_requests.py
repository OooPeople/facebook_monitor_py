"""Formal resident scan commit 使用的 request 資料模型。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
from facebook_monitor.worker.scan_commit_guard import ScanCommitGuard


@dataclass(frozen=True)
class FailureScanCommitRequest:
    """保存 guarded failure finalize 需要的完整輸入。"""

    db_path: Path
    target_id: str
    reason: str
    message: str
    source: ScanFailureSource
    worker_path: str
    commit_guard: ScanCommitGuard
    worker_mode: WorkerMode = WorkerMode.HEADLESS
    exception_class: str = ""
    profile_lease_state: str = ""
    page_reused: bool | None = None
    scan_request_id: str = ""
    runtime_error_message: str | None = None


__all__ = ["FailureScanCommitRequest"]
