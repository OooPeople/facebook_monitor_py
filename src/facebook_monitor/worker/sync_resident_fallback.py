"""Sync resident fallback worker。

職責：保留 debug/fallback 用的 sync Playwright resident loop。正式產品主路徑
是 `worker.resident_main`，共用 model/helper 從 `worker.resident_shared` 匯入。
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from contextlib import AbstractContextManager
from contextlib import contextmanager
from time import sleep
from typing import Any
from typing import Protocol
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions
from facebook_monitor.automation.browser_runtime import launch_persistent_context_sync
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.scheduler.runtime_recovery import RETRYABLE_IDLE_FAILURE_REASONS
from facebook_monitor.scheduler.runtime_recovery import recover_stale_runtime_targets
from facebook_monitor.worker.comments_pipeline import CommentsScanSummary
from facebook_monitor.worker.comments_pipeline import scan_comments_target_page
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.errors import classify_playwright_exception
from facebook_monitor.worker.page_timing import RESIDENT_PAGE_READY_WAIT_MS
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.posts_pipeline import scan_posts_page
from facebook_monitor.worker.resident_shared import ResidentCycleSummary
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.resident_shared import list_active_resident_target_ids
from facebook_monitor.worker.resident_shared import load_resident_target
from facebook_monitor.worker.resident_shared import mark_resident_target_error
from facebook_monitor.worker.resident_shared import mark_resident_target_idle
from facebook_monitor.worker.resident_shared import record_resident_scan_failure
from facebook_monitor.worker.resident_shared import should_reload_resident_page


SleepCallable = Callable[[float], None]
StopCheckCallable = Callable[[], bool]
ContextFactory = Callable[[ResidentRuntimeOptions], AbstractContextManager[Any]]
CycleObserver = Callable[[ResidentCycleSummary], None]


class ResidentScanCallable(Protocol):
    """定義 sync fallback worker 可注入的掃描函式介面。"""

    def __call__(
        self,
        *,
        page: Any,
        app: Any,
        target: TargetDescriptor,
        config: TargetConfig,
        scroll_rounds: int,
        scroll_wait_ms: int,
    ) -> PostsScanSummary | CommentsScanSummary:
        """掃描單一 target page 並回傳摘要。"""


class SyncResidentPagePool:
    """維護 sync fallback target id 到 Playwright page 的對應。"""

    def __init__(self, context: Any) -> None:
        self.context = context
        self.pages: dict[str, Any] = {}

    def get(self, target: TargetDescriptor) -> tuple[Any, bool]:
        """取得 target 對應 page；不存在或已關閉時建立新 page。"""

        page = self.pages.get(target.id)
        if page is not None and not _is_page_closed(page):
            return page, False

        page = self.context.new_page()
        self.pages[target.id] = page
        return page, True

    def discard(self, target_id: str) -> None:
        """關閉並移除單一 target page，供下輪重新建立。"""

        page = self.pages.pop(target_id, None)
        _close_page_quietly(page)

    def close_inactive(self, active_target_ids: set[str]) -> int:
        """關閉不再處於 active desired state 的 target pages。"""

        closed_count = 0
        for target_id in tuple(self.pages):
            if target_id in active_target_ids:
                continue
            self.discard(target_id)
            closed_count += 1
        return closed_count

    def close_all(self) -> None:
        """關閉所有已建立 page。"""

        for target_id in tuple(self.pages):
            self.discard(target_id)


def prepare_sync_resident_page(
    *,
    page: Any,
    target: TargetDescriptor,
    timeout_ms: float,
) -> None:
    """讓 sync fallback page 停在 target route；同一 route 只 reload。"""

    current_url = str(getattr(page, "url", "") or "")
    if should_reload_resident_page(current_url, target.canonical_url):
        page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
    else:
        page.goto(target.canonical_url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(RESIDENT_PAGE_READY_WAIT_MS)


def select_sync_resident_scan_page(target: TargetDescriptor) -> ResidentScanCallable:
    """依 target kind 選擇 sync fallback 掃描函式。"""

    if target.target_kind == TargetKind.COMMENTS:
        return scan_comments_target_page
    return scan_posts_page


def run_sync_resident_fallback_loop(
    options: ResidentRuntimeOptions,
    *,
    context_factory: ContextFactory | None = None,
    scan_page: ResidentScanCallable = scan_posts_page,
    sleep_fn: SleepCallable = sleep,
    should_stop: StopCheckCallable | None = None,
    on_cycle: CycleObserver | None = None,
) -> list[ResidentCycleSummary]:
    """執行 sync fallback 常駐 loop；max_cycles 為 None 時會持續執行。"""

    if not options.profile_dir.exists():
        raise WorkerFailure("profile_missing", str(options.profile_dir))

    summaries: list[ResidentCycleSummary] = []
    cycle_index = 0
    selected_context_factory = context_factory or _open_sync_fallback_browser_context
    with selected_context_factory(options) as browser_context:
        page_pool = SyncResidentPagePool(browser_context)
        schedule_planner = TargetSchedulePlanner()
        try:
            stop_requested = should_stop or _never_stop
            while (
                not stop_requested()
                and (options.max_cycles is None or cycle_index < options.max_cycles)
            ):
                cycle_index += 1
                summary = run_sync_resident_fallback_cycle(
                    options=options,
                    page_pool=page_pool,
                    scan_page=scan_page,
                    schedule_planner=schedule_planner,
                    cycle_index=cycle_index,
                )
                summaries.append(summary)
                if on_cycle:
                    on_cycle(summary)

                if options.max_cycles is not None and cycle_index >= options.max_cycles:
                    break
                sleep_fn(max(options.scheduler_tick_seconds, 0))
        finally:
            page_pool.close_all()

    return summaries


def run_sync_resident_fallback_cycle(
    *,
    options: ResidentRuntimeOptions,
    page_pool: SyncResidentPagePool,
    scan_page: ResidentScanCallable,
    cycle_index: int,
    schedule_planner: TargetSchedulePlanner | None = None,
) -> ResidentCycleSummary:
    """執行 sync fallback 單輪掃描。"""

    planner = schedule_planner or TargetSchedulePlanner()
    recover_stale_runtime_targets(options.db_path, options.stale_running_after_seconds)
    active_target_ids = list_active_resident_target_ids(options.db_path)
    closed_page_count = page_pool.close_inactive(active_target_ids)
    due_targets = planner.list_due_targets(
        options.db_path,
        default_interval_seconds=options.interval_seconds,
        max_count=options.max_concurrent_scans,
    )
    worker_id = f"resident-{uuid4()}"
    success_count = 0
    failure_count = 0
    skipped_count = 0
    opened_page_count = 0
    reused_page_count = 0

    for due_target in due_targets:
        target_id = due_target.target_id
        try:
            resident_target = load_resident_target(options.db_path, target_id)
        except WorkerFailure as exc:
            mark_resident_target_error(options.db_path, target_id, f"{exc.reason}: {exc}")
            failure_count += 1
            continue

        with SqliteApplicationContext(options.db_path) as app:
            locked_state = app.services.targets.try_mark_target_running(target_id, worker_id)
        if locked_state is None:
            skipped_count += 1
            continue
        planner.mark_dispatched(due_target)

        try:
            page, opened = page_pool.get(resident_target.target)
            opened_page_count += int(opened)
            reused_page_count += int(not opened)
            prepare_sync_resident_page(
                page=page,
                target=resident_target.target,
                timeout_ms=max(
                    options.scan_timeout_seconds,
                    PYTHON_SCHEDULER_RUNTIME_DEFAULTS.min_browser_scan_timeout_seconds,
                )
                * 1000,
            )
            with SqliteApplicationContext(options.db_path) as app:
                selected_scan_page = (
                    scan_page
                    if resident_target.target.target_kind == TargetKind.POSTS
                    else select_sync_resident_scan_page(resident_target.target)
                )
                selected_scan_page(
                    page=page,
                    app=app,
                    target=resident_target.target,
                    config=resident_target.config,
                    scroll_rounds=options.scroll_rounds,
                    scroll_wait_ms=options.scroll_wait_ms,
                )
                app.services.targets.mark_target_idle(target_id)
            success_count += 1
        except WorkerFailure as exc:
            failure_count += 1
            record_resident_scan_failure(options.db_path, resident_target.target, exc.reason, str(exc))
            if exc.reason in RETRYABLE_IDLE_FAILURE_REASONS:
                mark_resident_target_idle(options.db_path, target_id)
            else:
                mark_resident_target_error(options.db_path, target_id, f"{exc.reason}: {exc}")
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            failure_count += 1
            reason = classify_playwright_exception(exc)
            record_resident_scan_failure(options.db_path, resident_target.target, reason, str(exc))
            mark_resident_target_error(options.db_path, target_id, f"{reason}: {exc}")
            page_pool.discard(target_id)
        except Exception as exc:
            failure_count += 1
            record_resident_scan_failure(options.db_path, resident_target.target, "unknown", str(exc))
            mark_resident_target_error(options.db_path, target_id, f"unknown: {exc}")
            page_pool.discard(target_id)
        finally:
            planner.mark_finished(target_id)

    return ResidentCycleSummary(
        cycle_index=cycle_index,
        selected_count=len(due_targets),
        success_count=success_count,
        failure_count=failure_count,
        skipped_count=skipped_count,
        opened_page_count=opened_page_count,
        reused_page_count=reused_page_count,
        closed_page_count=closed_page_count,
    )


@contextmanager
def _open_sync_fallback_browser_context(
    options: ResidentRuntimeOptions,
) -> Iterator[Any]:
    """開啟 sync fallback worker 共用的 Playwright persistent context。"""

    try:
        with acquire_profile_lease(options.profile_dir, "sync resident fallback worker"):
            with sync_playwright() as playwright:
                context = launch_persistent_context_sync(
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
                    context.set_default_timeout(
                        max(
                            options.scan_timeout_seconds,
                            PYTHON_SCHEDULER_RUNTIME_DEFAULTS.min_browser_scan_timeout_seconds,
                        )
                        * 1000
                    )
                    context.set_default_navigation_timeout(
                        max(
                            options.scan_timeout_seconds,
                            PYTHON_SCHEDULER_RUNTIME_DEFAULTS.min_browser_scan_timeout_seconds,
                        )
                        * 1000
                    )
                    yield context
                finally:
                    context.close()
    except ProfileLeaseError as exc:
        raise WorkerFailure("profile_locked", str(exc)) from exc


def _is_page_closed(page: Any) -> bool:
    """回傳 Playwright page 是否已關閉。"""

    is_closed = getattr(page, "is_closed", None)
    if callable(is_closed):
        return bool(is_closed())
    return False


def _close_page_quietly(page: Any | None) -> None:
    """安靜關閉 page，避免清理階段例外蓋過主要錯誤。"""

    if page is None or _is_page_closed(page):
        return
    close = getattr(page, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            return


def _never_stop() -> bool:
    """預設不要求 sync fallback worker 停止。"""

    return False
