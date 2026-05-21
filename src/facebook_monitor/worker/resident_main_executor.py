"""Resident async executor worker pool。

職責：以固定 worker slots 消化 TargetQueue，並集中維護 scan guard、
target runtime state、page ownership 與 worker diagnostics。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from typing import Protocol

from playwright.async_api import Error as AsyncPlaywrightError
from playwright.async_api import TimeoutError as AsyncPlaywrightTimeoutError

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.scan_failures import SCAN_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import TARGET_STOPPED_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.scheduler.planner import DueTarget
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
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

    async def start(self) -> None:
        """啟動固定數量的 executor worker slots。"""

        self.worker_tasks = [
            asyncio.create_task(self._worker_loop(worker_id), name=worker_id)
            for worker_id in self.worker_ids
        ]

    async def stop(self, *, cancel_running: bool = False) -> None:
        """停止 worker slots；必要時取消尚未完成的長掃描。"""

        if cancel_running:
            for target_id in await self.target_queue.cancel_pending():
                mark_resident_target_idle(self.options.db_path, target_id)

        for _worker_id in self.worker_ids:
            await self.target_queue.stop_worker()
        if cancel_running:
            for task in self.worker_tasks:
                if not task.done():
                    task.cancel()
        if self.worker_tasks:
            await asyncio.gather(*self.worker_tasks, return_exceptions=True)

    def worker_health_ok(self) -> bool:
        """回傳 worker tasks 是否仍健康存活。"""

        return all(not task.done() or task.cancelled() for task in self.worker_tasks)

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
            enqueued_count += 1
        return enqueued_count

    async def take_counters(self) -> ExecutorCounters:
        """取出自上次讀取後累積的 worker 結果。"""

        async with self._counter_lock:
            counters = self._counters
            self._counters = ExecutorCounters()
            return counters

    async def _worker_loop(self, worker_id: str) -> None:
        """單一 executor worker slot：持續從 queue 取 target 執行 scan。"""

        while True:
            item = await self.target_queue.get()
            if item is None:
                return
            result = await self._run_queue_item(worker_id, item)
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
        commit_guard: ScanCommitGuard | None = None
        try:
            resident_target = load_resident_target(self.options.db_path, target_id)
            if not self._target_still_active(target_id):
                mark_resident_target_idle(self.options.db_path, target_id)
                return AsyncTargetScanResult(target_id=target_id, skipped=True)

            page, page_id, opened = await self.page_pool.acquire(resident_target, worker_id)
            with SqliteApplicationContext(self.options.db_path) as app:
                locked_state = app.services.targets.try_mark_target_running(
                    target_id,
                    worker_id,
                    page_id=page_id,
                )
            if locked_state is None:
                return AsyncTargetScanResult(target_id=target_id, skipped=True)
            commit_guard = scan_commit_guard_from_runtime_state(locked_state)
            self.schedule_planner.mark_dispatched(item.due_target)

            await prepare_resident_main_page(
                page=page,
                target=resident_target,
                timeout_ms=max(self.options.scan_timeout_seconds, 10) * 1000,
            )
            reloaded_at = await self.page_pool.mark_reloaded(
                target_id,
                current_url=str(getattr(page, "url", "") or ""),
            )
            with SqliteApplicationContext(self.options.db_path) as app:
                app.services.targets.mark_target_page_reloaded(
                    target_id,
                    page_id=page_id,
                    reloaded_at=reloaded_at,
                )
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
                return AsyncTargetScanResult(target_id=target_id, skipped=True)
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
                page_reused=not opened and bool(page_id),
            )
            if decision is None:
                return AsyncTargetScanResult(target_id=target_id, skipped=True)
            if decision.discard_page:
                await self.page_pool.discard(target_id)
            return AsyncTargetScanResult(
                target_id=target_id,
                failure=True,
                opened_page=opened,
                reused_page=not opened and bool(page_id),
            )
        except asyncio.CancelledError:
            record_guarded_scan_failure_for_db(
                db_path=self.options.db_path,
                target_id=target_id,
                reason="scheduler_stopping",
                message="resident scheduler is stopping",
                source="scheduler_cancel",
                worker_path="resident_main",
                commit_guard=commit_guard,
                exception_class="CancelledError",
                page_reused=not opened and bool(page_id),
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
                page_reused=not opened and bool(page_id),
            )
            if decision is None:
                return AsyncTargetScanResult(target_id=target_id, skipped=True)
            if decision.discard_page:
                await self.page_pool.discard(target_id)
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
                page_reused=not opened and bool(page_id),
            )
            if decision is None:
                return AsyncTargetScanResult(target_id=target_id, skipped=True)
            if decision.discard_page:
                await self.page_pool.discard(target_id)
            return AsyncTargetScanResult(target_id=target_id, failure=True)
        finally:
            await self.page_pool.release(target_id)
            await self.target_queue.complete(target_id)
            self.schedule_planner.mark_finished(target_id)

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
                app.services.targets.record_target_heartbeat(
                    target_id,
                    worker_id=worker_id,
                    page_id=page_id,
                )

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
