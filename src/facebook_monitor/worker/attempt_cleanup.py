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
from facebook_monitor.worker.scan_finalize import ScanCommitGuard


class ResidentAttemptCleanupHost(Protocol):
    """描述 cleanup runner 需要的 executor host 能力。"""

    page_pool: AsyncResidentPagePool
    target_queue: TargetQueue
    schedule_planner: TargetSchedulePlanner

    async def _unregister_active_attempt(self, target_id: str, owner_key: str) -> None:
        """解除 active attempt 登記。"""


@dataclass(frozen=True)
class ResidentAttemptResources:
    """保存 attempt 已取得、cleanup 需尊重的 resource tokens。"""

    queue_item_consumed: bool = True
    queue_owner_key: str = ""
    active_attempt_key: str = ""
    page_id: str = ""
    page_acquired: bool = False
    planner_dispatch_id: str = ""
    runtime_owner_guard: ScanCommitGuard | None = None


@dataclass(frozen=True)
class ResidentAttemptCleanupPlan:
    """保存 resident attempt 結束時必須執行的 guarded cleanup。"""

    target_id: str
    owner_key: str = ""
    active_attempt_key: str = ""
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

        return cls(
            target_id=target_id,
            owner_key=owner_key,
            active_attempt_key=owner_key,
            page_id=page_id,
        )

    @classmethod
    def from_resources(
        cls,
        *,
        target_id: str,
        resources: ResidentAttemptResources,
    ) -> ResidentAttemptCleanupPlan:
        """依已取得的 resource tokens 推導 cleanup obligation。"""

        queue_owner_key = resources.queue_owner_key or resources.active_attempt_key
        return cls(
            target_id=target_id,
            owner_key=queue_owner_key,
            active_attempt_key=resources.active_attempt_key,
            page_id=resources.page_id if resources.page_acquired else "",
            unregister_active_attempt=bool(resources.active_attempt_key),
            release_page=resources.page_acquired,
            complete_queue_item=resources.queue_item_consumed,
            mark_planner_finished=bool(resources.planner_dispatch_id),
        )


async def run_resident_attempt_cleanup(
    host: ResidentAttemptCleanupHost,
    plan: ResidentAttemptCleanupPlan,
) -> None:
    """依現有 finally 順序執行 cleanup，避免改變例外傳播語義。"""

    if plan.unregister_active_attempt:
        active_attempt_key = plan.active_attempt_key or plan.owner_key
        await host._unregister_active_attempt(plan.target_id, active_attempt_key)
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
    "ResidentAttemptResources",
    "run_resident_attempt_cleanup",
]
