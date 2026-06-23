"""Resident maintenance jobs 共用的 runtime guard 與例外處理。"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
import logging

from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.worker.errors import classify_playwright_exception
from facebook_monitor.worker.errors import classify_wrapped_playwright_exception
from facebook_monitor.worker.playwright_runtime_errors import (
    is_playwright_runtime_closed_exception,
)
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.scan_failure_finalize import (
    record_guarded_scan_failure_decision_for_db,
)


logger = logging.getLogger(__name__)
StopCheckCallable = Callable[[], bool]


def handle_maintenance_refresh_exception(
    *,
    options: ResidentRuntimeOptions,
    target_id: str,
    exc: Exception,
    stop_requested: StopCheckCallable,
    request_runtime_restart: Callable[[], None] | None,
    shutdown_log_message: str,
    runtime_restart_log_message: str,
    failure_log_message: str,
    mark_failed: Callable[[], None],
) -> bool:
    """處理 maintenance refresh 共用例外分支；回傳是否應中止本輪。"""

    if should_skip_refresh_failure_for_shutdown(exc, stop_requested):
        logger.info(
            shutdown_log_message,
            extra={"target_id": target_id},
        )
        return True
    if is_scheduler_runtime_refresh_failure(exc):
        logger.warning(
            runtime_restart_log_message,
            extra={"target_id": target_id},
        )
        recorded_failure = record_refresh_runtime_failure(
            options=options,
            target_id=target_id,
            exc=exc,
        )
        if recorded_failure and request_runtime_restart is not None:
            request_runtime_restart()
        return True
    logger.exception(
        failure_log_message,
        extra={"target_id": target_id},
    )
    mark_failed()
    return False


def record_refresh_runtime_failure(
    *,
    options: ResidentRuntimeOptions,
    target_id: str,
    exc: Exception,
) -> bool:
    """將 maintenance refresh 的 browser runtime failure 接回 scan failure policy。"""

    exception_class, message = runtime_refresh_failure_detail(exc)
    decision = record_guarded_scan_failure_decision_for_db(
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


def runtime_refresh_failure_detail(exc: Exception) -> tuple[str, str]:
    """取出最接近 Playwright runtime closed 的 exception 類型與訊息。"""

    for current in _iter_exception_chain(exc):
        if isinstance(current, Exception) and (
            classify_playwright_exception(current) == SCHEDULER_RUNTIME_REASON
            or classify_wrapped_playwright_exception(current) == SCHEDULER_RUNTIME_REASON
            or is_playwright_runtime_closed_exception(current)
        ):
            return current.__class__.__name__, format_exception_message(current)
    return exc.__class__.__name__, format_exception_message(exc)


def should_skip_refresh_failure_for_shutdown(
    exc: Exception,
    should_stop: StopCheckCallable,
) -> bool:
    """停止流程中 Playwright runtime 關閉不應污染 maintenance job 診斷。"""

    return should_stop() and any(
        isinstance(current, Exception) and is_playwright_runtime_closed_exception(current)
        for current in _iter_exception_chain(exc)
    )


def is_scheduler_runtime_refresh_failure(exc: Exception) -> bool:
    """判斷 metadata/cover refresh 失敗是否代表 browser runtime 已損壞。"""

    for current in _iter_exception_chain(exc):
        if isinstance(current, Exception):
            if classify_playwright_exception(current) == SCHEDULER_RUNTIME_REASON:
                return True
            if classify_wrapped_playwright_exception(current) == SCHEDULER_RUNTIME_REASON:
                return True
            if is_playwright_runtime_closed_exception(current):
                return True
    return False


def _iter_exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """沿著 cause/context 走訪 exception chain，避免 wrapper 隱藏 runtime closed。"""

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def format_exception_message(exc: Exception) -> str:
    """保留非預期例外類型，讓 maintenance 診斷可回查真正原因。"""

    message = str(exc).strip()
    if message:
        return f"{exc.__class__.__name__}: {message}"
    return exc.__class__.__name__


__all__ = [
    "StopCheckCallable",
    "format_exception_message",
    "handle_maintenance_refresh_exception",
    "is_scheduler_runtime_refresh_failure",
    "record_refresh_runtime_failure",
    "runtime_refresh_failure_detail",
    "should_skip_refresh_failure_for_shutdown",
]
