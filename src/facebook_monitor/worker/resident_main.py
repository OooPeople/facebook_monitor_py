"""Formal resident main worker loop。

職責：正式產品主路徑，負責 Playwright persistent context 生命週期與
producer-only scheduler tick 接線；queue、page pool 與 executor worker pool
分別由專門模組承擔。fallback/debug path 不應反向牽動此主路徑。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from collections.abc import Coroutine
from datetime import datetime
import logging
from pathlib import Path
import sqlite3
from typing import Any

from playwright.async_api import async_playwright

from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions
from facebook_monitor.automation.browser_runtime import launch_persistent_context_async
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.defaults import PYTHON_PERSISTENCE_RETENTION_DEFAULTS
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import TargetCoverImageRefreshState
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import PROFILE_LOCKED_REASON
from facebook_monitor.core.scan_failures import PROFILE_MISSING_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.facebook.group_metadata import GroupMetadataError
from facebook_monitor.facebook.group_metadata import resolve_group_cover_image_with_context
from facebook_monitor.facebook.group_metadata import resolve_group_metadata_with_context
from facebook_monitor.notifications.outbox_service import (
    dispatch_new_pending_notification_outbox_for_db,
)
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.scheduler.runtime_recovery import recover_stale_runtime_targets_detailed
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.errors import classify_playwright_exception
from facebook_monitor.worker.posts_pipeline import scan_posts_page_async
from facebook_monitor.worker.scan_failure_finalize import record_guarded_scan_failure_for_db
from facebook_monitor.worker.resident_shared import ResidentCycleSummary
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.resident_shared import list_active_resident_target_ids
from facebook_monitor.worker.resident_main_executor import AsyncScanCallable
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.resident_recovery import ResidentRecoveryCoordinator


logger = logging.getLogger(__name__)
_LAST_RETENTION_MAINTENANCE_BY_DB: dict[Path, datetime] = {}
AsyncSleepCallable = Callable[[float], Coroutine[Any, Any, None]]
StopCheckCallable = Callable[[], bool]
AsyncCycleObserver = Callable[[ResidentCycleSummary], None]
METADATA_REFRESH_TARGET_LIMIT_PER_TICK = (
    PYTHON_SCHEDULER_RUNTIME_DEFAULTS.metadata_refresh_target_limit_per_tick
)
COVER_IMAGE_REFRESH_TARGET_LIMIT_PER_TICK = (
    PYTHON_SCHEDULER_RUNTIME_DEFAULTS.cover_image_refresh_target_limit_per_tick
)


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

    restore_playwright_exception_handler = (
        _install_playwright_shutdown_exception_handler()
    )
    try:
        try:
            while (
                not stop_requested()
                and (options.max_cycles is None or cycle_index < options.max_cycles)
            ):
                runtime_restart_requested = False
                target_queue = TargetQueue()
                with acquire_profile_lease(options.profile_dir, "resident main worker"):
                    async with async_playwright() as playwright:
                        browser_context = await launch_persistent_context_async(
                            playwright,
                            BrowserRuntimeOptions(
                                profile_dir=options.profile_dir,
                                headless=not options.headed_compat,
                                timeout_seconds=max(
                                    options.scan_timeout_seconds,
                                    PYTHON_SCHEDULER_RUNTIME_DEFAULTS.min_browser_scan_timeout_seconds,
                                ),
                            ),
                        )
                        try:
                            browser_context.set_default_timeout(
                                max(
                                    options.scan_timeout_seconds,
                                    PYTHON_SCHEDULER_RUNTIME_DEFAULTS.min_browser_scan_timeout_seconds,
                                )
                                * 1000
                            )
                            browser_context.set_default_navigation_timeout(
                                max(
                                    options.scan_timeout_seconds,
                                    PYTHON_SCHEDULER_RUNTIME_DEFAULTS.min_browser_scan_timeout_seconds,
                                )
                                * 1000
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
                                        logger.warning(
                                            "resident_main_runtime_restart_requested "
                                            "reason=%s cycle=%s worker_statuses=%s",
                                            "worker_pool_unhealthy",
                                            summary.cycle_index,
                                            ",".join(summary.worker_statuses),
                                        )
                                        executor.request_runtime_restart()
                                        runtime_restart_requested = True
                                        break
                                    if (
                                        options.max_cycles is not None
                                        and cycle_index >= options.max_cycles
                                    ):
                                        break
                                    if await _sleep_or_runtime_restart(
                                        sleep_fn=sleep,
                                        seconds=max(options.scheduler_tick_seconds, 0),
                                        executor=executor,
                                    ):
                                        runtime_restart_requested = True
                                        break
                                if not stop_requested() and not runtime_restart_requested:
                                    runtime_restart_requested = (
                                        await _drain_queue_or_runtime_restart(
                                            target_queue=target_queue,
                                            executor=executor,
                                        )
                                    )
                            finally:
                                await executor.stop(
                                    cancel_running=(
                                        stop_requested() or runtime_restart_requested
                                    ),
                                    runtime_restart=runtime_restart_requested,
                                )
                                await page_pool.close_all()
                        finally:
                            await browser_context.close()
                if not runtime_restart_requested:
                    break
        except ProfileLeaseError as exc:
            raise WorkerFailure(PROFILE_LOCKED_REASON, str(exc)) from exc
    finally:
        restore_playwright_exception_handler()

    return summaries


def _publish_display_next_due_at(
    db_path: Path,
) -> Callable[[str, datetime | None], None]:
    """建立 scheduler due time 發布器；DB 欄位只供 dashboard 顯示。"""

    def publish(target_id: str, due_at: datetime | None) -> None:
        """將 planner 已決定的 next due 寫入 read model。"""

        with SqliteApplicationContext(db_path) as app:
            app.services.targets.set_target_display_next_due_at(target_id, due_at)

    return publish


async def run_resident_main_scheduler_tick(
    *,
    options: ResidentRuntimeOptions,
    browser_context: Any | None = None,
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
        metadata_refresh_count = await refresh_requested_target_metadata(
            options=options,
            browser_context=browser_context,
            should_stop=stop_requested,
            request_runtime_restart=executor.request_runtime_restart,
        )
    cover_image_refresh_count = 0
    if not stop_requested() and not executor.runtime_restart_requested():
        cover_image_refresh_count = await refresh_pending_target_cover_images(
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
            logger.warning(
                "pending notification outbox dispatch skipped: database locked"
            )
            return 0
        logger.exception("pending notification outbox dispatch failed")
        return 0


def run_bounded_retention_maintenance_if_due(options: ResidentRuntimeOptions) -> int:
    """週期性清理 bounded retention horizon 外的內部資料。"""

    now = utc_now()
    last_run = _LAST_RETENTION_MAINTENANCE_BY_DB.get(options.db_path)
    if (
        last_run is not None
        and (now - last_run).total_seconds()
        < PYTHON_PERSISTENCE_RETENTION_DEFAULTS.maintenance_interval_seconds
    ):
        return 0
    try:
        with SqliteApplicationContext(options.db_path) as app:
            result = app.repositories.maintenance.prune_bounded_retention(now=now)
        _LAST_RETENTION_MAINTENANCE_BY_DB[options.db_path] = now
        return result.total_deleted
    except sqlite3.OperationalError as exc:
        if _is_sqlite_database_locked(exc):
            logger.warning("bounded retention maintenance skipped: database locked")
            return 0
        logger.exception("bounded retention maintenance failed")
        return 0
    except Exception:
        logger.exception("bounded retention maintenance failed")
        return 0


def _is_sqlite_database_locked(exc: sqlite3.OperationalError) -> bool:
    """判斷 SQLite OperationalError 是否為暫時性 lock contention。"""

    return "locked" in str(exc).lower()


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


async def refresh_requested_target_metadata(
    *,
    options: ResidentRuntimeOptions,
    browser_context: Any | None,
    should_stop: StopCheckCallable | None = None,
    request_runtime_restart: Callable[[], None] | None = None,
) -> int:
    """消化 Web UI request 與 DB pending metadata refresh job。"""

    stop_requested = should_stop or (lambda: False)
    if browser_context is None or stop_requested():
        return 0
    refreshed_count = 0
    for target_id in list_metadata_refresh_target_ids(options):
        if stop_requested():
            break
        try:
            if await refresh_target_group_name_from_context(
                options=options,
                browser_context=browser_context,
                target_id=target_id,
            ):
                refreshed_count += 1
        except Exception as exc:
            if _should_skip_refresh_failure_for_shutdown(exc, stop_requested):
                logger.info(
                    "metadata refresh skipped because scheduler is stopping",
                    extra={"target_id": target_id},
                )
                break
            if _is_scheduler_runtime_refresh_failure(exc):
                logger.warning(
                    "metadata refresh requested browser runtime restart",
                    extra={"target_id": target_id},
                )
                recorded_failure = record_refresh_runtime_failure(
                    options=options,
                    target_id=target_id,
                    exc=exc,
                )
                if recorded_failure and request_runtime_restart is not None:
                    request_runtime_restart()
                break
            logger.exception(
                "metadata refresh failed",
                extra={"target_id": target_id},
            )
            mark_target_metadata_refresh_failed(
                options,
                target_id,
                "metadata refresh failed",
            )
    return refreshed_count


def list_metadata_refresh_target_ids(options: ResidentRuntimeOptions) -> tuple[str, ...]:
    """列出本輪要消化的明確 metadata refresh target ids。"""

    target_ids: list[str] = []
    if options.metadata_refresh_provider is not None:
        target_ids.extend(options.metadata_refresh_provider())
    with SqliteApplicationContext(options.db_path) as app:
        target_ids.extend(
            target.id
            for target in app.repositories.targets.list_by_metadata_status(
                TargetMetadataStatus.PENDING,
                limit=METADATA_REFRESH_TARGET_LIMIT_PER_TICK,
            )
        )
    return filter_maintenance_refresh_target_ids(
        options,
        tuple(dict.fromkeys(target_id for target_id in target_ids if target_id)),
    )


async def refresh_pending_target_cover_images(
    *,
    options: ResidentRuntimeOptions,
    browser_context: Any | None,
    should_stop: StopCheckCallable | None = None,
    request_runtime_restart: Callable[[], None] | None = None,
) -> int:
    """消化 dashboard 壞圖上報排入的 image-only cover refresh jobs。"""

    stop_requested = should_stop or (lambda: False)
    if browser_context is None or stop_requested():
        return 0
    refreshed_count = 0
    with SqliteApplicationContext(options.db_path) as app:
        states = app.services.targets.list_pending_cover_image_refreshes(
            limit=COVER_IMAGE_REFRESH_TARGET_LIMIT_PER_TICK,
        )
    states = filter_maintenance_cover_refresh_states(options, states)
    for state in states:
        if stop_requested():
            break
        try:
            if await refresh_target_group_cover_image_from_context(
                options=options,
                browser_context=browser_context,
                state=state,
            ):
                refreshed_count += 1
        except Exception as exc:
            if _should_skip_refresh_failure_for_shutdown(exc, stop_requested):
                logger.info(
                    "cover image refresh skipped because scheduler is stopping",
                    extra={"target_id": state.target_id},
                )
                break
            if _is_scheduler_runtime_refresh_failure(exc):
                logger.warning(
                    "cover image refresh requested browser runtime restart",
                    extra={"target_id": state.target_id},
                )
                recorded_failure = record_refresh_runtime_failure(
                    options=options,
                    target_id=state.target_id,
                    exc=exc,
                )
                if recorded_failure and request_runtime_restart is not None:
                    request_runtime_restart()
                break
            logger.exception(
                "cover image refresh failed",
                extra={"target_id": state.target_id},
            )
            mark_target_cover_image_refresh_failed(
                options,
                state.target_id,
                _format_exception_message(exc),
                reported_url=state.last_reported_url,
                requested_at=state.requested_at,
            )
    return refreshed_count


def filter_maintenance_refresh_target_ids(
    options: ResidentRuntimeOptions,
    target_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """避開已有正式掃描工作的 target，避免 maintenance job 擋住 retry。"""

    if not target_ids:
        return ()
    with SqliteApplicationContext(options.db_path) as app:
        runtime_states = app.repositories.runtime_states.list_by_targets(list(target_ids))
        targets = {
            target_id: app.repositories.targets.get(target_id)
            for target_id in target_ids
        }
    return tuple(
        target_id
        for target_id in target_ids
        if targets.get(target_id) is not None
        and _runtime_state_allows_maintenance_refresh(runtime_states.get(target_id))
    )


def filter_maintenance_cover_refresh_states(
    options: ResidentRuntimeOptions,
    states: list[TargetCoverImageRefreshState],
) -> list[TargetCoverImageRefreshState]:
    """避開已有正式掃描工作的 cover refresh jobs。"""

    if not states:
        return []
    target_ids = [state.target_id for state in states]
    with SqliteApplicationContext(options.db_path) as app:
        runtime_states = app.repositories.runtime_states.list_by_targets(target_ids)
        targets = {
            target_id: app.repositories.targets.get(target_id)
            for target_id in target_ids
        }
    return [
        state
        for state in states
        if targets.get(state.target_id) is not None
        and _runtime_state_allows_maintenance_refresh(
            runtime_states.get(state.target_id)
        )
    ]


def _runtime_state_allows_maintenance_refresh(
    state: TargetRuntimeState | None,
) -> bool:
    """runtime recovery retry 等待期間，maintenance refresh 先讓位。"""

    if state is None:
        return True
    if _runtime_state_has_pending_failure_retry(state):
        return False
    return state.runtime_status not in {
        TargetRuntimeStatus.QUEUED,
        TargetRuntimeStatus.RUNNING,
        TargetRuntimeStatus.ERROR,
    }


def _runtime_state_has_pending_failure_retry(state: TargetRuntimeState) -> bool:
    """判斷 target 是否正等待 failure policy 自動重試掃描。"""

    return (
        state.runtime_status == TargetRuntimeStatus.IDLE
        and state.scan_requested_at is not None
        and state.consecutive_failure_count > 0
    )


def record_refresh_runtime_failure(
    *,
    options: ResidentRuntimeOptions,
    target_id: str,
    exc: Exception,
) -> bool:
    """將 maintenance refresh 的 browser runtime failure 接回 scan failure policy。"""

    exception_class, message = _runtime_refresh_failure_detail(exc)
    decision = record_guarded_scan_failure_for_db(
        db_path=options.db_path,
        target_id=target_id,
        reason=SCHEDULER_RUNTIME_REASON,
        message=message,
        source="unknown_exception",
        worker_path="resident_main",
        commit_guard=None,
        exception_class=exception_class,
    )
    return decision is not None


def _runtime_refresh_failure_detail(exc: Exception) -> tuple[str, str]:
    """取出最接近 Playwright runtime closed 的 exception 類型與訊息。"""

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, Exception) and (
            classify_playwright_exception(current) == SCHEDULER_RUNTIME_REASON
            or _is_playwright_driver_shutdown_exception(current)
        ):
            return current.__class__.__name__, _format_exception_message(current)
        current = current.__cause__ or current.__context__
    return exc.__class__.__name__, _format_exception_message(exc)


def _should_skip_refresh_failure_for_shutdown(
    exc: Exception,
    should_stop: StopCheckCallable,
) -> bool:
    """停止流程中 Playwright driver 關閉不應污染 maintenance job 診斷。"""

    return should_stop() and _is_playwright_driver_shutdown_exception(exc)


def _is_scheduler_runtime_refresh_failure(exc: Exception) -> bool:
    """判斷 metadata/cover refresh 失敗是否代表 browser runtime 已損壞。"""

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, Exception):
            if classify_playwright_exception(current) == SCHEDULER_RUNTIME_REASON:
                return True
            if _is_playwright_driver_shutdown_exception(current):
                return True
        current = current.__cause__ or current.__context__
    return False


async def refresh_target_group_cover_image_from_context(
    *,
    options: ResidentRuntimeOptions,
    browser_context: Any,
    state: TargetCoverImageRefreshState,
) -> bool:
    """用 resident browser context 只刷新 target group cover image URL。"""

    group_id = ""
    target_id = state.target_id
    reported_url = state.last_reported_url.strip()
    with SqliteApplicationContext(options.db_path) as app:
        target = app.repositories.targets.get(target_id)
        if target is None:
            return False
        current_url = target.group_cover_image_url.strip()
        if current_url != reported_url:
            app.services.targets.mark_target_cover_image_refresh_stale_skipped(
                target_id,
                current_url=current_url,
                reported_url=reported_url,
                requested_at=state.requested_at,
            )
            return False
        group_id = target.group_id
        if not app.services.targets.mark_target_cover_image_refresh_attempted(
            target_id,
            reported_url=reported_url,
            requested_at=state.requested_at,
        ):
            return False
    if not group_id:
        mark_target_cover_image_refresh_failed(
            options,
            target_id,
            "target group id is empty",
            reported_url=reported_url,
            requested_at=state.requested_at,
        )
        return False
    try:
        cover_image_url = await resolve_group_cover_image_with_context(
            browser_context,
            canonical_url=f"https://www.facebook.com/groups/{group_id}",
        )
    except GroupMetadataError as exc:
        if _is_scheduler_runtime_refresh_failure(exc):
            raise
        logger.info(
            "cover image refresh skipped",
            extra={"target_id": target_id},
        )
        mark_target_cover_image_refresh_failed(
            options,
            target_id,
            str(exc),
            reported_url=reported_url,
            requested_at=state.requested_at,
        )
        return False
    with SqliteApplicationContext(options.db_path) as app:
        target = app.repositories.targets.get(target_id)
        if target is None:
            return False
        current_url = target.group_cover_image_url.strip()
        if current_url != reported_url:
            app.services.targets.mark_target_cover_image_refresh_stale_skipped(
                target_id,
                current_url=current_url,
                reported_url=reported_url,
                requested_at=state.requested_at,
            )
            return True
        normalized_cover_image_url = cover_image_url.strip()
        changed = normalized_cover_image_url != current_url
        app.services.targets.refresh_target_group_cover_image(
            target_id,
            normalized_cover_image_url,
        )
        app.services.targets.mark_target_cover_image_refresh_succeeded(
            target_id,
            resolved_url=normalized_cover_image_url,
            changed=changed,
            reported_url=reported_url,
            requested_at=state.requested_at,
        )
    return True


def _format_exception_message(exc: Exception) -> str:
    """保留非預期例外類型，讓 cover refresh 診斷可回查真正原因。"""

    message = str(exc).strip()
    if message:
        return f"{exc.__class__.__name__}: {message}"
    return exc.__class__.__name__


def mark_target_cover_image_refresh_failed(
    options: ResidentRuntimeOptions,
    target_id: str,
    error: str,
    *,
    reported_url: str | None = None,
    requested_at: datetime | None = None,
) -> None:
    """將 cover image refresh 失敗寫回獨立狀態；target 已刪除時忽略。"""

    with SqliteApplicationContext(options.db_path) as app:
        if app.repositories.targets.get(target_id) is None:
            return
        app.services.targets.mark_target_cover_image_refresh_failed(
            target_id,
            error,
            reported_url=reported_url,
            requested_at=requested_at,
        )


def mark_target_metadata_refresh_failed(
    options: ResidentRuntimeOptions,
    target_id: str,
    error: str,
) -> None:
    """將 metadata refresh 失敗寫回 DB；target 已被刪除時忽略。"""

    with SqliteApplicationContext(options.db_path) as app:
        if app.repositories.targets.get(target_id) is None:
            return
        app.services.targets.mark_target_metadata_refresh_failed(target_id, error)


async def refresh_target_group_name_from_context(
    *,
    options: ResidentRuntimeOptions,
    browser_context: Any,
    target_id: str,
) -> bool:
    """用 resident browser context 補齊 target group name。"""

    group_id = ""
    with SqliteApplicationContext(options.db_path) as app:
        target = app.repositories.targets.get(target_id)
        if target is None:
            return False
        group_id = target.group_id
    if not group_id:
        return False
    try:
        metadata = await resolve_group_metadata_with_context(
            browser_context,
            canonical_url=f"https://www.facebook.com/groups/{group_id}",
        )
    except GroupMetadataError as exc:
        if _is_scheduler_runtime_refresh_failure(exc):
            raise
        logger.info(
            "metadata refresh skipped",
            extra={"target_id": target_id},
        )
        mark_target_metadata_refresh_failed(options, target_id, str(exc))
        return False
    with SqliteApplicationContext(options.db_path) as app:
        if app.repositories.targets.get(target_id) is None:
            return False
        app.services.targets.refresh_target_group_metadata(
            target_id,
            group_name=metadata.group_name,
            group_cover_image_url=metadata.group_cover_image_url,
            overwrite_name=True,
        )
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
