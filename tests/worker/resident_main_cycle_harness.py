"""Resident main 單 tick 測試 harness。"""

from __future__ import annotations

import asyncio

from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.resident_main import _drain_queue_or_runtime_restart
from facebook_monitor.worker.resident_main import run_resident_main_scheduler_tick
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_executor_types import (
    AsyncCommitReadyScanCallable,
)
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.resident_shared import ResidentCycleSummary
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions


async def run_resident_main_cycle_harness(
    *,
    options: ResidentRuntimeOptions,
    page_pool: AsyncResidentPagePool,
    scan_page: AsyncCommitReadyScanCallable,
    schedule_planner: TargetSchedulePlanner,
    cycle_index: int,
    comments_commit_ready_scan_page: AsyncCommitReadyScanCallable | None = None,
) -> ResidentCycleSummary:
    """以正式 queue/executor 跑單 tick，供 worker tests 驗證狀態機。"""

    target_queue = TargetQueue()
    executor = ExecutorWorkerPool(
        options=options,
        page_pool=page_pool,
        target_queue=target_queue,
        schedule_planner=schedule_planner,
        scan_page=scan_page,
        **(
            {"comments_commit_ready_scan_page": comments_commit_ready_scan_page}
            if comments_commit_ready_scan_page is not None
            else {}
        ),
    )
    await executor.start()
    runtime_restart_requested = False
    try:
        summary = await run_resident_main_scheduler_tick(
            options=options,
            page_pool=page_pool,
            target_queue=target_queue,
            executor=executor,
            schedule_planner=schedule_planner,
            cycle_index=cycle_index,
        )
        runtime_restart_requested = await _drain_queue_or_runtime_restart(
            target_queue=target_queue,
            executor=executor,
        )
        await asyncio.sleep(0)
        counters = await executor.take_counters()
        queued_count, running_count, queued_ids = await target_queue.snapshot()
        worker_health_ok = executor.worker_health_ok()
        runtime_restart_requested = (
            runtime_restart_requested or executor.runtime_restart_requested()
        )
        return ResidentCycleSummary(
            cycle_index=cycle_index,
            selected_count=summary.selected_count,
            success_count=summary.success_count + counters.success_count,
            failure_count=summary.failure_count + counters.failure_count,
            skipped_count=summary.skipped_count + counters.skipped_count,
            opened_page_count=summary.opened_page_count + counters.opened_page_count,
            reused_page_count=summary.reused_page_count + counters.reused_page_count,
            closed_page_count=summary.closed_page_count,
            queued_count=queued_count,
            running_count=running_count,
            queue_length=queued_count,
            queued_target_ids=queued_ids,
            worker_ids=executor.worker_ids,
            worker_statuses=executor.worker_statuses(),
            page_pool_size=await page_pool.size(),
            resident_browser_alive=worker_health_ok and not runtime_restart_requested,
            recovered_runtime_count=summary.recovered_runtime_count,
            metadata_refresh_count=summary.metadata_refresh_count,
            cover_image_refresh_count=summary.cover_image_refresh_count,
            notification_dispatch_count=summary.notification_dispatch_count,
            worker_health_ok=worker_health_ok,
        )
    finally:
        await executor.stop(
            cancel_running=runtime_restart_requested,
            runtime_restart=runtime_restart_requested,
        )
