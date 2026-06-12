"""Formal resident main worker loop。

職責：正式產品主路徑，負責 Playwright persistent context 生命週期與
producer-only scheduler tick 接線；queue、page pool 與 executor worker pool
分別由專門模組承擔。fallback/debug path 不應反向牽動此主路徑。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from collections.abc import Coroutine
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
import sqlite3
from typing import Any
from typing import Protocol

from playwright.async_api import async_playwright

from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions
from facebook_monitor.automation.browser_runtime import launch_persistent_context_async
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.application.maintenance import run_bounded_retention_maintenance_for_db
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import PROFILE_LOCKED_REASON
from facebook_monitor.core.scan_failures import PROFILE_MISSING_REASON
from facebook_monitor.facebook.group_metadata import (
    AsyncBrowserContextLike as GroupMetadataBrowserContextLike,
)
from facebook_monitor.notifications.outbox_service import (
    dispatch_new_pending_notification_outbox_for_db,
)
from facebook_monitor.persistence.sqlite_retry import is_sqlite_lock_error
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.scheduler.runtime_recovery import recover_stale_runtime_targets_detailed
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.posts_pipeline import scan_posts_page_async
import facebook_monitor.worker.resident_maintenance as resident_maintenance
import facebook_monitor.worker.resident_runtime_errors as resident_runtime_errors
from facebook_monitor.worker.resident_shared import ResidentCycleSummary
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.resident_shared import list_active_resident_target_ids
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_executor_types import AsyncScanCallable
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.resident_recovery import ResidentRecoveryCoordinator


logger = logging.getLogger(__name__)
_DISPLAY_NEXT_DUE_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="facebook-monitor-display-next-due",
)
_DISPLAY_NEXT_DUE_BUSY_TIMEOUT_MS = 100
AsyncSleepCallable = Callable[[float], Coroutine[Any, Any, None]]
StopCheckCallable = Callable[[], bool]
AsyncCycleObserver = Callable[[ResidentCycleSummary], None]


class BrowserTimeoutContextLike(Protocol):
    """resident 啟動時設定 Playwright context timeout 需要的能力。"""

    def set_default_timeout(self, timeout: float) -> None:
        """設定 Playwright default timeout。"""

    def set_default_navigation_timeout(self, timeout: float) -> None:
        """設定 Playwright navigation timeout。"""


@dataclass(frozen=True)
class ResidentRuntimeSessionResult:
    """保存單次 browser runtime session 結束原因與 cycle 進度。"""

    cycle_index: int
    runtime_restart_requested: bool


def _install_playwright_shutdown_exception_handler() -> Callable[[], None]:
    """安裝 resident worker 關閉期間用的 Playwright 例外過濾器。"""

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def handle_exception(loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        """只消化 Playwright driver shutdown 的已知背景 future 例外。"""

        if resident_runtime_errors._is_playwright_driver_shutdown_exception(
            context.get("exception")
        ):
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
        raise WorkerFailure(PROFILE_MISSING_REASON, str(options.profile_dir))
    logger.info(
        "resident_main_start db_path=%s profile_dir=%s interval_seconds=%s "
        "scheduler_tick_seconds=%s max_concurrent_scans=%s scan_timeout_seconds=%s "
        "stale_running_after_seconds=%s headed_compat=%s",
        options.db_path,
        options.profile_dir,
        options.interval_seconds,
        options.scheduler_tick_seconds,
        options.max_concurrent_scans,
        options.scan_timeout_seconds,
        options.stale_running_after_seconds,
        options.headed_compat,
    )

    summaries: list[ResidentCycleSummary] = []
    cycle_index = 0
    schedule_planner = TargetSchedulePlanner(
        on_display_next_due_changed=_publish_display_next_due_at(options.db_path)
    )
    stop_requested = should_stop or (lambda: False)
    sleep = sleep_fn or asyncio.sleep

    restore_playwright_exception_handler = _install_playwright_shutdown_exception_handler()
    try:
        try:
            while not stop_requested() and (
                options.max_cycles is None or cycle_index < options.max_cycles
            ):
                session_result = await _run_resident_browser_runtime_session(
                    options=options,
                    scan_page=scan_page,
                    scan_comments_target_page=scan_comments_target_page,
                    schedule_planner=schedule_planner,
                    stop_requested=stop_requested,
                    sleep=sleep,
                    on_cycle=on_cycle,
                    summaries=summaries,
                    cycle_index=cycle_index,
                )
                cycle_index = session_result.cycle_index
                if not session_result.runtime_restart_requested:
                    break
        except ProfileLeaseError as exc:
            raise WorkerFailure(PROFILE_LOCKED_REASON, str(exc)) from exc
    finally:
        restore_playwright_exception_handler()

    return summaries


async def _run_resident_browser_runtime_session(
    *,
    options: ResidentRuntimeOptions,
    scan_page: AsyncScanCallable,
    scan_comments_target_page: AsyncScanCallable | None,
    schedule_planner: TargetSchedulePlanner,
    stop_requested: StopCheckCallable,
    sleep: AsyncSleepCallable,
    on_cycle: AsyncCycleObserver | None,
    summaries: list[ResidentCycleSummary],
    cycle_index: int,
) -> ResidentRuntimeSessionResult:
    """執行單一 Playwright persistent context runtime session。"""

    target_queue = TargetQueue()
    with acquire_profile_lease(options.profile_dir, "resident main worker"):
        async with async_playwright() as playwright:
            browser_context = await launch_persistent_context_async(
                playwright,
                BrowserRuntimeOptions(
                    profile_dir=options.profile_dir,
                    headless=not options.headed_compat,
                    timeout_seconds=_browser_runtime_timeout_seconds(options),
                ),
            )
            try:
                _set_browser_context_timeouts(browser_context, options)
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
                    return await _run_scheduler_ticks_until_restart(
                        options=options,
                        browser_context=browser_context,
                        page_pool=page_pool,
                        target_queue=target_queue,
                        executor=executor,
                        schedule_planner=schedule_planner,
                        stop_requested=stop_requested,
                        sleep=sleep,
                        on_cycle=on_cycle,
                        summaries=summaries,
                        cycle_index=cycle_index,
                    )
                finally:
                    runtime_restart_requested = executor.runtime_restart_requested()
                    await executor.stop(
                        cancel_running=(stop_requested() or runtime_restart_requested),
                        runtime_restart=runtime_restart_requested,
                    )
                    await page_pool.close_all()
            finally:
                await browser_context.close()


async def _run_scheduler_ticks_until_restart(
    *,
    options: ResidentRuntimeOptions,
    browser_context: GroupMetadataBrowserContextLike,
    page_pool: AsyncResidentPagePool,
    target_queue: TargetQueue,
    executor: ExecutorWorkerPool,
    schedule_planner: TargetSchedulePlanner,
    stop_requested: StopCheckCallable,
    sleep: AsyncSleepCallable,
    on_cycle: AsyncCycleObserver | None,
    summaries: list[ResidentCycleSummary],
    cycle_index: int,
) -> ResidentRuntimeSessionResult:
    """在單一 runtime session 中執行 scheduler ticks 直到停止或 restart。"""

    runtime_restart_requested = False
    while not stop_requested() and (
        options.max_cycles is None or cycle_index < options.max_cycles
    ):
        if executor.runtime_restart_requested():
            runtime_restart_requested = True
            break
        cycle_index += 1
        summary = await run_resident_main_scheduler_tick(
            options=options,
            browser_context=browser_context,
            page_pool=page_pool,
            target_queue=target_queue,
            executor=executor,
            schedule_planner=schedule_planner,
            cycle_index=cycle_index,
            should_stop=stop_requested,
        )
        summaries.append(summary)
        if on_cycle:
            on_cycle(summary)
        if executor.runtime_restart_requested():
            runtime_restart_requested = True
            break
        if not summary.worker_health_ok:
            _request_restart_for_unhealthy_workers(executor, summary)
            runtime_restart_requested = True
            break
        if options.max_cycles is not None and cycle_index >= options.max_cycles:
            break
        if await _sleep_or_runtime_restart(
            sleep_fn=sleep,
            seconds=max(options.scheduler_tick_seconds, 0),
            executor=executor,
        ):
            runtime_restart_requested = True
            break
    if not stop_requested() and not runtime_restart_requested:
        runtime_restart_requested = await _drain_queue_or_runtime_restart(
            target_queue=target_queue,
            executor=executor,
        )
    return ResidentRuntimeSessionResult(
        cycle_index=cycle_index,
        runtime_restart_requested=runtime_restart_requested,
    )


def _browser_runtime_timeout_seconds(options: ResidentRuntimeOptions) -> float:
    """回傳 browser runtime timeout，與 scan timeout 下限保持一致。"""

    return max(
        options.scan_timeout_seconds,
        PYTHON_SCHEDULER_RUNTIME_DEFAULTS.min_browser_scan_timeout_seconds,
    )


def _set_browser_context_timeouts(
    browser_context: BrowserTimeoutContextLike,
    options: ResidentRuntimeOptions,
) -> None:
    """設定 Playwright context 的 default timeout 與 navigation timeout。"""

    timeout_ms = _browser_runtime_timeout_seconds(options) * 1000
    browser_context.set_default_timeout(timeout_ms)
    browser_context.set_default_navigation_timeout(timeout_ms)


def _request_restart_for_unhealthy_workers(
    executor: ExecutorWorkerPool,
    summary: ResidentCycleSummary,
) -> None:
    """worker pool unhealthy 時記錄原因並要求 runtime restart。"""

    logger.warning(
        "resident_main_runtime_restart_requested "
        "reason=%s cycle=%s worker_statuses=%s",
        "worker_pool_unhealthy",
        summary.cycle_index,
        ",".join(summary.worker_statuses),
    )
    executor.request_runtime_restart()


def _publish_display_next_due_at(
    db_path: Path,
) -> Callable[[str, datetime | None], None]:
    """建立 scheduler due time 發布器；DB 欄位只供 dashboard 顯示。"""

    def publish(target_id: str, due_at: datetime | None) -> None:
        """將 planner 已決定的 next due 寫入 read model。"""

        future = _DISPLAY_NEXT_DUE_EXECUTOR.submit(
            _write_display_next_due_at_best_effort,
            db_path,
            target_id,
            due_at,
        )
        future.add_done_callback(
            lambda done: _log_display_next_due_update_exception(done, target_id)
        )

    return publish


def _write_display_next_due_at_best_effort(
    db_path: Path,
    target_id: str,
    due_at: datetime | None,
) -> None:
    """以短 timeout 更新 UI-only next due read model；lock 時直接略過。"""

    try:
        with closing(sqlite3.connect(db_path, timeout=0.1)) as connection:
            connection.execute(f"PRAGMA busy_timeout = {_DISPLAY_NEXT_DUE_BUSY_TIMEOUT_MS}")
            connection.execute(
                """
                UPDATE target_runtime_state
                SET display_next_due_at = ?, updated_at = ?
                WHERE target_id = ?
                """,
                (
                    encode_datetime(due_at),
                    encode_datetime(utc_now()),
                    target_id,
                ),
            )
            connection.commit()
    except sqlite3.OperationalError as exc:
        if not is_sqlite_lock_error(exc):
            raise
        logger.warning(
            "display next due update skipped: database locked target_id=%s exception_class=%s",
            target_id,
            exc.__class__.__name__,
        )


def _log_display_next_due_update_exception(
    future: Future[None],
    target_id: str,
) -> None:
    """記錄背景 display-next-due 更新的非 lock 例外。"""

    exc = future.exception()
    if exc is None:
        return
    if is_sqlite_lock_error(exc):
        logger.warning(
            "display next due update skipped: database locked target_id=%s exception_class=%s",
            target_id,
            exc.__class__.__name__,
        )
        return
    logger.error(
        "display next due update failed target_id=%s exception_class=%s",
        target_id,
        exc.__class__.__name__,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


async def run_resident_main_scheduler_tick(
    *,
    options: ResidentRuntimeOptions,
    browser_context: GroupMetadataBrowserContextLike | None = None,
    page_pool: AsyncResidentPagePool,
    target_queue: TargetQueue,
    executor: ExecutorWorkerPool,
    schedule_planner: TargetSchedulePlanner,
    cycle_index: int,
    should_stop: StopCheckCallable | None = None,
) -> ResidentCycleSummary:
    """producer-only scheduler tick：只負責發現 due targets 並 enqueue。"""

    stop_requested = should_stop or (lambda: False)
    recovery_summary = recover_stale_runtime_targets_detailed(
        options.db_path,
        options.stale_running_after_seconds,
    )
    recovery_result = await ResidentRecoveryCoordinator(
        executor=executor,
        page_pool=page_pool,
        target_queue=target_queue,
    ).apply(recovery_summary.running_actions)
    notification_dispatch_count = dispatch_pending_notification_outbox(options)
    run_bounded_retention_maintenance_if_due(options)
    metadata_refresh_count = 0
    if not stop_requested() and not executor.runtime_restart_requested():
        metadata_refresh_count = await resident_maintenance.refresh_requested_target_metadata(
            options=options,
            browser_context=browser_context,
            should_stop=stop_requested,
            request_runtime_restart=executor.request_runtime_restart,
        )
    cover_image_refresh_count = 0
    if not stop_requested() and not executor.runtime_restart_requested():
        cover_image_refresh_count = await resident_maintenance.refresh_pending_target_cover_images(
            options=options,
            browser_context=browser_context,
            should_stop=stop_requested,
            request_runtime_restart=executor.request_runtime_restart,
        )
    active_target_ids = list_active_resident_target_ids(options.db_path)
    closed_page_count = await page_pool.close_inactive(active_target_ids)
    enqueued_count = 0
    if not stop_requested() and not executor.runtime_restart_requested():
        due_targets = schedule_planner.list_due_targets(
            options.db_path,
            default_interval_seconds=options.interval_seconds,
            max_count=None,
        )
        enqueued_count = await executor.enqueue_due_targets(due_targets)
    counters = await executor.take_counters()
    worker_health_ok = executor.worker_health_ok()
    runtime_restart_requested = executor.runtime_restart_requested()
    queued_count, running_count, queued_ids = await target_queue.snapshot()
    summary = ResidentCycleSummary(
        cycle_index=cycle_index,
        selected_count=enqueued_count,
        success_count=counters.success_count,
        failure_count=counters.failure_count,
        skipped_count=counters.skipped_count,
        opened_page_count=counters.opened_page_count,
        reused_page_count=counters.reused_page_count,
        closed_page_count=closed_page_count
        + metadata_refresh_count
        + cover_image_refresh_count
        + recovery_result.discarded_page_count,
        queued_count=queued_count,
        running_count=running_count,
        queue_length=queued_count,
        queued_target_ids=queued_ids,
        worker_ids=executor.worker_ids,
        worker_statuses=executor.worker_statuses(),
        page_pool_size=await page_pool.size(),
        resident_browser_alive=worker_health_ok and not runtime_restart_requested,
        recovered_runtime_count=recovery_summary.recovered_count,
        metadata_refresh_count=metadata_refresh_count,
        cover_image_refresh_count=cover_image_refresh_count,
        notification_dispatch_count=notification_dispatch_count,
        worker_health_ok=worker_health_ok,
    )
    _log_resident_scheduler_tick_summary(summary, options=options)
    return summary


def _log_resident_scheduler_tick_summary(
    summary: ResidentCycleSummary,
    *,
    options: ResidentRuntimeOptions,
) -> None:
    """記錄 resident scheduler tick 的 queue/worker 診斷摘要。"""

    if not _should_log_resident_scheduler_tick_summary(summary):
        return
    logger.info(
        "resident_scheduler_tick cycle=%s selected=%s success=%s failure=%s "
        "skipped=%s running=%s queued=%s queue_length=%s queued_target_ids=%s "
        "max_concurrent_scans=%s worker_ids=%s worker_statuses=%s "
        "opened_pages=%s reused_pages=%s "
        "closed_pages=%s page_pool_size=%s recovered_runtime=%s metadata_refresh=%s "
        "cover_image_refresh=%s notification_dispatch=%s browser_alive=%s "
        "worker_health_ok=%s",
        summary.cycle_index,
        summary.selected_count,
        summary.success_count,
        summary.failure_count,
        summary.skipped_count,
        summary.running_count,
        summary.queued_count,
        summary.queue_length,
        ",".join(summary.queued_target_ids),
        options.max_concurrent_scans,
        ",".join(summary.worker_ids),
        ",".join(summary.worker_statuses),
        summary.opened_page_count,
        summary.reused_page_count,
        summary.closed_page_count,
        summary.page_pool_size,
        summary.recovered_runtime_count,
        summary.metadata_refresh_count,
        summary.cover_image_refresh_count,
        summary.notification_dispatch_count,
        summary.resident_browser_alive,
        summary.worker_health_ok,
    )


def _should_log_resident_scheduler_tick_summary(summary: ResidentCycleSummary) -> bool:
    """只在本輪有排程活動或可診斷狀態時輸出 INFO log。"""

    return any(
        (
            summary.selected_count,
            summary.success_count,
            summary.failure_count,
            summary.skipped_count,
            summary.running_count,
            summary.queued_count,
            summary.recovered_runtime_count,
            summary.metadata_refresh_count,
            summary.cover_image_refresh_count,
            summary.notification_dispatch_count,
            not summary.worker_health_ok,
            not summary.resident_browser_alive,
        )
    )


def dispatch_pending_notification_outbox(options: ResidentRuntimeOptions) -> int:
    """每輪 tick drain 已存在的 pending outbox，避免 after-commit hook 漏跑後卡住。"""

    try:
        return dispatch_new_pending_notification_outbox_for_db(db_path=options.db_path)
    except sqlite3.OperationalError as exc:
        if _is_sqlite_database_locked(exc):
            logger.warning("pending notification outbox dispatch skipped: database locked")
            return 0
        logger.exception("pending notification outbox dispatch failed")
        return 0


def run_bounded_retention_maintenance_if_due(options: ResidentRuntimeOptions) -> int:
    """週期性清理 bounded retention horizon 外的內部資料。"""

    return run_bounded_retention_maintenance_for_db(options.db_path)


def _is_sqlite_database_locked(exc: sqlite3.OperationalError) -> bool:
    """判斷 SQLite OperationalError 是否為暫時性 lock contention。"""

    return is_sqlite_lock_error(exc)


async def _sleep_or_runtime_restart(
    *,
    sleep_fn: AsyncSleepCallable,
    seconds: float,
    executor: ExecutorWorkerPool,
) -> bool:
    """等待下一輪排程或 runtime restart request，先到者為準。"""

    if executor.runtime_restart_requested():
        return True
    sleep_task = asyncio.create_task(sleep_fn(max(seconds, 0)))
    restart_task = asyncio.create_task(executor.wait_runtime_restart_requested())
    try:
        done, pending = await asyncio.wait(
            {sleep_task, restart_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if sleep_task in done:
            await sleep_task
        if restart_task in done:
            await restart_task
        return restart_task in done or executor.runtime_restart_requested()
    finally:
        for task in (sleep_task, restart_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(sleep_task, restart_task, return_exceptions=True)


async def _drain_queue_or_runtime_restart(
    *,
    target_queue: TargetQueue,
    executor: ExecutorWorkerPool,
) -> bool:
    """等待 queue drain；若 runtime restart 先發生則交給外層重建。"""

    if executor.runtime_restart_requested():
        return True
    join_task = asyncio.create_task(target_queue.join())
    restart_task = asyncio.create_task(executor.wait_runtime_restart_requested())
    try:
        done, _pending = await asyncio.wait(
            {join_task, restart_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if join_task in done:
            await join_task
        if restart_task in done:
            await restart_task
            return True
        return executor.runtime_restart_requested()
    finally:
        for task in (join_task, restart_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(join_task, restart_task, return_exceptions=True)


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
        **(
            {"scan_comments_target_page": scan_comments_target_page}
            if scan_comments_target_page is not None
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
