"""Resident async executor worker pool。

職責：以固定 worker slots 消化 TargetQueue，並集中維護 scan guard、
target runtime state、page ownership 與 worker diagnostics。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from collections.abc import Coroutine
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
import logging
from typing import Any
from typing import Protocol

from playwright.async_api import Error as AsyncPlaywrightError
from playwright.async_api import TimeoutError as AsyncPlaywrightTimeoutError

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_STOPPING_REASON
from facebook_monitor.core.scan_failures import SCAN_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import TARGET_STOPPED_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.core.scan_failure_policy import SCHEDULER_RUNTIME_RESTART_ACTION
from facebook_monitor.scheduler.planner import DueTarget
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.scheduler.runtime_recovery import RunningRecoveryAction
from facebook_monitor.scheduler.runtime_recovery import build_recovery_owner_key
from facebook_monitor.worker.comments_pipeline import scan_comments_target_page_async
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.resident_shared import ResidentTarget
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.resident_shared import load_resident_target
from facebook_monitor.worker.resident_shared import mark_resident_target_idle
from facebook_monitor.worker.resident_shared import should_reload_resident_page
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import QueueItem
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.errors import classify_playwright_exception
from facebook_monitor.worker.page_timing import RESIDENT_PAGE_READY_WAIT_MS
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import mark_target_idle_for_scan_commit
from facebook_monitor.worker.scan_finalize import scan_commit_guard_from_runtime_state
from facebook_monitor.worker.scan_finalize import target_matches_scan_commit_guard
from facebook_monitor.worker.scan_failure_finalize import record_guarded_scan_failure_for_db


logger = logging.getLogger(__name__)
AsyncScanCallable = Callable[..., Coroutine[Any, Any, Any]]


class AsyncResidentPageLike(Protocol):
    """resident executor page preparation 需要的 async Playwright page 能力。"""

    url: str

    async def reload(self, *, wait_until: str, timeout: float) -> object:
        """重新載入目前 page。"""

    async def goto(self, url: str, *, wait_until: str, timeout: float) -> object:
        """前往指定 URL。"""

    async def wait_for_timeout(self, timeout: int) -> None:
        """等待指定毫秒。"""


@dataclass(frozen=True)
class AsyncTargetScanResult:
    """保存單一 target async scan 執行結果，供 diagnostics 彙整。"""

    target_id: str
    success: bool = False
    failure: bool = False
    skipped: bool = False
    opened_page: bool = False
    reused_page: bool = False


@dataclass(frozen=True)
class ExecutorCounters:
    """保存 worker pool 自上次讀取後累積的執行結果。"""

    success_count: int = 0
    failure_count: int = 0
    skipped_count: int = 0
    opened_page_count: int = 0
    reused_page_count: int = 0


class ExecutorWorkerPool:
    """長壽命 target executor，維持固定 worker slots 消化 TargetQueue。"""

    def __init__(
        self,
        *,
        options: ResidentRuntimeOptions,
        page_pool: AsyncResidentPagePool,
        target_queue: TargetQueue,
        schedule_planner: TargetSchedulePlanner,
        scan_page: AsyncScanCallable,
        scan_comments_target_page: AsyncScanCallable = scan_comments_target_page_async,
    ) -> None:
        self.options = options
        self.page_pool = page_pool
        self.target_queue = target_queue
        self.schedule_planner = schedule_planner
        self.scan_page = scan_page
        self.scan_comments_target_page = scan_comments_target_page
        self.worker_ids = tuple(
            f"resident-slot-{index + 1}" for index in range(max(options.max_concurrent_scans, 1))
        )
        self.worker_tasks: list[asyncio.Task[None]] = []
        self._counter_lock = asyncio.Lock()
        self._counters = ExecutorCounters()
        self._active_scan_lock = asyncio.Lock()
        self._active_scan_tasks: dict[str, tuple[str, asyncio.Task[Any]]] = {}
        self._active_attempt_lock = asyncio.Lock()
        self._active_attempt_tasks: dict[str, tuple[str, asyncio.Task[Any]]] = {}
        self._runtime_restart_requested = asyncio.Event()

    async def start(self) -> None:
        """啟動固定數量的 executor worker slots。"""

        self.worker_tasks = [
            asyncio.create_task(self._worker_loop(worker_id), name=worker_id)
            for worker_id in self.worker_ids
        ]
        logger.info(
            "resident_executor_start max_concurrent_scans=%s worker_ids=%s",
            self.options.max_concurrent_scans,
            ",".join(self.worker_ids),
        )

    async def stop(
        self,
        *,
        cancel_running: bool = False,
        runtime_restart: bool = False,
    ) -> None:
        """停止 worker slots；必要時取消尚未完成的長掃描。"""

        if runtime_restart:
            self.request_runtime_restart()
        cancelled_ids: tuple[str, ...] = ()
        if cancel_running:
            cancelled_ids = await self.target_queue.cancel_pending()
            for target_id in cancelled_ids:
                if runtime_restart:
                    self._request_target_retry_after_runtime_restart(target_id)
                else:
                    mark_resident_target_idle(self.options.db_path, target_id)
        if cancel_running and runtime_restart:
            await self._cancel_active_attempts_for_runtime_restart()

        for _worker_id in self.worker_ids:
            await self.target_queue.stop_worker()
        logger.info(
            "resident_executor_stop cancel_running=%s runtime_restart=%s "
            "cancelled_pending_target_ids=%s worker_ids=%s",
            cancel_running,
            runtime_restart,
            ",".join(cancelled_ids),
            ",".join(self.worker_ids),
        )
        if cancel_running and not runtime_restart:
            for task in self.worker_tasks:
                if not task.done():
                    task.cancel()
        if self.worker_tasks:
            await asyncio.gather(*self.worker_tasks, return_exceptions=True)

    def worker_health_ok(self) -> bool:
        """回傳 worker tasks 是否仍健康存活。"""

        return all(not task.done() or task.cancelled() for task in self.worker_tasks)

    def runtime_restart_requested(self) -> bool:
        """回傳目前 browser runtime 是否需要關閉並重建。"""

        return self._runtime_restart_requested.is_set()

    def request_runtime_restart(self) -> None:
        """要求外層 resident loop 關閉並重建 browser runtime。"""

        self._runtime_restart_requested.set()

    async def wait_runtime_restart_requested(self) -> None:
        """等待 browser runtime restart request 被觸發。"""

        await self._runtime_restart_requested.wait()

    async def _cancel_active_attempts_for_runtime_restart(self) -> None:
        """取消正在使用壞掉 browser runtime 的 target attempts 並等待寫回。"""

        async with self._active_attempt_lock:
            attempt_tasks = tuple(
                task
                for _owner_key, task in self._active_attempt_tasks.values()
                if not task.done()
            )
        for task in attempt_tasks:
            task.cancel()
        if attempt_tasks:
            await asyncio.gather(*attempt_tasks, return_exceptions=True)

    def _request_target_retry_after_runtime_restart(self, target_id: str) -> None:
        """讓尚未開始的 queued target 在新 runtime 建立後立即補掃。"""

        with SqliteApplicationContext(self.options.db_path) as app:
            if app.repositories.targets.get(target_id) is None:
                return
            state = app.services.targets.ensure_runtime_state(target_id)
            now = utc_now()
            app.repositories.runtime_states.save(
                replace(
                    state,
                    runtime_status=TargetRuntimeStatus.IDLE,
                    scan_requested_at=now,
                    enqueue_reason="",
                    active_worker_id="",
                    active_page_id="",
                    updated_at=now,
                )
            )

    async def enqueue_due_targets(self, due_targets: tuple[DueTarget, ...]) -> int:
        """把 due targets 放入 queue；成功 enqueue 時同步 runtime state。"""

        enqueued_count = 0
        for due_target in due_targets:
            reason = "manual" if due_target.scan_requested else "due"
            queue_item = QueueItem(
                due_target=due_target,
                enqueue_reason=reason,
                enqueued_at=datetime.now().astimezone(),
            )
            accepted = await self.target_queue.enqueue(queue_item)
            if not accepted:
                logger.info(
                    "resident_target_enqueue_skipped target_id=%s reason=%s "
                    "due_at=%s scan_requested=%s",
                    due_target.target_id,
                    "target_already_queued_or_running",
                    due_target.due_at.isoformat(),
                    due_target.scan_requested,
                )
                self._record_guard_skip(
                    due_target.target_id,
                    "scan_guard_skipped: target_already_queued_or_running",
                )
                await self._add_counters(ExecutorCounters(skipped_count=1))
                continue
            with SqliteApplicationContext(self.options.db_path) as app:
                app.services.targets.mark_target_queued(due_target.target_id, reason)
                if due_target.scan_requested:
                    app.services.targets.clear_target_scan_request_if_not_newer(
                        due_target.target_id,
                        due_target.scan_requested_at,
                    )
            logger.info(
                "resident_target_enqueued target_id=%s reason=%s due_at=%s "
                "interval_seconds=%s scan_requested=%s scan_requested_at=%s "
                "enqueued_at=%s",
                due_target.target_id,
                reason,
                due_target.due_at.isoformat(),
                due_target.interval_seconds,
                due_target.scan_requested,
                (
                    due_target.scan_requested_at.isoformat()
                    if due_target.scan_requested_at is not None
                    else ""
                ),
                queue_item.enqueued_at.isoformat(),
            )
            enqueued_count += 1
        return enqueued_count

    async def take_counters(self) -> ExecutorCounters:
        """取出自上次讀取後累積的 worker 結果。"""

        async with self._counter_lock:
            counters = self._counters
            self._counters = ExecutorCounters()
            return counters

    async def cancel_active_attempt_if_owner(
        self,
        action: RunningRecoveryAction,
    ) -> bool:
        """取消同一 owner 的 active attempt，涵蓋 prepare/goto 與 scan 階段。"""

        async with self._active_attempt_lock:
            active_attempt = self._active_attempt_tasks.get(action.target_id)
            if active_attempt is not None:
                owner_key, task = active_attempt
                if owner_key == action.owner_key:
                    task.cancel()
                    return True

        async with self._active_scan_lock:
            active = self._active_scan_tasks.get(action.target_id)
            if active is None:
                return False
            owner_key, task = active
            if owner_key != action.owner_key:
                return False
            task.cancel()
            return True

    async def _worker_loop(self, worker_id: str) -> None:
        """單一 executor worker slot：持續從 queue 取 target 執行 scan。"""

        while True:
            item = await self.target_queue.get()
            if item is None:
                return
            attempt_task: asyncio.Task[AsyncTargetScanResult] = asyncio.create_task(
                self._run_queue_item(worker_id, item),
                name=f"{worker_id}:{item.due_target.target_id}",
            )
            try:
                result = await attempt_task
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    attempt_task.cancel()
                    raise
                result = AsyncTargetScanResult(
                    target_id=item.due_target.target_id,
                    skipped=True,
                )
            await self._add_counters(
                ExecutorCounters(
                    success_count=int(result.success),
                    failure_count=int(result.failure),
                    skipped_count=int(result.skipped),
                    opened_page_count=int(result.opened_page),
                    reused_page_count=int(result.reused_page),
                )
            )

    async def _run_queue_item(self, worker_id: str, item: QueueItem) -> AsyncTargetScanResult:
        """執行 queue 中的單一 target，並維護 runtime / page ownership。"""

        target_id = item.due_target.target_id
        opened = False
        page_id = ""
        acquired_page = False
        owner_key = ""
        commit_guard: ScanCommitGuard | None = None
        try:
            resident_target = load_resident_target(self.options.db_path, target_id)
            if not self._target_still_active(target_id):
                logger.info(
                    "resident_target_skipped target_id=%s worker_id=%s reason=%s",
                    target_id,
                    worker_id,
                    "target_not_active_before_running",
                )
                mark_resident_target_idle(self.options.db_path, target_id)
                return AsyncTargetScanResult(target_id=target_id, skipped=True)

            page_id = await self.page_pool.reserve_page_id(target_id)
            with SqliteApplicationContext(self.options.db_path) as app:
                locked_state = app.services.targets.try_mark_target_running(
                    target_id,
                    worker_id,
                    page_id=page_id,
                )
            if locked_state is None:
                logger.info(
                    "resident_target_skipped target_id=%s worker_id=%s page_id=%s "
                    "reason=%s",
                    target_id,
                    worker_id,
                    page_id,
                    "running_claim_rejected",
                )
                return AsyncTargetScanResult(target_id=target_id, skipped=True)
            commit_guard = scan_commit_guard_from_runtime_state(locked_state)
            owner_key = build_recovery_owner_key(
                worker_id=commit_guard.worker_id,
                started_at=commit_guard.started_at,
                page_id=commit_guard.page_id,
            )
            await self.target_queue.bind_running_owner(target_id, owner_key)
            await self._register_active_attempt(target_id, owner_key)
            self.schedule_planner.mark_dispatched(item.due_target)
            logger.info(
                "resident_target_running target_id=%s worker_id=%s page_id=%s "
                "owner_key=%s enqueue_reason=%s enqueued_at=%s due_at=%s "
                "scan_requested=%s",
                target_id,
                worker_id,
                page_id,
                owner_key,
                item.enqueue_reason,
                item.enqueued_at.isoformat(),
                item.due_target.due_at.isoformat(),
                item.due_target.scan_requested,
            )

            page, acquired_page_id, opened = await self.page_pool.acquire(
                resident_target,
                worker_id,
                page_id=page_id,
            )
            acquired_page = True
            page_id = acquired_page_id
            await prepare_resident_main_page(
                page=page,
                target=resident_target,
                timeout_ms=max(
                    self.options.scan_timeout_seconds,
                    PYTHON_SCHEDULER_RUNTIME_DEFAULTS.min_browser_scan_timeout_seconds,
                )
                * 1000,
            )
            reloaded_at = await self.page_pool.mark_reloaded_if_page_id(
                target_id,
                page_id,
                current_url=str(getattr(page, "url", "") or ""),
            )
            with SqliteApplicationContext(self.options.db_path) as app:
                page_reload_state = app.services.targets.mark_target_page_reloaded_if_owner(
                    target_id,
                    worker_id=commit_guard.worker_id,
                    started_at=commit_guard.started_at,
                    page_id=page_id,
                    reloaded_at=reloaded_at,
                )
                if page_reload_state is None:
                    logger.info(
                        "resident_target_skipped target_id=%s worker_id=%s page_id=%s "
                        "reason=%s",
                        target_id,
                        worker_id,
                        page_id,
                        "page_reload_owner_changed",
                    )
                    return AsyncTargetScanResult(target_id=target_id, skipped=True)
            with SqliteApplicationContext(self.options.db_path) as app:
                selected_scan_page = self._select_scan_page(resident_target.target.target_kind)
                await self._run_scan_with_heartbeat(
                    selected_scan_page,
                    page=page,
                    app=app,
                    target=resident_target.target,
                    config=resident_target.config,
                    scroll_rounds=self.options.scroll_rounds,
                    scroll_wait_ms=self.options.scroll_wait_ms,
                    worker_id=worker_id,
                    page_id=page_id,
                    commit_guard=commit_guard,
                )
                committed_current_attempt = False
                if mark_target_idle_for_scan_commit(
                    app=app,
                    target_id=target_id,
                    commit_guard=commit_guard,
                ):
                    committed_current_attempt = True
            if not committed_current_attempt:
                logger.info(
                    "resident_target_skipped target_id=%s worker_id=%s page_id=%s "
                    "reason=%s",
                    target_id,
                    worker_id,
                    page_id,
                    "scan_commit_guard_mismatch",
                )
                return AsyncTargetScanResult(target_id=target_id, skipped=True)
            logger.info(
                "resident_target_finished target_id=%s worker_id=%s page_id=%s "
                "result=%s opened_page=%s reused_page=%s",
                target_id,
                worker_id,
                page_id,
                "success",
                opened,
                not opened,
            )
            return AsyncTargetScanResult(
                target_id=target_id,
                success=True,
                opened_page=opened,
                reused_page=not opened,
            )
        except WorkerFailure as exc:
            decision = record_guarded_scan_failure_for_db(
                db_path=self.options.db_path,
                target_id=target_id,
                reason=exc.reason,
                message=str(exc),
                source="worker_failure",
                worker_path="resident_main",
                commit_guard=commit_guard,
                exception_class=exc.__class__.__name__,
                page_reused=acquired_page and not opened,
            )
            if decision is None:
                logger.info(
                    "resident_target_skipped target_id=%s worker_id=%s page_id=%s "
                    "reason=%s",
                    target_id,
                    worker_id,
                    page_id,
                    "worker_failure_owner_changed",
                )
                return AsyncTargetScanResult(target_id=target_id, skipped=True)
            if decision.discard_page:
                await self.page_pool.discard(target_id)
            if decision.recovery_action == SCHEDULER_RUNTIME_RESTART_ACTION:
                self.request_runtime_restart()
            logger.warning(
                "resident_target_finished target_id=%s worker_id=%s page_id=%s "
                "result=%s reason=%s runtime_action=%s recovery_action=%s "
                "retryable=%s retry_streak=%s retry_limit=%s discard_page=%s "
                "opened_page=%s reused_page=%s exception_class=%s",
                target_id,
                worker_id,
                page_id,
                "failure",
                exc.reason,
                decision.target_action,
                decision.recovery_action,
                decision.retryable,
                decision.retry_streak,
                decision.retry_limit,
                decision.discard_page,
                opened,
                acquired_page and not opened,
                exc.__class__.__name__,
            )
            return AsyncTargetScanResult(
                target_id=target_id,
                failure=True,
                opened_page=opened,
                reused_page=acquired_page and not opened,
            )
        except asyncio.CancelledError:
            if self.runtime_restart_requested():
                decision = record_guarded_scan_failure_for_db(
                    db_path=self.options.db_path,
                    target_id=target_id,
                    reason=SCHEDULER_RUNTIME_REASON,
                    message="browser runtime restart requested",
                    source="unknown_exception",
                    worker_path="resident_main",
                    commit_guard=commit_guard,
                    exception_class="CancelledError",
                    page_reused=acquired_page and not opened,
                )
                if decision is None:
                    logger.info(
                        "resident_target_skipped target_id=%s worker_id=%s page_id=%s "
                        "reason=%s",
                        target_id,
                        worker_id,
                        page_id,
                        "runtime_restart_cancel_owner_changed",
                    )
                    return AsyncTargetScanResult(target_id=target_id, skipped=True)
                if decision.discard_page:
                    await self.page_pool.discard(target_id)
                logger.warning(
                    "resident_target_finished target_id=%s worker_id=%s page_id=%s "
                    "result=%s reason=%s runtime_action=%s recovery_action=%s "
                    "retryable=%s retry_streak=%s retry_limit=%s discard_page=%s",
                    target_id,
                    worker_id,
                    page_id,
                    "failure",
                    SCHEDULER_RUNTIME_REASON,
                    decision.target_action,
                    decision.recovery_action,
                    decision.retryable,
                    decision.retry_streak,
                    decision.retry_limit,
                    decision.discard_page,
                )
                return AsyncTargetScanResult(target_id=target_id, failure=True)
            record_guarded_scan_failure_for_db(
                db_path=self.options.db_path,
                target_id=target_id,
                reason=SCHEDULER_STOPPING_REASON,
                message="resident scheduler is stopping",
                source="scheduler_cancel",
                worker_path="resident_main",
                commit_guard=commit_guard,
                exception_class="CancelledError",
                page_reused=acquired_page and not opened,
            )
            raise
        except (AsyncPlaywrightTimeoutError, AsyncPlaywrightError) as exc:
            reason = classify_playwright_exception(exc)
            decision = record_guarded_scan_failure_for_db(
                db_path=self.options.db_path,
                target_id=target_id,
                reason=reason,
                message=str(exc),
                source="playwright",
                worker_path="resident_main",
                commit_guard=commit_guard,
                exception_class=exc.__class__.__name__,
                page_reused=acquired_page and not opened,
            )
            if decision is None:
                logger.info(
                    "resident_target_skipped target_id=%s worker_id=%s page_id=%s "
                    "reason=%s",
                    target_id,
                    worker_id,
                    page_id,
                    "playwright_failure_owner_changed",
                )
                return AsyncTargetScanResult(target_id=target_id, skipped=True)
            if decision.discard_page:
                await self.page_pool.discard(target_id)
            if decision.recovery_action == SCHEDULER_RUNTIME_RESTART_ACTION:
                self.request_runtime_restart()
            logger.warning(
                "resident_target_finished target_id=%s worker_id=%s page_id=%s "
                "result=%s reason=%s runtime_action=%s recovery_action=%s "
                "retryable=%s retry_streak=%s retry_limit=%s discard_page=%s "
                "opened_page=%s reused_page=%s exception_class=%s",
                target_id,
                worker_id,
                page_id,
                "failure",
                reason,
                decision.target_action,
                decision.recovery_action,
                decision.retryable,
                decision.retry_streak,
                decision.retry_limit,
                decision.discard_page,
                opened,
                acquired_page and not opened,
                exc.__class__.__name__,
            )
            return AsyncTargetScanResult(target_id=target_id, failure=True)
        except Exception as exc:
            decision = record_guarded_scan_failure_for_db(
                db_path=self.options.db_path,
                target_id=target_id,
                reason=UNKNOWN_REASON,
                message=str(exc),
                source="unknown_exception",
                worker_path="resident_main",
                commit_guard=commit_guard,
                exception_class=exc.__class__.__name__,
                page_reused=acquired_page and not opened,
            )
            if decision is None:
                logger.info(
                    "resident_target_skipped target_id=%s worker_id=%s page_id=%s "
                    "reason=%s",
                    target_id,
                    worker_id,
                    page_id,
                    "unknown_failure_owner_changed",
                )
                return AsyncTargetScanResult(target_id=target_id, skipped=True)
            if decision.discard_page:
                await self.page_pool.discard(target_id)
            if decision.recovery_action == SCHEDULER_RUNTIME_RESTART_ACTION:
                self.request_runtime_restart()
            logger.warning(
                "resident_target_finished target_id=%s worker_id=%s page_id=%s "
                "result=%s reason=%s runtime_action=%s recovery_action=%s "
                "retryable=%s retry_streak=%s retry_limit=%s discard_page=%s "
                "opened_page=%s reused_page=%s exception_class=%s",
                target_id,
                worker_id,
                page_id,
                "failure",
                UNKNOWN_REASON,
                decision.target_action,
                decision.recovery_action,
                decision.retryable,
                decision.retry_streak,
                decision.retry_limit,
                decision.discard_page,
                opened,
                acquired_page and not opened,
                exc.__class__.__name__,
            )
            return AsyncTargetScanResult(target_id=target_id, failure=True)
        finally:
            await self._unregister_active_attempt(target_id, owner_key)
            if page_id:
                await self.page_pool.release_if_page_id(target_id, page_id)
            else:
                await self.page_pool.release(target_id)
            await self.target_queue.complete(target_id, owner_key=owner_key)
            self.schedule_planner.mark_finished(target_id)

    async def _register_active_attempt(self, target_id: str, owner_key: str) -> None:
        """記錄整個 target attempt task，讓 recovery 可取消 prepare/goto 階段。"""

        task = asyncio.current_task()
        if task is None or not owner_key:
            return
        async with self._active_attempt_lock:
            self._active_attempt_tasks[target_id] = (owner_key, task)

    async def _unregister_active_attempt(self, target_id: str, owner_key: str) -> None:
        """移除同 owner 的 active attempt task 紀錄。"""

        if not owner_key:
            return
        async with self._active_attempt_lock:
            active = self._active_attempt_tasks.get(target_id)
            if active is not None and active[0] == owner_key:
                self._active_attempt_tasks.pop(target_id, None)

    async def _add_counters(self, counters: ExecutorCounters) -> None:
        """累加 worker pool diagnostics counters。"""

        async with self._counter_lock:
            self._counters = ExecutorCounters(
                success_count=self._counters.success_count + counters.success_count,
                failure_count=self._counters.failure_count + counters.failure_count,
                skipped_count=self._counters.skipped_count + counters.skipped_count,
                opened_page_count=self._counters.opened_page_count + counters.opened_page_count,
                reused_page_count=self._counters.reused_page_count + counters.reused_page_count,
            )

    async def _run_scan_with_heartbeat(
        self,
        scan_page: AsyncScanCallable,
        **kwargs: Any,
    ) -> Any:
        """以 timeout 與 heartbeat 包住 target scan，避免長跑誤判或永久卡住。"""

        target = kwargs["target"]
        target_id = target.id
        worker_id = str(kwargs.pop("worker_id"))
        page_id = str(kwargs.pop("page_id"))
        commit_guard = kwargs["commit_guard"]
        scan_task: asyncio.Task[Any] = asyncio.create_task(scan_page(**kwargs))
        owner_key = build_recovery_owner_key(
            worker_id=worker_id,
            started_at=commit_guard.started_at,
            page_id=page_id,
        )
        async with self._active_scan_lock:
            self._active_scan_tasks[target_id] = (owner_key, scan_task)
        heartbeat_task = asyncio.create_task(
            self._scan_heartbeat_loop(
                target_id=target_id,
                worker_id=worker_id,
                page_id=page_id,
                commit_guard=commit_guard,
                scan_task=scan_task,
            )
        )
        try:
            timeout_seconds = max(
                float(self.options.scan_timeout_seconds),
                PYTHON_SCHEDULER_RUNTIME_DEFAULTS.min_scan_task_timeout_seconds,
            )
            return await asyncio.wait_for(
                scan_task,
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            raise WorkerFailure(
                SCAN_TIMEOUT_REASON,
                f"scan exceeded {timeout_seconds:g} seconds",
            ) from exc
        except asyncio.CancelledError:
            if not self._target_matches_commit_guard(target_id, commit_guard):
                raise WorkerFailure(
                    TARGET_STOPPED_REASON,
                    "target stopped during scan",
                ) from None
            raise
        finally:
            async with self._active_scan_lock:
                active = self._active_scan_tasks.get(target_id)
                if active is not None and active[0] == owner_key:
                    self._active_scan_tasks.pop(target_id, None)
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

    async def _scan_heartbeat_loop(
        self,
        *,
        target_id: str,
        worker_id: str,
        page_id: str,
        commit_guard: ScanCommitGuard,
        scan_task: asyncio.Task[Any],
    ) -> None:
        """掃描期間刷新 heartbeat，並在 target 被停止時取消本輪 scan。"""

        interval_seconds = max(
            0.01,
            min(
                float(self.options.heartbeat_interval_seconds),
                float(self.options.stale_running_after_seconds) / 3,
            ),
        )
        while not scan_task.done():
            await asyncio.sleep(interval_seconds)
            if scan_task.done():
                return
            if not self._target_matches_commit_guard(target_id, commit_guard):
                scan_task.cancel()
                return
            with SqliteApplicationContext(self.options.db_path) as app:
                if app.repositories.targets.get(target_id) is None:
                    return
                heartbeat_state = app.services.targets.record_target_heartbeat_if_owner(
                    target_id,
                    worker_id=worker_id,
                    started_at=commit_guard.started_at,
                    page_id=page_id,
                )
                if heartbeat_state is None:
                    scan_task.cancel()
                    return

    def _record_guard_skip(self, target_id: str, reason: str) -> None:
        """將 queue admission guard skip 寫入 runtime state。"""

        with SqliteApplicationContext(self.options.db_path) as app:
            if app.repositories.targets.get(target_id) is None:
                return
            app.services.targets.record_scan_guard_skip(target_id, reason)

    def _target_still_active(self, target_id: str) -> bool:
        """確認 target 從 enqueue 到執行前仍保持 desired active。"""

        with SqliteApplicationContext(self.options.db_path) as app:
            target = app.repositories.targets.get(target_id)
            if target is None or not target.enabled or target.paused:
                return False
            state = app.services.targets.ensure_runtime_state(target_id)
            return state.desired_state == TargetDesiredState.ACTIVE

    def _target_matches_commit_guard(
        self,
        target_id: str,
        commit_guard: ScanCommitGuard,
    ) -> bool:
        """確認 target runtime 仍是同一輪 running attempt。"""

        with SqliteApplicationContext(self.options.db_path) as app:
            if app.repositories.targets.get(target_id) is None:
                return False
            return target_matches_scan_commit_guard(
                app=app,
                target_id=target_id,
                commit_guard=commit_guard,
            )

    def _select_scan_page(self, target_kind: TargetKind) -> AsyncScanCallable:
        """依 target kind 選擇 resident main 掃描函式。"""

        if target_kind == TargetKind.COMMENTS:
            return self.scan_comments_target_page
        return self.scan_page


async def prepare_resident_main_page(
    *,
    page: AsyncResidentPageLike,
    target: ResidentTarget,
    timeout_ms: float,
) -> None:
    """讓 async page 停在 target route；同一 route 只 reload。"""

    current_url = str(getattr(page, "url", "") or "")
    if should_reload_resident_page(current_url, target.target.canonical_url):
        await page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
    else:
        await page.goto(target.target.canonical_url, wait_until="domcontentloaded", timeout=timeout_ms)
    await page.wait_for_timeout(RESIDENT_PAGE_READY_WAIT_MS)
