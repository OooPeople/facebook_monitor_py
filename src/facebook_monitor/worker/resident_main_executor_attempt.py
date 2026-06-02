"""Single-target execution path for the resident executor."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any

from playwright.async_api import Error as AsyncPlaywrightError
from playwright.async_api import TimeoutError as AsyncPlaywrightTimeoutError

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_STOPPING_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.core.scan_failure_policy import SCHEDULER_RUNTIME_RESTART_ACTION
from facebook_monitor.persistence.sqlite_retry import is_sqlite_lock_error
from facebook_monitor.scheduler.runtime_recovery import build_recovery_owner_key
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.errors import classify_playwright_exception
from facebook_monitor.worker.resident_main_executor_types import AsyncTargetScanResult
from facebook_monitor.worker.resident_main_page_prepare import _RESIDENT_SCAN_DB_BUSY_TIMEOUT_MS
from facebook_monitor.worker.resident_main_page_prepare import _set_resident_scan_db_busy_timeout
from facebook_monitor.worker.resident_main_page_prepare import prepare_resident_main_page
from facebook_monitor.worker.resident_main_queue import QueueItem
from facebook_monitor.worker.resident_shared import load_resident_target
from facebook_monitor.worker.resident_shared import mark_resident_target_idle
from facebook_monitor.worker.scan_failure_finalize import record_guarded_scan_failure_for_db_async
from facebook_monitor.worker.scan_finalize import mark_target_idle_for_scan_commit
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import scan_commit_guard_from_runtime_state


logger = logging.getLogger(__name__)


async def run_queue_item(pool: Any, worker_id: str, item: QueueItem) -> AsyncTargetScanResult:
    """執行 queue 中的單一 target，並維護 runtime / page ownership。"""

    target_id = item.due_target.target_id
    opened = False
    page_id = ""
    acquired_page = False
    owner_key = ""
    commit_guard: ScanCommitGuard | None = None
    try:
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
            mark_resident_target_idle(pool.options.db_path, target_id)
            return AsyncTargetScanResult(target_id=target_id, skipped=True)

        page_id = await pool.page_pool.reserve_page_id(target_id)

        def mark_running_operation() -> Any:
            with SqliteApplicationContext(pool.options.db_path) as app:
                return app.services.targets.try_mark_target_running(
                    target_id,
                    worker_id,
                    page_id=page_id,
                )

        locked_state = await pool._run_db_operation_with_retry(
            "try_mark_target_running",
            mark_running_operation,
        )
        if locked_state is None:
            logger.info(
                "resident_target_skipped target_id=%s worker_id=%s page_id=%s reason=%s",
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
        await pool.target_queue.bind_running_owner(target_id, owner_key)
        await pool._register_active_attempt(target_id, owner_key)
        pool.schedule_planner.mark_dispatched(item.due_target)
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

        page, acquired_page_id, opened = await pool.page_pool.acquire(
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
                pool.options.scan_timeout_seconds,
                PYTHON_SCHEDULER_RUNTIME_DEFAULTS.min_browser_scan_timeout_seconds,
            )
            * 1000,
        )
        reloaded_at = await pool.page_pool.mark_reloaded_if_page_id(
            target_id,
            page_id,
            current_url=str(getattr(page, "url", "") or ""),
        )

        def mark_reloaded_operation() -> Any:
            with SqliteApplicationContext(pool.options.db_path) as app:
                return app.services.targets.mark_target_page_reloaded_if_owner(
                    target_id,
                    worker_id=commit_guard.worker_id,
                    started_at=commit_guard.started_at,
                    page_id=page_id,
                    reloaded_at=reloaded_at,
                )

        page_reload_state = await pool._run_db_operation_with_retry(
            "mark_target_page_reloaded_if_owner",
            mark_reloaded_operation,
        )
        if page_reload_state is None:
            logger.info(
                "resident_target_skipped target_id=%s worker_id=%s page_id=%s reason=%s",
                target_id,
                worker_id,
                page_id,
                "page_reload_owner_changed",
            )
            return AsyncTargetScanResult(target_id=target_id, skipped=True)
        with SqliteApplicationContext(pool.options.db_path) as app:
            _set_resident_scan_db_busy_timeout(app, _RESIDENT_SCAN_DB_BUSY_TIMEOUT_MS)
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
                "resident_target_skipped target_id=%s worker_id=%s page_id=%s reason=%s",
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
        decision = await record_guarded_scan_failure_for_db_async(
            db_path=pool.options.db_path,
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
                "resident_target_skipped target_id=%s worker_id=%s page_id=%s reason=%s",
                target_id,
                worker_id,
                page_id,
                "worker_failure_owner_changed",
            )
            return AsyncTargetScanResult(target_id=target_id, skipped=True)
        if decision.discard_page:
            await pool.page_pool.discard(target_id)
        if decision.recovery_action == SCHEDULER_RUNTIME_RESTART_ACTION:
            pool.request_runtime_restart()
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
        if pool.runtime_restart_requested():
            decision = await record_guarded_scan_failure_for_db_async(
                db_path=pool.options.db_path,
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
                    "resident_target_skipped target_id=%s worker_id=%s page_id=%s reason=%s",
                    target_id,
                    worker_id,
                    page_id,
                    "runtime_restart_cancel_owner_changed",
                )
                return AsyncTargetScanResult(target_id=target_id, skipped=True)
            if decision.discard_page:
                await pool.page_pool.discard(target_id)
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
        await record_guarded_scan_failure_for_db_async(
            db_path=pool.options.db_path,
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
    except sqlite3.OperationalError as exc:
        if not is_sqlite_lock_error(exc):
            raise
        try:
            await pool._retry_target_after_sqlite_lock(
                target_id=target_id,
                commit_guard=commit_guard,
            )
        except sqlite3.OperationalError as retry_exc:
            if not is_sqlite_lock_error(retry_exc):
                raise
            logger.error(
                "resident_target_sqlite_lock_retry_state_update_failed "
                "target_id=%s worker_id=%s page_id=%s exception_class=%s",
                target_id,
                worker_id,
                page_id,
                retry_exc.__class__.__name__,
            )
        logger.warning(
            "resident_target_finished target_id=%s worker_id=%s page_id=%s "
            "result=%s reason=%s opened_page=%s reused_page=%s exception_class=%s",
            target_id,
            worker_id,
            page_id,
            "skipped",
            "database_locked",
            opened,
            acquired_page and not opened,
            exc.__class__.__name__,
        )
        return AsyncTargetScanResult(
            target_id=target_id,
            skipped=True,
            opened_page=opened,
            reused_page=acquired_page and not opened,
        )
    except (AsyncPlaywrightTimeoutError, AsyncPlaywrightError) as exc:
        reason = classify_playwright_exception(exc)
        decision = await record_guarded_scan_failure_for_db_async(
            db_path=pool.options.db_path,
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
                "resident_target_skipped target_id=%s worker_id=%s page_id=%s reason=%s",
                target_id,
                worker_id,
                page_id,
                "playwright_failure_owner_changed",
            )
            return AsyncTargetScanResult(target_id=target_id, skipped=True)
        if decision.discard_page:
            await pool.page_pool.discard(target_id)
        if decision.recovery_action == SCHEDULER_RUNTIME_RESTART_ACTION:
            pool.request_runtime_restart()
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
        decision = await record_guarded_scan_failure_for_db_async(
            db_path=pool.options.db_path,
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
                "resident_target_skipped target_id=%s worker_id=%s page_id=%s reason=%s",
                target_id,
                worker_id,
                page_id,
                "unknown_failure_owner_changed",
            )
            return AsyncTargetScanResult(target_id=target_id, skipped=True)
        if decision.discard_page:
            await pool.page_pool.discard(target_id)
        if decision.recovery_action == SCHEDULER_RUNTIME_RESTART_ACTION:
            pool.request_runtime_restart()
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
        await pool._unregister_active_attempt(target_id, owner_key)
        if page_id:
            await pool.page_pool.release_if_page_id(target_id, page_id)
        else:
            await pool.page_pool.release(target_id)
        await pool.target_queue.complete(target_id, owner_key=owner_key)
        pool.schedule_planner.mark_finished(target_id)
