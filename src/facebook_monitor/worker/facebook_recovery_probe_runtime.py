"""Recovery probe 的共用 deadline、分類與 bounded teardown 工具。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from typing import AsyncIterator
from urllib.parse import quote

from playwright.async_api import async_playwright

from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions
from facebook_monitor.automation.browser_runtime import launch_persistent_context_async
from facebook_monitor.core.defaults import PYTHON_FACEBOOK_ACCESS_DEFAULTS
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookProbeFailureStage
from facebook_monitor.core.facebook_access import FacebookProbeResult
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookRecoveryRecipeKind
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.scan_failures import CONTENT_UNAVAILABLE_REASON
from facebook_monitor.core.scan_failures import FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.core.scan_failures import PAGE_LOAD_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import PROFILE_LOCKED_REASON
from facebook_monitor.core.scan_failures import PROFILE_SESSION_FAILURE_REASONS
from facebook_monitor.core.scan_failures import SCAN_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.facebook.group_metadata_validation import (
    final_url_matches_expected_group,
)
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.scan_orchestration import ensure_async_page_scannable


_SAFE_PROBE_FAILURE_REASONS = frozenset(
    {
        CONTENT_UNAVAILABLE_REASON,
        FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON,
        FACEBOOK_TEMPORARY_BLOCK_REASON,
        PAGE_LOAD_TIMEOUT_REASON,
        PROFILE_LOCKED_REASON,
        SCAN_TIMEOUT_REASON,
        SCHEDULER_RUNTIME_REASON,
        UNKNOWN_REASON,
    }
) | PROFILE_SESSION_FAILURE_REASONS


@dataclass(frozen=True)
class FacebookRecoveryProbeRecipe:
    """Approved probe 的產品意圖、action budget 與 timeout policy。"""

    operation_kind: FacebookProductOperationKind
    action_kind: FacebookActionKind
    absolute_deadline_seconds: float = (
        PYTHON_FACEBOOK_ACCESS_DEFAULTS.probe_absolute_deadline_seconds
    )
    cleanup_grace_seconds: float = (
        PYTHON_FACEBOOK_ACCESS_DEFAULTS.probe_cleanup_grace_seconds
    )


APPROVED_FACEBOOK_RECOVERY_PROBE_RECIPES = {
    FacebookRecoveryRecipeKind.GROUP_FEED_DOCUMENT_GUARD_V1: (
        FacebookRecoveryProbeRecipe(
            FacebookProductOperationKind.POSTS_ACCESS,
            FacebookActionKind.GROUP_FEED_DOCUMENT,
        )
    ),
    FacebookRecoveryRecipeKind.GROUP_DOCUMENT_GUARD_V1: FacebookRecoveryProbeRecipe(
        FacebookProductOperationKind.GROUP_METADATA_ACCESS,
        FacebookActionKind.GROUP_DOCUMENT,
    ),
    FacebookRecoveryRecipeKind.GROUP_COVER_GUARD_V1: FacebookRecoveryProbeRecipe(
        FacebookProductOperationKind.COVER_METADATA_ACCESS,
        FacebookActionKind.GROUP_DOCUMENT,
    ),
}


@dataclass(frozen=True)
class FacebookProbeDeadline:
    """以 monotonic clock 保存 claim 後不可延長的單一 absolute deadline。"""

    expires_at: float
    monotonic: Callable[[], float]

    def remaining_seconds(self) -> float:
        """回傳目前剩餘 budget；不得由 wall clock 跳動延長。"""

        return max(self.expires_at - self.monotonic(), 0.0)

    def driver_timeout_seconds(self, maximum_seconds: float) -> float:
        """回傳不超過 absolute deadline 的 Playwright driver timeout。"""

        remaining = self.remaining_seconds()
        if remaining <= 0:
            raise TimeoutError
        return min(max(float(maximum_seconds), 0.0), remaining)

    @asynccontextmanager
    async def enforce(self) -> AsyncIterator[None]:
        """以目前剩餘 budget 約束完整 resource/browser observation。"""

        remaining = self.remaining_seconds()
        if remaining <= 0:
            raise TimeoutError
        async with asyncio.timeout(remaining):
            yield


def build_facebook_probe_deadline(
    *,
    configured_timeout_seconds: float,
    lease_expires_at: datetime | None,
    wall_now: datetime,
    monotonic: Callable[[], float] | None = None,
) -> FacebookProbeDeadline:
    """把 durable lease 剩餘時間一次換算為 monotonic deadline。"""

    monotonic_clock = monotonic or asyncio.get_running_loop().time
    configured_budget = max(float(configured_timeout_seconds), 0.0)
    lease_budget = configured_budget
    if lease_expires_at is not None:
        lease_budget = max((lease_expires_at - wall_now).total_seconds(), 0.0)
    budget = min(configured_budget, lease_budget)
    return FacebookProbeDeadline(
        expires_at=monotonic_clock() + budget,
        monotonic=monotonic_clock,
    )


def unexpected_probe_failure_reason(stage: FacebookProbeFailureStage) -> str:
    """把例外發生階段映射為既有、privacy-safe 的 stable reason。"""

    if stage == FacebookProbeFailureStage.NAVIGATION:
        return PAGE_LOAD_TIMEOUT_REASON
    if stage in {
        FacebookProbeFailureStage.PAGE_GUARD,
        FacebookProbeFailureStage.ROUTE_IDENTITY,
    }:
        return FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON
    return SCHEDULER_RUNTIME_REASON


def normalize_probe_failure_reason(reason: str) -> str:
    """只允許固定 reason code 離開 browser boundary。"""

    normalized = str(reason or "").strip()
    return normalized if normalized in _SAFE_PROBE_FAILURE_REASONS else UNKNOWN_REASON


@dataclass(frozen=True)
class FacebookProbeCleanupResult:
    """Browser cleanup 是否完整完成，以及實際 context 是否已關閉。"""

    completed: bool
    context_closed: bool


async def close_probe_browser_resources(
    *,
    browser_context: Any | None,
    playwright_manager: Any | None,
    playwright_started: bool,
    timeout_seconds: float,
) -> FacebookProbeCleanupResult:
    """在 hard monotonic grace 內分別嘗試關閉 context 與 manager。"""

    if browser_context is None and not playwright_started:
        return FacebookProbeCleanupResult(completed=True, context_closed=False)

    loop = asyncio.get_running_loop()
    expires_at = loop.time() + max(float(timeout_seconds), 0.01)
    context_closed = False
    completed = True
    operations: list[Callable[[], Awaitable[None]]] = []
    if browser_context is not None:
        operations.append(browser_context.close)
    if playwright_started and playwright_manager is not None:
        operations.append(lambda: playwright_manager.__aexit__(None, None, None))

    for index, operation in enumerate(operations):
        remaining = max(expires_at - loop.time(), 0.0)
        remaining_steps = len(operations) - index
        step_budget = remaining / remaining_steps if remaining_steps else 0.0
        succeeded = await _run_hard_bounded_cleanup_step(
            operation,
            timeout_seconds=step_budget,
        )
        completed = completed and succeeded
        if index == 0 and browser_context is not None and succeeded:
            context_closed = True

    return FacebookProbeCleanupResult(
        completed=completed,
        context_closed=context_closed,
    )


async def _run_hard_bounded_cleanup_step(
    operation: Callable[[], Awaitable[None]],
    *,
    timeout_seconds: float,
) -> bool:
    """Timeout 後只送 cancellation，不再無界等待 inner task 收旂。"""

    if timeout_seconds <= 0:
        return False
    task: asyncio.Future[None] = asyncio.ensure_future(operation())
    try:
        done, _pending = await asyncio.wait({task}, timeout=timeout_seconds)
    except asyncio.CancelledError:
        task.cancel()
        asyncio.get_running_loop().call_soon(task.cancel)
        task.add_done_callback(_consume_cleanup_task_result)
        raise
    if task not in done:
        task.cancel()
        asyncio.get_running_loop().call_soon(task.cancel)
        task.add_done_callback(_consume_cleanup_task_result)
        return False
    try:
        task.result()
    except (asyncio.CancelledError, Exception):
        return False
    return True


def _consume_cleanup_task_result(task: asyncio.Future[None]) -> None:
    """消化 hard-bound 後才完成的 cleanup task，不輸出 raw exception。"""

    try:
        task.result()
    except (asyncio.CancelledError, Exception):
        pass


async def _close_probe_browser_resources_despite_cancellation(
    *,
    browser_context: Any | None,
    playwright_manager: Any | None,
    playwright_started: bool,
    timeout_seconds: float,
) -> tuple[FacebookProbeCleanupResult, bool]:
    """外部取消只記錄意圖，cleanup仍依自己的hard grace完成。"""

    cleanup_task = asyncio.create_task(
        close_probe_browser_resources(
            browser_context=browser_context,
            playwright_manager=playwright_manager,
            playwright_started=playwright_started,
            timeout_seconds=timeout_seconds,
        )
    )
    cancelled = False
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            cancelled = True
    return cleanup_task.result(), cancelled


async def cancel_probe_monitor_task(task: asyncio.Task[Any]) -> bool:
    """取消probe monitor並等到terminal；額外取消只回傳給owner延後傳遞。"""

    owner = asyncio.current_task()
    observed_cancellation_count = owner.cancelling() if owner is not None else 0
    externally_cancelled = False
    if not task.done():
        task.cancel()
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            current_count = owner.cancelling() if owner is not None else 0
            if current_count > observed_cancellation_count:
                externally_cancelled = True
                observed_cancellation_count = current_count
    try:
        # 讓 monitor cancellation cleanup 中已排程的 owner.cancel() 有確定checkpoint。
        await asyncio.sleep(0)
    except asyncio.CancelledError:
        current_count = owner.cancelling() if owner is not None else 0
        if current_count > observed_cancellation_count:
            externally_cancelled = True
            observed_cancellation_count = current_count
    if not task.cancelled():
        try:
            task.result()
        except Exception:
            pass
    return externally_cancelled


@dataclass(frozen=True)
class FacebookProbeBrowserExecution:
    """共用 group-document probe 的 privacy-safe browser observation。"""

    result: FacebookProbeResult
    browser_launched: bool = False
    page_count: int = 0
    document_count: int = 0
    context_closed: bool = False
    cleanup_completed: bool = True
    marker_trip_pending: bool = False
    cancelled: bool = False
    failure_reason: str = ""
    failure_stage: FacebookProbeFailureStage | None = None


class FacebookProbeBrowserCancelled(asyncio.CancelledError):
    """攜帶 cleanup 後 execution，讓 durable owner 完成 cancel。"""

    def __init__(self, execution: FacebookProbeBrowserExecution) -> None:
        super().__init__()
        self.execution = execution


async def execute_group_document_probe(
    *,
    options: ResidentRuntimeOptions,
    target: TargetDescriptor,
    recipe: FacebookRecoveryProbeRecipe,
    deadline: FacebookProbeDeadline,
    mark_trip_pending: Callable[[], bool],
    playwright_factory: Callable[[], Any] = async_playwright,
    launch_context: Callable[..., Awaitable[Any]] = launch_persistent_context_async,
    page_guard: Callable[[Any], Awaitable[None]] = ensure_async_page_scannable,
) -> FacebookProbeBrowserExecution:
    """執行共用的一頁/一次group document probe，再用獨立grace cleanup。"""

    playwright_manager: Any | None = None
    playwright_started = False
    browser_context: Any | None = None
    browser_launched = False
    page_count = 0
    document_count = 0
    marker_trip_pending = False
    cancelled = False
    failure_reason = ""
    failure_stage: FacebookProbeFailureStage | None = None
    result = FacebookProbeResult.INCONCLUSIVE
    group_url = _group_document_url(target)
    stage = FacebookProbeFailureStage.BROWSER_LAUNCH

    try:
        async with deadline.enforce():
            playwright_manager = playwright_factory()
            playwright_started = True
            playwright = await playwright_manager.__aenter__()
            browser_context = await launch_context(
                playwright,
                BrowserRuntimeOptions(
                    profile_dir=options.profile_dir,
                    headless=not options.headed_compat,
                    timeout_seconds=deadline.driver_timeout_seconds(
                        max(options.scan_timeout_seconds, 1.0)
                    ),
                ),
            )
            browser_launched = True
            stage = FacebookProbeFailureStage.PAGE_CREATE
            pages = tuple(getattr(browser_context, "pages", ()))
            if len(pages) > 1:
                raise RuntimeError("probe context contains multiple pages")
            page = pages[0] if pages else await browser_context.new_page()
            page_count = 1
            stage = FacebookProbeFailureStage.NAVIGATION
            await page.goto(
                group_url,
                wait_until="domcontentloaded",
                timeout=deadline.driver_timeout_seconds(
                    max(options.scan_timeout_seconds, 1.0)
                )
                * 1000,
            )
            document_count = 1
            stage = FacebookProbeFailureStage.PAGE_GUARD
            await page_guard(page)
            stage = FacebookProbeFailureStage.ROUTE_IDENTITY
            final_url = str(getattr(page, "url", "") or "").strip()
            if not final_url or not final_url_matches_expected_group(
                final_url=final_url,
                canonical_url=group_url,
            ):
                raise RuntimeError("probe group identity mismatch")
            result = FacebookProbeResult.SUCCESS
    except TimeoutError:
        result = FacebookProbeResult.INCONCLUSIVE
        failure_reason = SCAN_TIMEOUT_REASON
        failure_stage = FacebookProbeFailureStage.DEADLINE
    except WorkerFailure as exc:
        failure_reason = normalize_probe_failure_reason(exc.reason)
        failure_stage = stage
        if failure_reason == FACEBOOK_TEMPORARY_BLOCK_REASON:
            marker_trip_pending = mark_trip_pending()
            result = FacebookProbeResult.BLOCKED
        else:
            result = FacebookProbeResult.INCONCLUSIVE
    except asyncio.CancelledError:
        result = FacebookProbeResult.CANCELLED
        cancelled = True
    except Exception:
        result = FacebookProbeResult.INCONCLUSIVE
        failure_reason = unexpected_probe_failure_reason(stage)
        failure_stage = stage

    cleanup, cleanup_cancelled = await _close_probe_browser_resources_despite_cancellation(
        browser_context=browser_context,
        playwright_manager=playwright_manager,
        playwright_started=playwright_started,
        timeout_seconds=recipe.cleanup_grace_seconds,
    )
    if cleanup_cancelled:
        result = FacebookProbeResult.CANCELLED
        cancelled = True
    context_closed = cleanup.completed and cleanup.context_closed
    if not cleanup.completed:
        failure_stage = FacebookProbeFailureStage.CONTEXT_CLOSE
        if not failure_reason:
            failure_reason = SCHEDULER_RUNTIME_REASON
        if result == FacebookProbeResult.SUCCESS:
            result = FacebookProbeResult.INCONCLUSIVE

    execution = FacebookProbeBrowserExecution(
        result=result,
        browser_launched=browser_launched,
        page_count=page_count,
        document_count=document_count,
        context_closed=context_closed,
        cleanup_completed=cleanup.completed,
        marker_trip_pending=marker_trip_pending,
        cancelled=cancelled,
        failure_reason=failure_reason,
        failure_stage=failure_stage,
    )
    if cancelled:
        raise FacebookProbeBrowserCancelled(execution)
    return execution


def _group_document_url(target: TargetDescriptor) -> str:
    """建立不含 post/permalink 的 canonical group root URL。"""

    group_id = target.group_id.strip()
    if not group_id:
        raise ValueError("probe target group id is required")
    return f"https://www.facebook.com/groups/{quote(group_id, safe='')}"
