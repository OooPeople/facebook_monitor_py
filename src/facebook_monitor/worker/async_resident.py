"""Async resident group posts loop。

職責：負責 Playwright persistent context 生命週期與 producer-only scheduler tick
接線；queue、page pool 與 executor worker pool 分別由專門模組承擔。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable

from playwright.async_api import async_playwright

from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.scheduler.loop import recover_stale_running_targets
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.group_posts import WorkerFailure
from facebook_monitor.worker.group_posts import scan_group_posts_page_async
from facebook_monitor.worker.resident import ResidentCycleSummary
from facebook_monitor.worker.resident import ResidentWorkerOptions
from facebook_monitor.worker.resident import list_active_resident_target_ids
from facebook_monitor.worker.resident_executor import AsyncScanCallable
from facebook_monitor.worker.resident_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_queue import TargetQueue


AsyncSleepCallable = Callable[[float], Awaitable[None]]
StopCheckCallable = Callable[[], bool]
AsyncCycleObserver = Callable[[ResidentCycleSummary], None]


async def run_async_resident_worker_loop(
    options: ResidentWorkerOptions,
    *,
    scan_page: AsyncScanCallable = scan_group_posts_page_async,
    scan_comments_page: AsyncScanCallable | None = None,
    sleep_fn: AsyncSleepCallable | None = None,
    should_stop: StopCheckCallable | None = None,
    on_cycle: AsyncCycleObserver | None = None,
) -> list[ResidentCycleSummary]:
    """執行 queue-based continuous async resident worker loop。"""

    if not options.profile_dir.exists():
        raise WorkerFailure("profile_missing", str(options.profile_dir))

    summaries: list[ResidentCycleSummary] = []
    cycle_index = 0
    schedule_planner = TargetSchedulePlanner()
    target_queue = TargetQueue()
    stop_requested = should_stop or (lambda: False)
    sleep = sleep_fn or asyncio.sleep

    try:
        with acquire_profile_lease(options.profile_dir, "async resident worker"):
            async with async_playwright() as playwright:
                browser_context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=str(options.profile_dir),
                    headless=not options.headed_compat,
                    viewport={"width": 1366, "height": 900},
                    timeout=max(options.scan_timeout_seconds, 10) * 1000,
                )
                try:
                    browser_context.set_default_timeout(
                        max(options.scan_timeout_seconds, 10) * 1000
                    )
                    browser_context.set_default_navigation_timeout(
                        max(options.scan_timeout_seconds, 10) * 1000
                    )
                    page_pool = AsyncResidentPagePool(browser_context)
                    executor = ExecutorWorkerPool(
                        options=options,
                        page_pool=page_pool,
                        target_queue=target_queue,
                        schedule_planner=schedule_planner,
                        scan_page=scan_page,
                        **(
                            {"scan_comments_page": scan_comments_page}
                            if scan_comments_page is not None
                            else {}
                        ),
                    )
                    await executor.start()
                    try:
                        while (
                            not stop_requested()
                            and (options.max_cycles is None or cycle_index < options.max_cycles)
                        ):
                            cycle_index += 1
                            summary = await run_async_resident_scheduler_tick(
                                options=options,
                                page_pool=page_pool,
                                target_queue=target_queue,
                                executor=executor,
                                schedule_planner=schedule_planner,
                                cycle_index=cycle_index,
                            )
                            summaries.append(summary)
                            if on_cycle:
                                on_cycle(summary)
                            if options.max_cycles is not None and cycle_index >= options.max_cycles:
                                break
                            await sleep(max(options.scheduler_tick_seconds, 0))
                        await target_queue.join()
                    finally:
                        await executor.stop()
                        await page_pool.close_all()
                finally:
                    await browser_context.close()
    except ProfileLeaseError as exc:
        raise WorkerFailure("profile_locked", str(exc)) from exc

    return summaries


async def run_async_resident_scheduler_tick(
    *,
    options: ResidentWorkerOptions,
    page_pool: AsyncResidentPagePool,
    target_queue: TargetQueue,
    executor: ExecutorWorkerPool,
    schedule_planner: TargetSchedulePlanner,
    cycle_index: int,
) -> ResidentCycleSummary:
    """producer-only scheduler tick：只負責發現 due targets 並 enqueue。"""

    recover_stale_running_targets(options.db_path, options.stale_running_after_seconds)
    active_target_ids = list_active_resident_target_ids(options.db_path)
    closed_page_count = await page_pool.close_inactive(active_target_ids)
    due_targets = schedule_planner.list_due_targets(
        options.db_path,
        default_interval_seconds=options.interval_seconds,
        max_count=None,
    )
    enqueued_count = await executor.enqueue_due_targets(due_targets)
    counters = await executor.take_counters()
    queued_count, running_count, queued_ids = await target_queue.snapshot()
    return ResidentCycleSummary(
        cycle_index=cycle_index,
        selected_count=enqueued_count,
        success_count=counters.success_count,
        failure_count=counters.failure_count,
        skipped_count=counters.skipped_count,
        opened_page_count=counters.opened_page_count,
        reused_page_count=counters.reused_page_count,
        closed_page_count=closed_page_count,
        queued_count=queued_count,
        running_count=running_count,
        queue_length=queued_count,
        queued_target_ids=queued_ids,
        worker_ids=executor.worker_ids,
        page_pool_size=await page_pool.size(),
        resident_browser_alive=True,
    )


async def run_async_resident_worker_cycle(
    *,
    options: ResidentWorkerOptions,
    page_pool: AsyncResidentPagePool,
    scan_page: AsyncScanCallable,
    schedule_planner: TargetSchedulePlanner,
    cycle_index: int,
    scan_comments_page: AsyncScanCallable | None = None,
) -> ResidentCycleSummary:
    """測試/相容用單 tick：用新 queue/executor 模型完成一次 due target drain。"""

    target_queue = TargetQueue()
    executor = ExecutorWorkerPool(
        options=options,
        page_pool=page_pool,
        target_queue=target_queue,
        schedule_planner=schedule_planner,
        scan_page=scan_page,
        **({"scan_comments_page": scan_comments_page} if scan_comments_page is not None else {}),
    )
    await executor.start()
    try:
        summary = await run_async_resident_scheduler_tick(
            options=options,
            page_pool=page_pool,
            target_queue=target_queue,
            executor=executor,
            schedule_planner=schedule_planner,
            cycle_index=cycle_index,
        )
        await target_queue.join()
        counters = await executor.take_counters()
        queued_count, running_count, queued_ids = await target_queue.snapshot()
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
            page_pool_size=await page_pool.size(),
            resident_browser_alive=True,
        )
    finally:
        await executor.stop()


def run_async_resident_worker_loop_sync(
    options: ResidentWorkerOptions,
    *,
    should_stop: StopCheckCallable | None = None,
    on_cycle: AsyncCycleObserver | None = None,
    sleep_fn: Callable[[float], object] | None = None,
) -> list[ResidentCycleSummary]:
    """同步包裝 async resident worker，供 CLI / Web UI background thread 呼叫。"""

    async def selected_sleep(seconds: float) -> None:
        """橋接既有同步 wake-aware sleep_fn 到 async worker。"""

        if sleep_fn is None:
            await asyncio.sleep(seconds)
            return
        await asyncio.to_thread(sleep_fn, seconds)

    return asyncio.run(
        run_async_resident_worker_loop(
            options,
            should_stop=should_stop,
            on_cycle=on_cycle,
            sleep_fn=selected_sleep,
        )
    )
