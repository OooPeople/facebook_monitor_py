"""Resident runtime recovery coordinator.

職責：把 DB runtime recovery action 套用到 resident process 內的 scan task、
queue ownership 與 page pool，讓 target page restart 不只停留在資料庫狀態。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.scheduler.runtime_recovery import RunningRecoveryAction
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import TargetQueue


@dataclass(frozen=True)
class ResidentRecoveryResult:
    """保存 resident process 已套用的 recovery side effects。"""

    cancelled_scan_count: int = 0
    discarded_page_count: int = 0
    released_queue_owner_count: int = 0


class ResidentRecoveryCoordinator:
    """協調 resident in-memory state 的 target page restart。"""

    def __init__(
        self,
        *,
        executor: ExecutorWorkerPool,
        page_pool: AsyncResidentPagePool,
        target_queue: TargetQueue,
    ) -> None:
        self.executor = executor
        self.page_pool = page_pool
        self.target_queue = target_queue

    async def apply(
        self,
        actions: tuple[RunningRecoveryAction, ...],
    ) -> ResidentRecoveryResult:
        """套用 stale running recovery actions，讓下一輪掃描能使用新 page。"""

        cancelled_scan_count = 0
        discarded_page_count = 0
        released_queue_owner_count = 0
        for action in actions:
            if await self.executor.cancel_active_attempt_if_owner(action):
                cancelled_scan_count += 1
            if action.page_id and await self.page_pool.discard_if_page_id(
                action.target_id,
                action.page_id,
            ):
                discarded_page_count += 1
            if await self.target_queue.release_running_if_owner(
                action.target_id,
                action.owner_key,
            ):
                released_queue_owner_count += 1
        return ResidentRecoveryResult(
            cancelled_scan_count=cancelled_scan_count,
            discarded_page_count=discarded_page_count,
            released_queue_owner_count=released_queue_owner_count,
        )
