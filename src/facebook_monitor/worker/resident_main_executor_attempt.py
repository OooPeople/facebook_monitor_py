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
from facebook_monitor.core.scan_failures import SCHEDULER_STOPPING_REASON
from facebook_monitor.core.scan_failure_policy import ScanFailureDecision
from facebook_monitor.persistence.sqlite_retry import is_sqlite_lock_error
from facebook_monitor.scheduler.runtime_recovery import build_recovery_owner_key
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.attempt_cleanup import ResidentAttemptCleanupPlan
from facebook_monitor.worker.attempt_cleanup import ResidentAttemptResources
from facebook_monitor.worker.attempt_cleanup import run_resident_attempt_cleanup
from facebook_monitor.worker.attempt_outcomes import ResidentAttemptOutcome
from facebook_monitor.worker.attempt_outcomes import ResidentAttemptOutcomeKind
from facebook_monitor.worker.attempt_transitions import ResidentAttemptTerminalTransition
from facebook_monitor.worker.attempt_transitions import transition_from_attempt_outcome
from facebook_monitor.worker.attempt_transitions import transition_from_scan_commit_outcome
from facebook_monitor.worker.resident_main_executor_types import AsyncReusablePageLike
from facebook_monitor.worker.resident_main_executor_types import (
    AsyncCommitReadyScanCallable,
)
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
from facebook_monitor.worker.resident_failure_decisions import (
    decide_resident_failure_attempt,
)
from facebook_monitor.worker.resident_failure_decisions import ResidentFailureAttemptDecision
from facebook_monitor.worker.resident_failure_decisions import (
    failure_record_decision_for_playwright_exception,
)
from facebook_monitor.worker.resident_failure_decisions import (
    failure_record_decision_for_runtime_restart_cancellation,
)
from facebook_monitor.worker.resident_failure_decisions import (
    failure_record_decision_for_unknown_exception,
)
from facebook_monitor.worker.resident_failure_decisions import (
    failure_record_decision_for_worker_failure,
)
from facebook_monitor.worker.resident_failure_decisions import ResidentFailureRecordDecision
from facebook_monitor.worker.scan_commit_coordinator import commit_failure_request_for_db_async
from facebook_monitor.worker.scan_commit_coordinator import commit_guarded_protective_skip
from facebook_monitor.worker.scan_commit_coordinator import commit_success
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcome
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcomeKind
from facebook_monitor.worker.scan_commit_requests import FailureScanCommitRequest
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import scan_commit_guard_from_runtime_state
from facebook_monitor.worker.scan_pipeline_results import FormalAsyncScanResult
from facebook_monitor.worker.scan_pipeline_results import ProtectiveSkipScanResult
from facebook_monitor.worker.scan_pipeline_results import SuccessScanResult


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

    def _select_scan_page(self, target_kind: TargetKind) -> AsyncCommitReadyScanCallable:
        """依 target kind 選擇 scan callable。"""

    async def _run_scan_with_heartbeat(
        self,
        scan_page: AsyncCommitReadyScanCallable,
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
    ) -> FormalAsyncScanResult:
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
    active_attempt_key: str = ""
    planner_dispatch_id: str = ""
    commit_guard: ScanCommitGuard | None = None

    def cleanup_plan(self) -> ResidentAttemptCleanupPlan:
        """依目前取得的 resources 推導 cleanup plan。"""

        return ResidentAttemptCleanupPlan.from_resources(
            target_id=self.target_id,
            resources=ResidentAttemptResources(
                queue_item_consumed=True,
                queue_owner_key=self.owner_key,
                active_attempt_key=self.active_attempt_key,
                page_id=self.page_id,
                page_acquired=self.acquired_page,
                planner_dispatch_id=self.planner_dispatch_id,
            ),
        )


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
    state.active_attempt_key = state.owner_key
    pool.schedule_planner.mark_dispatched(item.due_target)
    state.planner_dispatch_id = target_id
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
) -> ScanCommitOutcome:
    """執行 target scan，並依 scanner result 做 guarded commit。"""

    commit_guard = _require_commit_guard(state)
    with SqliteApplicationContext(pool.options.db_path) as app:
        set_resident_scan_db_busy_timeout(app, RESIDENT_SCAN_DB_BUSY_TIMEOUT_MS)
        selected_scan_page = pool._select_scan_page(resident_target.target.target_kind)
        scan_result = await pool._run_scan_with_heartbeat(
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
        commit_ready_result = _require_formal_async_scan_result(scan_result)
        if isinstance(commit_ready_result, ProtectiveSkipScanResult):
            return commit_guarded_protective_skip(
                app=app,
                target=resident_target.target,
                result=commit_ready_result,
                commit_guard=commit_guard,
            )
        return commit_success(
            app=app,
            target=resident_target.target,
            config=resident_target.config,
            result=commit_ready_result,
            commit_guard=commit_guard,
        )


def _require_formal_async_scan_result(result: object) -> FormalAsyncScanResult:
    """正式 async resident path 只接受 commit-ready scanner result。"""

    if isinstance(result, (SuccessScanResult, ProtectiveSkipScanResult)):
        return result
    raise TypeError(
        "formal async resident scanner must return SuccessScanResult "
        f"or ProtectiveSkipScanResult, got {type(result).__name__}"
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
    cleanup_plan: ResidentAttemptCleanupPlan | None = None
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
        commit_outcome = await _run_guarded_scan_and_commit_idle(
            pool,
            worker_id,
            resident_target,
            page,
            state,
        )
        scan_result, cleanup_plan = _finish_scan_commit_outcome(
            worker_id=worker_id,
            state=state,
            commit_outcome=commit_outcome,
        )
        return scan_result
    except WorkerFailure as exc:
        transition = await _record_failure_and_finish(
            pool=pool,
            worker_id=worker_id,
            state=state,
            failure_record_decision=failure_record_decision_for_worker_failure(exc),
        )
        cleanup_plan = transition.cleanup_plan
        return transition.outcome.to_scan_result()
    except asyncio.CancelledError:
        if pool.runtime_restart_requested():
            transition = await _record_failure_and_finish(
                pool=pool,
                worker_id=worker_id,
                state=state,
                failure_record_decision=(
                    failure_record_decision_for_runtime_restart_cancellation()
                ),
            )
            cleanup_plan = transition.cleanup_plan
            return transition.outcome.to_scan_result()
        if state.commit_guard is None:
            transition = _finish_pre_admission_failure(
                pool=pool,
                worker_id=worker_id,
                state=state,
                reason="scheduler_cancel_before_running",
                exception_class="CancelledError",
                kind=ResidentAttemptOutcomeKind.CANCELLED,
            )
            cleanup_plan = transition.cleanup_plan
            raise
        transition = await _record_scheduler_stopping_cancellation(
            pool=pool,
            state=state,
        )
        cleanup_plan = transition.cleanup_plan
        raise
    except sqlite3.OperationalError as exc:
        if not is_sqlite_lock_error(exc):
            raise
        transition = await _retry_after_sqlite_lock_and_skip(
            pool=pool,
            worker_id=worker_id,
            state=state,
            exception_class=exc.__class__.__name__,
        )
        cleanup_plan = transition.cleanup_plan
        return transition.outcome.to_scan_result()
    except (AsyncPlaywrightTimeoutError, AsyncPlaywrightError) as exc:
        transition = await _record_failure_and_finish(
            pool=pool,
            worker_id=worker_id,
            state=state,
            failure_record_decision=failure_record_decision_for_playwright_exception(exc),
        )
        cleanup_plan = transition.cleanup_plan
        return transition.outcome.to_scan_result()
    except Exception as exc:
        transition = await _record_failure_and_finish(
            pool=pool,
            worker_id=worker_id,
            state=state,
            failure_record_decision=failure_record_decision_for_unknown_exception(exc),
        )
        cleanup_plan = transition.cleanup_plan
        return transition.outcome.to_scan_result()
    finally:
        await run_resident_attempt_cleanup(
            pool,
            cleanup_plan or state.cleanup_plan(),
        )


def _finish_scan_commit_outcome(
    *,
    worker_id: str,
    state: ResidentQueueAttemptState,
    commit_outcome: ScanCommitOutcome,
) -> tuple[AsyncTargetScanResult, ResidentAttemptCleanupPlan]:
    """將 scan commit outcome 映射成既有 executor result。"""

    transition = transition_from_scan_commit_outcome(
        target_id=state.target_id,
        commit_outcome=commit_outcome,
        opened_page=state.opened,
        reused_page=not state.opened,
    )
    if transition.outcome.kind != ResidentAttemptOutcomeKind.SUCCEEDED:
        logger.info(
            "resident_target_skipped target_id=%s worker_id=%s page_id=%s reason=%s",
            state.target_id,
            worker_id,
            state.page_id,
            transition.outcome.reason or "scan_commit_guard_mismatch",
        )
        return transition.outcome.to_scan_result(), state.cleanup_plan()
    return (
        _successful_attempt_result(worker_id=worker_id, state=state),
        state.cleanup_plan(),
    )


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
    return ResidentAttemptOutcome.succeeded(
        target_id=state.target_id,
        opened_page=state.opened,
        reused_page=not state.opened,
    ).to_scan_result()


async def _record_failure_and_finish(
    *,
    pool: ResidentExecutorAttemptHost,
    worker_id: str,
    state: ResidentQueueAttemptState,
    failure_record_decision: ResidentFailureRecordDecision,
) -> ResidentAttemptTerminalTransition:
    """記錄 guarded scan failure，並依 recovery decision 完成本輪結果。"""

    if state.commit_guard is None:
        return _finish_pre_admission_failure(
            pool=pool,
            worker_id=worker_id,
            state=state,
            reason=failure_record_decision.reason,
            exception_class=failure_record_decision.exception_class,
        )
    commit_outcome = await _commit_failure_record_decision(
        pool=pool,
        state=state,
        failure_record_decision=failure_record_decision,
    )
    failure_attempt_decision = decide_resident_failure_attempt(
        target_id=state.target_id,
        commit_outcome=commit_outcome,
        owner_changed_reason=failure_record_decision.owner_changed_reason,
        source=failure_record_decision.source,
        exception_class=failure_record_decision.exception_class,
        request_runtime_restart=failure_record_decision.request_runtime_restart,
        opened_page=state.opened,
        reused_page=_attempt_reused_page(state),
        include_page_counts_in_result=(failure_record_decision.include_page_counts_in_result),
    )
    return await _finish_failure_attempt_decision(
        pool=pool,
        worker_id=worker_id,
        state=state,
        failure_record_decision=failure_record_decision,
        failure_attempt_decision=failure_attempt_decision,
    )


async def _commit_failure_record_decision(
    *,
    pool: ResidentExecutorAttemptHost,
    state: ResidentQueueAttemptState,
    failure_record_decision: ResidentFailureRecordDecision,
) -> ScanCommitOutcome:
    """依 pure failure record decision 執行 guarded failure commit。"""

    return await commit_failure_request_for_db_async(
        FailureScanCommitRequest(
            db_path=pool.options.db_path,
            target_id=state.target_id,
            reason=failure_record_decision.reason,
            message=failure_record_decision.message,
            source=failure_record_decision.source,
            worker_path="resident_main",
            commit_guard=_require_commit_guard(state),
            exception_class=failure_record_decision.exception_class,
            page_reused=_attempt_reused_page(state),
        )
    )


async def _finish_failure_attempt_decision(
    *,
    pool: ResidentExecutorAttemptHost,
    worker_id: str,
    state: ResidentQueueAttemptState,
    failure_record_decision: ResidentFailureRecordDecision,
    failure_attempt_decision: ResidentFailureAttemptDecision,
) -> ResidentAttemptTerminalTransition:
    """依 failure attempt decision 套用 page/runtime side effects 並回傳 transition。"""

    decision = failure_attempt_decision.failure_decision
    if failure_attempt_decision.owner_changed:
        logger.info(
            "resident_target_skipped target_id=%s worker_id=%s page_id=%s reason=%s",
            state.target_id,
            worker_id,
            state.page_id,
            failure_attempt_decision.outcome.reason,
        )
        transition = transition_from_attempt_outcome(
            target_id=state.target_id,
            outcome=failure_attempt_decision.outcome,
        )
        return _with_state_cleanup(state, transition)
    if decision is None:
        raise RuntimeError("owner_changed failure decision must return before side effects")
    if failure_attempt_decision.discard_page:
        await _discard_failed_attempt_page(pool, state)
    if failure_attempt_decision.request_runtime_restart:
        pool.request_runtime_restart()
    _log_failure_decision(
        worker_id=worker_id,
        state=state,
        decision=decision,
        exception_class=failure_record_decision.exception_class,
        include_page_counts=failure_record_decision.include_page_counts_in_log,
    )
    transition = transition_from_attempt_outcome(
        target_id=state.target_id,
        outcome=failure_attempt_decision.outcome,
    )
    return _with_state_cleanup(state, transition)


async def _record_scheduler_stopping_cancellation(
    *,
    pool: ResidentExecutorAttemptHost,
    state: ResidentQueueAttemptState,
) -> ResidentAttemptTerminalTransition:
    """保留 scheduler stopping guarded failure + re-raise 前的 transition。"""

    commit_outcome = await commit_failure_request_for_db_async(
        FailureScanCommitRequest(
            db_path=pool.options.db_path,
            target_id=state.target_id,
            reason=SCHEDULER_STOPPING_REASON,
            message="resident scheduler is stopping",
            source="scheduler_cancel",
            worker_path="resident_main",
            commit_guard=_require_commit_guard(state),
            exception_class="CancelledError",
            page_reused=state.acquired_page and not state.opened,
        )
    )
    transition = transition_from_scan_commit_outcome(
        target_id=state.target_id,
        commit_outcome=commit_outcome,
        opened_page=False,
        reused_page=False,
    )
    return _with_state_cleanup(state, transition)


async def _discard_failed_attempt_page(
    pool: ResidentExecutorAttemptHost,
    state: ResidentQueueAttemptState,
) -> None:
    """依 page id guard 丟棄失敗 page，避免舊 attempt 關掉新 page。"""

    if state.page_id:
        await pool.page_pool.discard_if_page_id(state.target_id, state.page_id)
        return
    await pool.page_pool.discard(state.target_id)


def _finish_pre_admission_failure(
    *,
    pool: ResidentExecutorAttemptHost,
    worker_id: str,
    state: ResidentQueueAttemptState,
    reason: str,
    exception_class: str,
    kind: ResidentAttemptOutcomeKind = ResidentAttemptOutcomeKind.TARGET_INACTIVE,
) -> ResidentAttemptTerminalTransition:
    """claim running 前失敗時，不走 scan finalize 的 unguarded fallback。"""

    mark_resident_target_idle_if_not_running(pool.options.db_path, state.target_id)
    logger.warning(
        "resident_target_skipped target_id=%s worker_id=%s page_id=%s reason=%s exception_class=%s",
        state.target_id,
        worker_id,
        state.page_id,
        reason,
        exception_class,
    )
    transition = transition_from_attempt_outcome(
        target_id=state.target_id,
        outcome=ResidentAttemptOutcome.skipped(
            target_id=state.target_id,
            kind=kind,
            reason=reason,
        ),
    )
    return _with_state_cleanup(state, transition)


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
) -> ResidentAttemptTerminalTransition:
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
    transition = transition_from_scan_commit_outcome(
        target_id=state.target_id,
        commit_outcome=ScanCommitOutcome(
            kind=ScanCommitOutcomeKind.SQLITE_LOCK_RETRY,
            target_id=state.target_id,
            reason="database_locked",
        ),
        opened_page=state.opened,
        reused_page=_attempt_reused_page(state),
    )
    return _with_state_cleanup(state, transition)


def _with_state_cleanup(
    state: ResidentQueueAttemptState,
    transition: ResidentAttemptTerminalTransition,
) -> ResidentAttemptTerminalTransition:
    """保留 terminal outcome，改用 attempt resource tokens 推導 cleanup。"""

    return ResidentAttemptTerminalTransition(
        outcome=transition.outcome,
        cleanup_plan=state.cleanup_plan(),
    )


def _attempt_reused_page(state: ResidentQueueAttemptState) -> bool:
    """回傳本次 attempt 是否使用既有 page。"""

    return state.acquired_page and not state.opened
