"""Resident attempt cleanup plan runner.

職責：集中 resident queue attempt 的 process-local cleanup obligation，
並保留 queue owner token 與 page id guard。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import TargetQueue


class ResidentAttemptCleanupHost(Protocol):
    """描述 cleanup runner 需要的 executor host 能力。"""

    page_pool: AsyncResidentPagePool
    target_queue: TargetQueue
    schedule_planner: TargetSchedulePlanner

    async def _unregister_active_attempt(self, target_id: str, owner_key: str) -> None:
        """解除 active attempt 登記。"""


@dataclass(frozen=True)
class ResidentAttemptCleanupPlan:
    """保存 resident attempt 結束時必須執行的 guarded cleanup。"""

    target_id: str
    owner_key: str = ""
    page_id: str = ""
    unregister_active_attempt: bool = True
    release_page: bool = True
    complete_queue_item: bool = True
    mark_planner_finished: bool = True

    @classmethod
    def for_attempt(
        cls,
        *,
        target_id: str,
        owner_key: str,
        page_id: str,
    ) -> ResidentAttemptCleanupPlan:
        """依現有 attempt state 欄位建立 cleanup plan。"""

        return cls(target_id=target_id, owner_key=owner_key, page_id=page_id)


async def run_resident_attempt_cleanup(
    host: ResidentAttemptCleanupHost,
    plan: ResidentAttemptCleanupPlan,
) -> None:
    """依現有 finally 順序執行 cleanup，避免改變例外傳播語義。"""

    if plan.unregister_active_attempt:
        await host._unregister_active_attempt(plan.target_id, plan.owner_key)
    if plan.release_page:
        if plan.page_id:
            await host.page_pool.release_if_page_id(plan.target_id, plan.page_id)
        else:
            await host.page_pool.release(plan.target_id)
    if plan.complete_queue_item:
        await host.target_queue.complete(plan.target_id, owner_key=plan.owner_key)
    if plan.mark_planner_finished:
        host.schedule_planner.mark_finished(plan.target_id)


__all__ = [
    "ResidentAttemptCleanupHost",
    "ResidentAttemptCleanupPlan",
    "run_resident_attempt_cleanup",
]
