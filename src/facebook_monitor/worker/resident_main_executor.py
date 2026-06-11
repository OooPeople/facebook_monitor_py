"""Resident async executor worker pool。

職責：以固定 worker slots 消化 TargetQueue，並集中維護 scan guard、
target runtime state、page ownership 與 worker diagnostics。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
import logging
import sqlite3
from typing import Any
from typing import TypeVar

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import SCAN_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import TARGET_STOPPED_REASON
from facebook_monitor.persistence.sqlite_retry import is_sqlite_lock_error
from facebook_monitor.persistence.sqlite_retry import run_sqlite_operation_with_retry
from facebook_monitor.persistence.sqlite_retry import run_sqlite_operation_with_retry_async
from facebook_monitor.scheduler.planner import DueTarget
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.scheduler.runtime_recovery import RunningRecoveryAction
from facebook_monitor.scheduler.runtime_recovery import build_recovery_owner_key
from facebook_monitor.worker.comments_pipeline import scan_comments_target_page_async
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.resident_main_executor_attempt import run_queue_item
from facebook_monitor.worker.resident_main_executor_types import AsyncScanCallable
from facebook_monitor.worker.resident_main_executor_types import AsyncTargetScanResult
from facebook_monitor.worker.resident_main_executor_types import ExecutorCounters
from facebook_monitor.worker.resident_main_page_prepare import prepare_resident_main_page
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.resident_shared import mark_resident_target_idle
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import QueueItem
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import begin_scan_commit_transaction
from facebook_monitor.worker.scan_finalize import target_matches_scan_commit_guard


logger = logging.getLogger(__name__)
T = TypeVar("T")
__all__ = ("ExecutorWorkerPool", "prepare_resident_main_page")


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
        self._stopping = False

    async def start(self) -> None:
        """啟動固定數量的 executor worker slots。"""

        self._stopping = False
        self.worker_tasks = [
            asyncio.create_task(self._worker_loop(worker_id), name=worker_id)
            for worker_id in self.worker_ids
        ]
        for task in self.worker_tasks:
            task.add_done_callback(self._handle_worker_task_done)
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

        self._stopping = True
        if runtime_restart:
            self.request_runtime_restart()
        cancelled_ids: tuple[str, ...] = ()
        if cancel_running:
            cancelled_ids = await self.target_queue.cancel_pending()
            for target_id in cancelled_ids:
                if runtime_restart:
                    await self._request_target_retry_after_runtime_restart_async(target_id)
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

    def _handle_worker_task_done(self, task: asyncio.Task[None]) -> None:
        """記錄非預期結束的 executor worker，並要求外層重建 runtime。"""

        if self._stopping:
            return
        worker_id = task.get_name()
        if task.cancelled():
            logger.warning(
                "resident_executor_worker_stopped worker_id=%s reason=%s",
                worker_id,
                "cancelled_unexpectedly",
            )
            self.request_runtime_restart()
            return
        exc = task.exception()
        if exc is None:
            logger.error(
                "resident_executor_worker_stopped worker_id=%s reason=%s",
                worker_id,
                "returned_unexpectedly",
            )
        else:
            logger.error(
                "resident_executor_worker_stopped worker_id=%s reason=%s exception_class=%s",
                worker_id,
                "exception",
                exc.__class__.__name__,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        self.request_runtime_restart()

    def worker_health_ok(self) -> bool:
        """回傳 worker tasks 是否仍健康存活。"""

        return bool(self.worker_tasks) and all(not task.done() for task in self.worker_tasks)

    def worker_statuses(self) -> tuple[str, ...]:
        """回傳每個 worker task 的診斷狀態，供 scheduler log 判讀。"""

        statuses: list[str] = []
        for task in self.worker_tasks:
            worker_id = task.get_name()
            if task.cancelled():
                statuses.append(f"{worker_id}:cancelled")
            elif not task.done():
                statuses.append(f"{worker_id}:running")
            else:
                exc = task.exception()
                if exc is None:
                    statuses.append(f"{worker_id}:returned")
                else:
                    statuses.append(f"{worker_id}:failed:{exc.__class__.__name__}")
        return tuple(statuses)

    def runtime_restart_requested(self) -> bool:
        """回傳目前 browser runtime 是否需要關閉並重建。"""

        return self._runtime_restart_requested.is_set()

    def request_runtime_restart(self) -> None:
        """要求外層 resident loop 關閉並重建 browser runtime。"""

        self._runtime_restart_requested.set()

    async def wait_runtime_restart_requested(self) -> None:
        """等待 browser runtime restart request 被觸發。"""

        await self._runtime_restart_requested.wait()

    async def _run_db_operation_with_retry(
        self,
        operation_name: str,
        operation: Callable[[], T],
    ) -> T:
        """以 async bounded retry 包住一個可 rollback 重跑的 DB operation。"""

        return await run_sqlite_operation_with_retry_async(
            operation,
            operation_name=operation_name,
            logger=logger,
        )

    async def _cancel_active_attempts_for_runtime_restart(self) -> None:
        """取消正在使用壞掉 browser runtime 的 target attempts 並等待寫回。"""

        async with self._active_attempt_lock:
            attempt_tasks = tuple(
                task for _owner_key, task in self._active_attempt_tasks.values() if not task.done()
            )
        for task in attempt_tasks:
            task.cancel()
        if attempt_tasks:
            await asyncio.gather(*attempt_tasks, return_exceptions=True)

    def _request_target_retry_after_runtime_restart(self, target_id: str) -> None:
        """讓尚未開始的 queued target 在新 runtime 建立後立即補掃。"""

        run_sqlite_operation_with_retry(
            lambda: self._write_target_retry_after_runtime_restart(target_id),
            operation_name="request_target_retry_after_runtime_restart",
            logger=logger,
        )

    async def _request_target_retry_after_runtime_restart_async(self, target_id: str) -> None:
        """async stop path 使用的 queued target retry 寫回，不阻塞 event loop。"""

        await self._run_db_operation_with_retry(
            "request_target_retry_after_runtime_restart",
            lambda: self._write_target_retry_after_runtime_restart(target_id),
        )

    def _write_target_retry_after_runtime_restart(self, target_id: str) -> None:
        """寫回 queued target retry state；由 sync/async retry wrapper 呼叫。"""

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

    async def _retry_target_after_sqlite_lock(
        self,
        *,
        target_id: str,
        commit_guard: ScanCommitGuard | None,
    ) -> None:
        """DB contention 中止本輪時，保留 failure streak 並安排下輪補掃。"""

        await self._run_db_operation_with_retry(
            "retry_target_after_sqlite_lock",
            lambda: self._write_target_retry_after_sqlite_lock(
                target_id=target_id,
                commit_guard=commit_guard,
            ),
        )

    def _write_target_retry_after_sqlite_lock(
        self,
        *,
        target_id: str,
        commit_guard: ScanCommitGuard | None,
    ) -> None:
        """以 guard 確認目前 attempt 後，將 DB lock 中止的 target 放回待掃。"""

        with SqliteApplicationContext(self.options.db_path) as app:
            begin_scan_commit_transaction(app)
            target = app.repositories.targets.get(target_id)
            if target is None or not target.enabled or target.paused:
                return
            state = app.services.targets.ensure_runtime_state(target_id)
            if state.desired_state != TargetDesiredState.ACTIVE:
                return
            if commit_guard is None:
                if state.runtime_status not in {
                    TargetRuntimeStatus.IDLE,
                    TargetRuntimeStatus.QUEUED,
                }:
                    return
            else:
                if not target_matches_scan_commit_guard(
                    app=app,
                    target_id=target_id,
                    commit_guard=commit_guard,
                ):
                    return
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
            reserved = await self.target_queue.reserve(queue_item)
            if not reserved:
                logger.info(
                    "resident_target_enqueue_skipped target_id=%s reason=%s "
                    "due_at=%s scan_requested=%s",
                    due_target.target_id,
                    "target_already_queued_or_running",
                    due_target.due_at.isoformat(),
                    due_target.scan_requested,
                )
                await self._record_guard_skip(
                    due_target.target_id,
                    "scan_guard_skipped: target_already_queued_or_running",
                )
                await self._add_counters(ExecutorCounters(skipped_count=1))
                continue

            def operation() -> None:
                with SqliteApplicationContext(self.options.db_path) as app:
                    app.services.targets.mark_target_queued(due_target.target_id, reason)
                    if due_target.scan_requested:
                        app.services.targets.clear_target_scan_request_if_not_newer(
                            due_target.target_id,
                            due_target.scan_requested_at,
                        )

            try:
                await self._run_db_operation_with_retry("mark_target_queued", operation)
                if not await self.target_queue.publish_reserved(queue_item):
                    raise RuntimeError(
                        "reserved target queue item disappeared before publish: "
                        f"{due_target.target_id}"
                    )
            except Exception:
                await self.target_queue.release_reserved(due_target.target_id)
                raise
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

        return await run_queue_item(self, worker_id, item)

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
        *,
        page: object,
        app: ApplicationContext,
        target: TargetDescriptor,
        config: TargetConfig,
        scroll_rounds: int,
        scroll_wait_ms: int,
        worker_id: str,
        page_id: str,
        commit_guard: ScanCommitGuard,
    ) -> object:
        """以 timeout 與 heartbeat 包住 target scan，避免長跑誤判或永久卡住。"""

        target_id = target.id
        scan_task: asyncio.Task[object] = asyncio.create_task(
            scan_page(
                page=page,
                app=app,
                target=target,
                config=config,
                scroll_rounds=scroll_rounds,
                scroll_wait_ms=scroll_wait_ms,
                commit_guard=commit_guard,
            )
        )
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
            guard_matches = await self._run_db_operation_with_retry(
                "target_matches_commit_guard",
                lambda: self._target_matches_commit_guard(target_id, commit_guard),
            )
            if not guard_matches:
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
            try:
                heartbeat_owner_matches = await self._run_db_operation_with_retry(
                    "guarded_record_target_heartbeat",
                    lambda: self._record_scan_heartbeat_if_owner(
                        target_id=target_id,
                        worker_id=worker_id,
                        page_id=page_id,
                        commit_guard=commit_guard,
                    ),
                )
            except sqlite3.OperationalError as exc:
                if not is_sqlite_lock_error(exc):
                    raise
                logger.warning(
                    "resident_target_heartbeat_skipped target_id=%s worker_id=%s "
                    "page_id=%s reason=%s exception_class=%s",
                    target_id,
                    worker_id,
                    page_id,
                    "database_locked",
                    exc.__class__.__name__,
                )
                continue
            if not heartbeat_owner_matches:
                scan_task.cancel()
                return

    def _record_scan_heartbeat_if_owner(
        self,
        *,
        target_id: str,
        worker_id: str,
        page_id: str,
        commit_guard: ScanCommitGuard,
    ) -> bool:
        """在單一 DB operation 中確認 owner 並刷新 scan heartbeat。"""

        with SqliteApplicationContext(self.options.db_path) as app:
            if app.repositories.targets.get(target_id) is None:
                return False
            if not target_matches_scan_commit_guard(
                app=app,
                target_id=target_id,
                commit_guard=commit_guard,
            ):
                return False
            heartbeat_state = app.services.targets.guarded_record_target_heartbeat(
                target_id,
                worker_id=worker_id,
                started_at=commit_guard.started_at,
                page_id=page_id,
            )
            return heartbeat_state is not None

    async def _record_guard_skip(self, target_id: str, reason: str) -> None:
        """將 queue admission guard skip 寫入 runtime state。"""

        def operation() -> None:
            with SqliteApplicationContext(self.options.db_path) as app:
                if app.repositories.targets.get(target_id) is None:
                    return
                app.services.targets.record_scan_guard_skip(target_id, reason)

        await self._run_db_operation_with_retry("record_scan_guard_skip", operation)

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
