"""Formal resident main worker loop。

職責：正式產品主路徑，負責 Playwright persistent context 生命週期與
producer-only scheduler tick 接線；queue、page pool 與 executor worker pool
分別由專門模組承擔。fallback/debug parity 不應反向牽動此主路徑。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
import logging
from typing import Any

from playwright.async_api import async_playwright

from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions
from facebook_monitor.automation.browser_runtime import launch_persistent_context_async
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.scheduler.runtime_recovery import recover_stale_runtime_targets
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import is_generated_group_comments_name
from facebook_monitor.core.models import is_generated_group_posts_name
from facebook_monitor.facebook.group_metadata import GroupMetadataError
from facebook_monitor.facebook.group_metadata import resolve_group_name_with_context
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.posts_pipeline import scan_posts_page_async
from facebook_monitor.worker.resident_shared import ResidentCycleSummary
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.resident_shared import list_active_resident_target_ids
from facebook_monitor.worker.resident_main_executor import AsyncScanCallable
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import TargetQueue


logger = logging.getLogger(__name__)
AsyncSleepCallable = Callable[[float], Awaitable[None]]
StopCheckCallable = Callable[[], bool]
AsyncCycleObserver = Callable[[ResidentCycleSummary], None]


def _is_playwright_driver_shutdown_exception(exc: object) -> bool:
    """判斷是否為 Playwright driver 關閉期間產生的已知背景 future 例外。"""

    return (
        isinstance(exc, Exception)
        and "Connection closed while reading from the driver" in str(exc)
    )


def _install_playwright_shutdown_exception_handler() -> Callable[[], None]:
    """安裝 resident worker 關閉期間用的 Playwright 例外過濾器。"""

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def handle_exception(
        loop: asyncio.AbstractEventLoop, context: dict[str, object]
    ) -> None:
        """只消化 Playwright driver shutdown 的已知背景 future 例外。"""

        if _is_playwright_driver_shutdown_exception(context.get("exception")):
            return
        if previous_handler is not None:
            previous_handler(loop, context)
            return
        loop.default_exception_handler(context)

    loop.set_exception_handler(handle_exception)

    def restore_handler() -> None:
        """還原呼叫端原本的 event loop exception handler。"""

        loop.set_exception_handler(previous_handler)

    return restore_handler


async def run_resident_main_loop(
    options: ResidentRuntimeOptions,
    *,
    scan_page: AsyncScanCallable = scan_posts_page_async,
    scan_comments_target_page: AsyncScanCallable | None = None,
    sleep_fn: AsyncSleepCallable | None = None,
    should_stop: StopCheckCallable | None = None,
    on_cycle: AsyncCycleObserver | None = None,
) -> list[ResidentCycleSummary]:
    """執行 queue-based continuous resident main worker loop。"""

    if not options.profile_dir.exists():
        raise WorkerFailure("profile_missing", str(options.profile_dir))

    summaries: list[ResidentCycleSummary] = []
    cycle_index = 0
    schedule_planner = TargetSchedulePlanner()
    target_queue = TargetQueue()
    stop_requested = should_stop or (lambda: False)
    sleep = sleep_fn or asyncio.sleep

    restore_playwright_exception_handler = (
        _install_playwright_shutdown_exception_handler()
    )
    try:
        try:
            with acquire_profile_lease(options.profile_dir, "resident main worker"):
                async with async_playwright() as playwright:
                    browser_context = await launch_persistent_context_async(
                        playwright,
                        BrowserRuntimeOptions(
                            profile_dir=options.profile_dir,
                            headless=not options.headed_compat,
                            timeout_seconds=max(options.scan_timeout_seconds, 10),
                        ),
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
                                {"scan_comments_target_page": scan_comments_target_page}
                                if scan_comments_target_page is not None
                                else {}
                            ),
                        )
                        await executor.start()
                        try:
                            while (
                                not stop_requested()
                                and (
                                    options.max_cycles is None
                                    or cycle_index < options.max_cycles
                                )
                            ):
                                cycle_index += 1
                                summary = await run_resident_main_scheduler_tick(
                                    options=options,
                                    browser_context=browser_context,
                                    page_pool=page_pool,
                                    target_queue=target_queue,
                                    executor=executor,
                                    schedule_planner=schedule_planner,
                                    cycle_index=cycle_index,
                                )
                                summaries.append(summary)
                                if on_cycle:
                                    on_cycle(summary)
                                if (
                                    options.max_cycles is not None
                                    and cycle_index >= options.max_cycles
                                ):
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
    finally:
        restore_playwright_exception_handler()

    return summaries


async def run_resident_main_scheduler_tick(
    *,
    options: ResidentRuntimeOptions,
    browser_context: Any | None = None,
    page_pool: AsyncResidentPagePool,
    target_queue: TargetQueue,
    executor: ExecutorWorkerPool,
    schedule_planner: TargetSchedulePlanner,
    cycle_index: int,
) -> ResidentCycleSummary:
    """producer-only scheduler tick：只負責發現 due targets 並 enqueue。"""

    recover_stale_runtime_targets(options.db_path, options.stale_running_after_seconds)
    metadata_refresh_count = await refresh_requested_target_metadata(
        options=options,
        browser_context=browser_context,
    )
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
        closed_page_count=closed_page_count + metadata_refresh_count,
        queued_count=queued_count,
        running_count=running_count,
        queue_length=queued_count,
        queued_target_ids=queued_ids,
        worker_ids=executor.worker_ids,
        page_pool_size=await page_pool.size(),
        resident_browser_alive=True,
    )


async def refresh_requested_target_metadata(
    *,
    options: ResidentRuntimeOptions,
    browser_context: Any | None,
) -> int:
    """消化 Web UI 丟給 resident scheduler 的 target metadata refresh request。"""

    if options.metadata_refresh_provider is None or browser_context is None:
        return 0
    refreshed_count = 0
    for target_id in options.metadata_refresh_provider():
        try:
            if await refresh_target_group_name_from_context(
                options=options,
                browser_context=browser_context,
                target_id=target_id,
            ):
                refreshed_count += 1
        except Exception:
            logger.exception(
                "metadata refresh failed",
                extra={"target_id": target_id},
            )
    return refreshed_count


async def refresh_target_group_name_from_context(
    *,
    options: ResidentRuntimeOptions,
    browser_context: Any,
    target_id: str,
) -> bool:
    """用 resident browser context 補齊 target group name。"""

    group_id = ""
    should_refresh = False
    with SqliteApplicationContext(options.db_path) as app:
        target = app.repositories.targets.get(target_id)
        if target is None:
            return False
        group_id = target.group_id
        should_refresh = (
            target.target_kind == TargetKind.POSTS
            and is_generated_group_posts_name(target.name, target.group_id)
        ) or (
            target.target_kind == TargetKind.COMMENTS
            and is_generated_group_comments_name(
                target.name,
                target.group_id,
                target.parent_post_id,
            )
        )
        if target.group_name and not should_refresh:
            return False
    if not group_id:
        return False
    try:
        group_name = await resolve_group_name_with_context(
            browser_context,
            canonical_url=f"https://www.facebook.com/groups/{group_id}",
        )
    except GroupMetadataError:
        logger.info(
            "metadata refresh skipped",
            extra={"target_id": target_id},
        )
        return False
    with SqliteApplicationContext(options.db_path) as app:
        if app.repositories.targets.get(target_id) is None:
            return False
        app.services.targets.refresh_target_group_name(target_id, group_name)
    return True


async def run_resident_main_cycle(
    *,
    options: ResidentRuntimeOptions,
    page_pool: AsyncResidentPagePool,
    scan_page: AsyncScanCallable,
    schedule_planner: TargetSchedulePlanner,
    cycle_index: int,
    scan_comments_target_page: AsyncScanCallable | None = None,
) -> ResidentCycleSummary:
    """測試/相容用單 tick：用新 queue/executor 模型完成一次 due target drain。"""

    target_queue = TargetQueue()
    executor = ExecutorWorkerPool(
        options=options,
        page_pool=page_pool,
        target_queue=target_queue,
        schedule_planner=schedule_planner,
        scan_page=scan_page,
        **({"scan_comments_target_page": scan_comments_target_page} if scan_comments_target_page is not None else {}),
    )
    await executor.start()
    try:
        summary = await run_resident_main_scheduler_tick(
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


def run_resident_main_loop_sync(
    options: ResidentRuntimeOptions,
    *,
    should_stop: StopCheckCallable | None = None,
    on_cycle: AsyncCycleObserver | None = None,
    sleep_fn: Callable[[float], object] | None = None,
) -> list[ResidentCycleSummary]:
    """同步包裝 resident main worker，供 CLI / Web UI background thread 呼叫。"""

    async def run_with_shutdown_handler() -> list[ResidentCycleSummary]:
        """讓 Playwright shutdown handler 維持到 asyncio.run 關閉 event loop。"""

        _install_playwright_shutdown_exception_handler()
        return await run_resident_main_loop(
            options,
            should_stop=should_stop,
            on_cycle=on_cycle,
            sleep_fn=selected_sleep,
        )

    async def selected_sleep(seconds: float) -> None:
        """橋接既有同步 wake-aware sleep_fn 到 async worker。"""

        if sleep_fn is None:
            await asyncio.sleep(seconds)
            return
        await asyncio.to_thread(sleep_fn, seconds)

    return asyncio.run(run_with_shutdown_handler())
