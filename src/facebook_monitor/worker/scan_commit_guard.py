"""Finalize 與 coordinator 共用的 scan commit guard primitive。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus


@dataclass(frozen=True)
class ScanCommitGuard:
    """保存本輪 scan admission identity，避免 stop/start 後舊掃描寫回。"""

    worker_id: str
    started_at: datetime
    page_id: str = ""


UNGUARDED_SCAN_COMMIT: ScanCommitGuard | None = None
"""明確標示 debug / one-shot 入口允許不綁定 runtime admission identity。"""


def scan_commit_guard_from_runtime_state(
    state: TargetRuntimeState,
) -> ScanCommitGuard:
    """由 running runtime state 建立本輪 scan commit guard。"""

    if state.last_started_at is None:
        raise ValueError("scan commit guard requires last_started_at")
    return ScanCommitGuard(
        worker_id=state.active_worker_id,
        page_id=state.active_page_id,
        started_at=state.last_started_at,
    )


def begin_scan_commit_transaction(app: ApplicationContext) -> None:
    """開始 guarded scan write transaction，避免 guard check 後被 stop/start 穿插。"""

    connection = app.repositories.runtime_states.connection
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")


def runtime_state_matches_commit_guard(
    runtime_state: TargetRuntimeState,
    commit_guard: ScanCommitGuard | None,
) -> bool:
    """比對 runtime 是否仍是同一個 running attempt。"""

    if commit_guard is None:
        return True
    if runtime_state.runtime_status != TargetRuntimeStatus.RUNNING:
        return False
    if runtime_state.active_worker_id != commit_guard.worker_id:
        return False
    if runtime_state.last_started_at != commit_guard.started_at:
        return False
    return not commit_guard.page_id or runtime_state.active_page_id == commit_guard.page_id


__all__ = [
    "ScanCommitGuard",
    "UNGUARDED_SCAN_COMMIT",
    "begin_scan_commit_transaction",
    "runtime_state_matches_commit_guard",
    "scan_commit_guard_from_runtime_state",
]
