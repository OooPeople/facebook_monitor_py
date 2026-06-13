"""Single-target execution path for the resident executor."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
import sqlite3
from typing import Protocol
from typing import TypeVar

from playwright.async_api import Error as AsyncPlaywrightError
from playwright.async_api import TimeoutError as AsyncPlaywrightTimeoutError

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_STOPPING_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.core.scan_failure_policy import ScanFailureDecision
from facebook_monitor.core.scan_failure_policy import SCHEDULER_RUNTIME_RESTART_ACTION
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
from facebook_monitor.persistence.sqlite_retry import is_sqlite_lock_error
from facebook_monitor.scheduler.runtime_recovery import build_recovery_owner_key
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.errors import classify_playwright_exception
from facebook_monitor.worker.errors import classify_wrapped_playwright_exception
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.resident_main_executor_types import AsyncReusablePageLike
from facebook_monitor.worker.resident_main_executor_types import AsyncScanCallable
from facebook_monitor.worker.resident_main_executor_types import AsyncTargetScanResult
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_page_prepare import prepare_resident_main_page
from facebook_monitor.worker.resident_main_queue import QueueItem
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.resident_scan_db import RESIDENT_SCAN_DB_BUSY_TIMEOUT_MS
from facebook_monitor.worker.resident_scan_db import set_resident_scan_db_busy_timeout
from facebook_monitor.worker.resident_shared import ResidentTarget
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.resident_shared import load_resident_target
from facebook_monitor.worker.resident_shared import mark_resident_target_idle_if_not_running
from facebook_monitor.worker.scan_failure_finalize import record_guarded_scan_failure_for_db_async
from facebook_monitor.worker.scan_finalize import mark_target_idle_for_scan_commit
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import scan_commit_guard_from_runtime_state


logger = logging.getLogger(__name__)
T = TypeVar("T")


class ResidentExecutorAttemptHost(Protocol):
    """描述 resident attempt helper 需要的 executor host 能力。"""

    options: ResidentRuntimeOptions
    page_pool: AsyncResidentPagePool
    target_queue: TargetQueue
    schedule_planner: TargetSchedulePlanner

    async def _run_db_operation_with_retry(
        self,
        operation_name: str,
        operation: Callable[[], T],
    ) -> T:
        """以 bounded retry 執行一個 DB operation。"""

    def _target_still_active(self, target_id: str) -> bool:
        """確認 target 仍可執行。"""

    def _select_scan_page(self, target_kind: TargetKind) -> AsyncScanCallable:
        """依 target kind 選擇 scan callable。"""

    async def _run_scan_with_heartbeat(
        self,
        scan_page: AsyncScanCallable,
        *,
        page: AsyncReusablePageLike,
        app: ApplicationContext,
        target: TargetDescriptor,
        config: TargetConfig,
        scroll_rounds: int,
        scroll_wait_ms: int,
        worker_id: str,
        page_id: str,
        commit_guard: ScanCommitGuard,
    ) -> object:
        """以 heartbeat/timeout 包住 scan callable。"""

    def runtime_restart_requested(self) -> bool:
        """回傳 browser runtime 是否已要求重建。"""

    def request_runtime_restart(self) -> None:
        """要求 browser runtime 重建。"""

    async def _retry_target_after_sqlite_lock(
        self,
        *,
        target_id: str,
        commit_guard: ScanCommitGuard | None,
    ) -> None:
        """SQLite lock 時安排 target 下輪補掃。"""

    async def _register_active_attempt(self, target_id: str, owner_key: str) -> None:
        """登記 active attempt。"""

    async def _unregister_active_attempt(self, target_id: str, owner_key: str) -> None:
        """解除 active attempt 登記。"""


@dataclass
class ResidentQueueAttemptState:
    """保存單次 queue item 執行期間必須由 outer cleanup 看到的狀態。"""

    target_id: str
    page_id: str = ""
    opened: bool = False
    acquired_page: bool = False
    owner_key: str = ""
    commit_guard: ScanCommitGuard | None = None


async def _load_and_admit_target_attempt(
    pool: ResidentExecutorAttemptHost,
    worker_id: str,
    item: QueueItem,
    state: ResidentQueueAttemptState,
) -> ResidentTarget | None:
    """載入 target 並取得 running ownership；失敗時回傳 None 表示本輪 skip。"""

    target_id = state.target_id
    resident_target = await pool._run_db_operation_with_retry(
        "load_resident_target",
        lambda: load_resident_target(pool.options.db_path, target_id),
    )
    if not await pool._run_db_operation_with_retry(
        "target_still_active",
        lambda: pool._target_still_active(target_id),
    ):
        logger.info(
            "resident_target_skipped target_id=%s worker_id=%s reason=%s",
            target_id,
            worker_id,
            "target_not_active_before_running",
        )
        mark_resident_target_idle_if_not_running(pool.options.db_path, target_id)
        return None

    state.page_id = await pool.page_pool.reserve_page_id(target_id)

    def mark_running_operation() -> TargetRuntimeState | None:
        with SqliteApplicationContext(pool.options.db_path) as app:
            return app.services.targets.try_claim_target_running(
                target_id,
                worker_id,
                page_id=state.page_id,
            )

    locked_state = await pool._run_db_operation_with_retry(
        "try_claim_target_running",
        mark_running_operation,
    )
    if locked_state is None:
        logger.info(
            "resident_target_skipped target_id=%s worker_id=%s page_id=%s reason=%s",
            target_id,
            worker_id,
            state.page_id,
            "running_claim_rejected",
        )
        return None
    state.commit_guard = scan_commit_guard_from_runtime_state(locked_state)
    state.owner_key = build_recovery_owner_key(
        worker_id=state.commit_guard.worker_id,
        started_at=state.commit_guard.started_at,
        page_id=state.commit_guard.page_id,
    )
    await pool.target_queue.bind_running_owner(target_id, state.owner_key)
    await pool._register_active_attempt(target_id, state.owner_key)
    pool.schedule_planner.mark_dispatched(item.due_target)
    logger.info(
        "resident_target_running target_id=%s worker_id=%s page_id=%s "
        "owner_key=%s enqueue_reason=%s enqueued_at=%s due_at=%s "
        "scan_requested=%s",
        target_id,
        worker_id,
        state.page_id,
        state.owner_key,
        item.enqueue_reason,
        item.enqueued_at.isoformat(),
        item.due_target.due_at.isoformat(),
        item.due_target.scan_requested,
    )
    return resident_target


async def _prepare_attempt_page(
    pool: ResidentExecutorAttemptHost,
    worker_id: str,
    resident_target: ResidentTarget,
    state: ResidentQueueAttemptState,
) -> AsyncReusablePageLike | None:
    """取得並準備 page；reload owner 已改變時回傳 None 表示本輪 skip。"""

    commit_guard = _require_commit_guard(state)
    page, acquired_page_id, opened = await pool.page_pool.acquire(
        resident_target,
        worker_id,
        page_id=state.page_id,
    )
    state.acquired_page = True
    state.opened = opened
    state.page_id = acquired_page_id
    await prepare_resident_main_page(
        page=page,
        target=resident_target,
        timeout_ms=max(
            pool.options.scan_timeout_seconds,
            PYTHON_SCHEDULER_RUNTIME_DEFAULTS.min_browser_scan_timeout_seconds,
        )
        * 1000,
    )
    reloaded_at = await pool.page_pool.mark_reloaded_if_page_id(
        state.target_id,
        state.page_id,
        current_url=str(getattr(page, "url", "") or ""),
    )

    def mark_reloaded_operation() -> TargetRuntimeState | None:
        with SqliteApplicationContext(pool.options.db_path) as app:
            return app.services.targets.guarded_mark_target_page_reloaded(
                state.target_id,
                worker_id=commit_guard.worker_id,
                started_at=commit_guard.started_at,
                page_id=state.page_id,
                reloaded_at=reloaded_at,
            )

    page_reload_state = await pool._run_db_operation_with_retry(
        "guarded_mark_target_page_reloaded",
        mark_reloaded_operation,
    )
    if page_reload_state is None:
        logger.info(
            "resident_target_skipped target_id=%s worker_id=%s page_id=%s reason=%s",
            state.target_id,
            worker_id,
            state.page_id,
            "page_reload_owner_changed",
        )
        return None
    return page


async def _run_guarded_scan_and_commit_idle(
    pool: ResidentExecutorAttemptHost,
    worker_id: str,
    resident_target: ResidentTarget,
    page: AsyncReusablePageLike,
    state: ResidentQueueAttemptState,
) -> bool:
    """執行 target scan，並在同一 DB context 內做 guarded idle commit。"""

    commit_guard = _require_commit_guard(state)
    with SqliteApplicationContext(pool.options.db_path) as app:
        set_resident_scan_db_busy_timeout(app, RESIDENT_SCAN_DB_BUSY_TIMEOUT_MS)
        selected_scan_page = pool._select_scan_page(resident_target.target.target_kind)
        await pool._run_scan_with_heartbeat(
            selected_scan_page,
            page=page,
            app=app,
            target=resident_target.target,
            config=resident_target.config,
            scroll_rounds=pool.options.scroll_rounds,
            scroll_wait_ms=pool.options.scroll_wait_ms,
            worker_id=worker_id,
            page_id=state.page_id,
            commit_guard=commit_guard,
        )
        return mark_target_idle_for_scan_commit(
            app=app,
            target_id=state.target_id,
            commit_guard=commit_guard,
        )


def _require_commit_guard(state: ResidentQueueAttemptState) -> ScanCommitGuard:
    """回傳已取得的 commit guard；未 claim running 時不得進入後續 phase。"""

    if state.commit_guard is None:
        raise RuntimeError("resident queue attempt has no commit guard")
    return state.commit_guard


async def run_queue_item(
    pool: ResidentExecutorAttemptHost,
    worker_id: str,
    item: QueueItem,
) -> AsyncTargetScanResult:
    """執行 queue 中的單一 target，並維護 runtime / page ownership。"""

    target_id = item.due_target.target_id
    state = ResidentQueueAttemptState(target_id=target_id)
    try:
        resident_target = await _load_and_admit_target_attempt(
            pool,
            worker_id,
            item,
            state,
        )
        if resident_target is None:
            return AsyncTargetScanResult(target_id=target_id, skipped=True)

        page = await _prepare_attempt_page(pool, worker_id, resident_target, state)
        if page is None:
            return AsyncTargetScanResult(target_id=target_id, skipped=True)
        committed_current_attempt = await _run_guarded_scan_and_commit_idle(
            pool,
            worker_id,
            resident_target,
            page,
            state,
        )
        if not committed_current_attempt:
            logger.info(
                "resident_target_skipped target_id=%s worker_id=%s page_id=%s reason=%s",
                target_id,
                worker_id,
                state.page_id,
                "scan_commit_guard_mismatch",
            )
            return AsyncTargetScanResult(target_id=target_id, skipped=True)
        return _successful_attempt_result(worker_id=worker_id, state=state)
    except WorkerFailure as exc:
        return await _record_failure_and_finish(
            pool=pool,
            worker_id=worker_id,
            state=state,
            reason=exc.reason,
            message=str(exc),
            source="worker_failure",
            exception_class=exc.__class__.__name__,
            owner_changed_reason="worker_failure_owner_changed",
            include_page_counts_in_result=True,
        )
    except asyncio.CancelledError:
        if pool.runtime_restart_requested():
            return await _record_failure_and_finish(
                pool=pool,
                worker_id=worker_id,
                state=state,
                reason=SCHEDULER_RUNTIME_REASON,
                message="browser runtime restart requested",
                source="unknown_exception",
                exception_class="CancelledError",
                owner_changed_reason="runtime_restart_cancel_owner_changed",
                request_runtime_restart=False,
                include_page_counts_in_log=False,
            )
        if state.commit_guard is None:
            _finish_pre_admission_failure(
                pool=pool,
                worker_id=worker_id,
                state=state,
                reason="scheduler_cancel_before_running",
                exception_class="CancelledError",
            )
            raise
        await record_guarded_scan_failure_for_db_async(
            db_path=pool.options.db_path,
            target_id=target_id,
            reason=SCHEDULER_STOPPING_REASON,
            message="resident scheduler is stopping",
            source="scheduler_cancel",
            worker_path="resident_main",
            commit_guard=_require_commit_guard(state),
            exception_class="CancelledError",
            page_reused=state.acquired_page and not state.opened,
        )
        raise
    except sqlite3.OperationalError as exc:
        if not is_sqlite_lock_error(exc):
            raise
        return await _retry_after_sqlite_lock_and_skip(
            pool=pool,
            worker_id=worker_id,
            state=state,
            exception_class=exc.__class__.__name__,
        )
    except (AsyncPlaywrightTimeoutError, AsyncPlaywrightError) as exc:
        reason = classify_playwright_exception(exc)
        return await _record_failure_and_finish(
            pool=pool,
            worker_id=worker_id,
            state=state,
            reason=reason,
            message=str(exc),
            source="playwright",
            exception_class=exc.__class__.__name__,
            owner_changed_reason="playwright_failure_owner_changed",
        )
    except Exception as exc:
        reason = classify_wrapped_playwright_exception(exc)
        source: ScanFailureSource = (
            "playwright" if reason != UNKNOWN_REASON else "unknown_exception"
        )
        return await _record_failure_and_finish(
            pool=pool,
            worker_id=worker_id,
            state=state,
            reason=reason,
            message=str(exc),
            source=source,
            exception_class=exc.__class__.__name__,
            owner_changed_reason="unknown_failure_owner_changed",
        )
    finally:
        await pool._unregister_active_attempt(target_id, state.owner_key)
        if state.page_id:
            await pool.page_pool.release_if_page_id(target_id, state.page_id)
        else:
            await pool.page_pool.release(target_id)
        await pool.target_queue.complete(target_id, owner_key=state.owner_key)
        pool.schedule_planner.mark_finished(target_id)


def _successful_attempt_result(
    *,
    worker_id: str,
    state: ResidentQueueAttemptState,
) -> AsyncTargetScanResult:
    """記錄成功完成並建立 executor result。"""

    logger.info(
        "resident_target_finished target_id=%s worker_id=%s page_id=%s "
        "result=%s opened_page=%s reused_page=%s",
        state.target_id,
        worker_id,
        state.page_id,
        "success",
        state.opened,
        not state.opened,
    )
    return AsyncTargetScanResult(
        target_id=state.target_id,
        success=True,
        opened_page=state.opened,
        reused_page=not state.opened,
    )


async def _record_failure_and_finish(
    *,
    pool: ResidentExecutorAttemptHost,
    worker_id: str,
    state: ResidentQueueAttemptState,
    reason: str,
    message: str,
    source: ScanFailureSource,
    exception_class: str,
    owner_changed_reason: str,
    request_runtime_restart: bool = True,
    include_page_counts_in_log: bool = True,
    include_page_counts_in_result: bool = False,
) -> AsyncTargetScanResult:
    """記錄 guarded scan failure，並依 recovery decision 完成本輪結果。"""

    if state.commit_guard is None:
        return _finish_pre_admission_failure(
            pool=pool,
            worker_id=worker_id,
            state=state,
            reason=reason,
            exception_class=exception_class,
        )
    commit_guard = _require_commit_guard(state)
    decision = await record_guarded_scan_failure_for_db_async(
        db_path=pool.options.db_path,
        target_id=state.target_id,
        reason=reason,
        message=message,
        source=source,
        worker_path="resident_main",
        commit_guard=commit_guard,
        exception_class=exception_class,
        page_reused=_attempt_reused_page(state),
    )
    if decision is None:
        logger.info(
            "resident_target_skipped target_id=%s worker_id=%s page_id=%s reason=%s",
            state.target_id,
            worker_id,
            state.page_id,
            owner_changed_reason,
        )
        return AsyncTargetScanResult(target_id=state.target_id, skipped=True)
    if decision.discard_page:
        await pool.page_pool.discard(state.target_id)
    if (
        request_runtime_restart
        and decision.recovery_action == SCHEDULER_RUNTIME_RESTART_ACTION
    ):
        pool.request_runtime_restart()
    _log_failure_decision(
        worker_id=worker_id,
        state=state,
        decision=decision,
        exception_class=exception_class,
        include_page_counts=include_page_counts_in_log,
    )
    if include_page_counts_in_result:
        return AsyncTargetScanResult(
            target_id=state.target_id,
            failure=True,
            opened_page=state.opened,
            reused_page=_attempt_reused_page(state),
        )
    return AsyncTargetScanResult(target_id=state.target_id, failure=True)


def _finish_pre_admission_failure(
    *,
    pool: ResidentExecutorAttemptHost,
    worker_id: str,
    state: ResidentQueueAttemptState,
    reason: str,
    exception_class: str,
) -> AsyncTargetScanResult:
    """claim running 前失敗時，不走 scan finalize 的 unguarded fallback。"""

    mark_resident_target_idle_if_not_running(pool.options.db_path, state.target_id)
    logger.warning(
        "resident_target_skipped target_id=%s worker_id=%s page_id=%s "
        "reason=%s exception_class=%s",
        state.target_id,
        worker_id,
        state.page_id,
        reason,
        exception_class,
    )
    return AsyncTargetScanResult(target_id=state.target_id, skipped=True)


def _log_failure_decision(
    *,
    worker_id: str,
    state: ResidentQueueAttemptState,
    decision: ScanFailureDecision,
    exception_class: str,
    include_page_counts: bool,
) -> None:
    """輸出 resident failure decision log，保持欄位順序穩定。"""

    if not include_page_counts:
        logger.warning(
            "resident_target_finished target_id=%s worker_id=%s page_id=%s "
            "result=%s reason=%s runtime_action=%s recovery_action=%s "
            "retryable=%s retry_streak=%s retry_limit=%s discard_page=%s",
            state.target_id,
            worker_id,
            state.page_id,
            "failure",
            decision.reason,
            decision.runtime_action,
            decision.recovery_action,
            decision.retryable,
            decision.retry_streak,
            decision.retry_limit,
            decision.discard_page,
        )
        return
    logger.warning(
        "resident_target_finished target_id=%s worker_id=%s page_id=%s "
        "result=%s reason=%s runtime_action=%s recovery_action=%s "
        "retryable=%s retry_streak=%s retry_limit=%s discard_page=%s "
        "opened_page=%s reused_page=%s exception_class=%s",
        state.target_id,
        worker_id,
        state.page_id,
        "failure",
        decision.reason,
        decision.runtime_action,
        decision.recovery_action,
        decision.retryable,
        decision.retry_streak,
        decision.retry_limit,
        decision.discard_page,
        state.opened,
        _attempt_reused_page(state),
        exception_class,
    )


async def _retry_after_sqlite_lock_and_skip(
    *,
    pool: ResidentExecutorAttemptHost,
    worker_id: str,
    state: ResidentQueueAttemptState,
    exception_class: str,
) -> AsyncTargetScanResult:
    """SQLite lock 時排回 target 並回傳 skipped result。"""

    try:
        await pool._retry_target_after_sqlite_lock(
            target_id=state.target_id,
            commit_guard=state.commit_guard,
        )
    except sqlite3.OperationalError as retry_exc:
        if not is_sqlite_lock_error(retry_exc):
            raise
        logger.error(
            "resident_target_sqlite_lock_retry_state_update_failed "
            "target_id=%s worker_id=%s page_id=%s exception_class=%s",
            state.target_id,
            worker_id,
            state.page_id,
            retry_exc.__class__.__name__,
        )
    logger.warning(
        "resident_target_finished target_id=%s worker_id=%s page_id=%s "
        "result=%s reason=%s opened_page=%s reused_page=%s exception_class=%s",
        state.target_id,
        worker_id,
        state.page_id,
        "skipped",
        "database_locked",
        state.opened,
        _attempt_reused_page(state),
        exception_class,
    )
    return AsyncTargetScanResult(
        target_id=state.target_id,
        skipped=True,
        opened_page=state.opened,
        reused_page=_attempt_reused_page(state),
    )


def _attempt_reused_page(state: ResidentQueueAttemptState) -> bool:
    """回傳本次 attempt 是否使用既有 page。"""

    return state.acquired_page and not state.opened
