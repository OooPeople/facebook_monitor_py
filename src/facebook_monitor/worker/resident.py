"""Resident group posts worker。

職責：維持單一 Playwright persistent context，並重用各 target 的 page 進行掃描。
此模組只管理瀏覽器與 page 生命週期，實際掃描、去重與通知仍委派
`scan_group_posts_page()`，避免常駐模式與 one-shot 模式重複實作核心邏輯。
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Any
from typing import Protocol
from urllib.parse import urlparse
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import RecordScanRequest
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.facebook.route_detection import FACEBOOK_HOSTS
from facebook_monitor.facebook.route_detection import RouteDetectionError
from facebook_monitor.facebook.route_detection import detect_group_comments_route
from facebook_monitor.scheduler.loop import recover_stale_running_targets
from facebook_monitor.scheduler.loop import RETRYABLE_IDLE_FAILURE_REASONS
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.comments import GroupCommentsScanSummary
from facebook_monitor.worker.comments import scan_comments_page
from facebook_monitor.worker.group_posts import GroupPostsScanSummary
from facebook_monitor.worker.group_posts import WorkerFailure
from facebook_monitor.worker.group_posts import scan_group_posts_page
from facebook_monitor.worker.runner import classify_exception
from facebook_monitor.worker.runner import validate_target_route


SleepCallable = Callable[[float], None]
StopCheckCallable = Callable[[], bool]
ContextFactory = Callable[["ResidentWorkerOptions"], AbstractContextManager[Any]]
CycleObserver = Callable[["ResidentCycleSummary"], None]


class ResidentScanCallable(Protocol):
    """定義 resident worker 可注入的掃描函式介面。"""

    def __call__(
        self,
        *,
        page: Any,
        app: Any,
        target: TargetDescriptor,
        config: TargetConfig,
        scroll_rounds: int,
        scroll_wait_ms: int,
    ) -> GroupPostsScanSummary | GroupCommentsScanSummary:
        """掃描單一 target page 並回傳摘要。"""


@dataclass(frozen=True)
class ResidentWorkerOptions:
    """保存 resident worker 執行選項。"""

    db_path: Path
    profile_dir: Path
    interval_seconds: float = 60
    scheduler_tick_seconds: float = 2
    max_concurrent_scans: int = 2
    scroll_rounds: int = 3
    scroll_wait_ms: int = 2500
    scan_timeout_seconds: float = 120
    stale_running_after_seconds: float = 180
    headed_compat: bool = False
    max_cycles: int | None = None


@dataclass(frozen=True)
class ResidentCycleSummary:
    """保存 resident worker 單輪摘要。"""

    cycle_index: int
    selected_count: int
    success_count: int
    failure_count: int
    skipped_count: int
    opened_page_count: int
    reused_page_count: int
    closed_page_count: int
    queued_count: int = 0
    running_count: int = 0
    queue_length: int = 0
    queued_target_ids: tuple[str, ...] = ()
    worker_ids: tuple[str, ...] = ()
    page_pool_size: int = 0
    resident_browser_alive: bool = False


@dataclass(frozen=True)
class ResidentTarget:
    """保存 resident worker 單次掃描需要的 target 與 config。"""

    target: TargetDescriptor
    config: TargetConfig


class ResidentPagePool:
    """維護 target id 到 Playwright page 的對應。"""

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


def list_active_resident_target_ids(db_path: Path) -> set[str]:
    """列出目前 desired active 且 resident 支援掃描的 target ids。"""

    with SqliteApplicationContext(db_path) as app:
        target_ids: set[str] = set()
        for target in app.repositories.targets.list_enabled():
            if target.target_kind not in {TargetKind.POSTS, TargetKind.COMMENTS}:
                continue
            runtime_state = app.services.targets.ensure_runtime_state(target.id)
            if runtime_state.desired_state == TargetDesiredState.ACTIVE:
                target_ids.add(target.id)
        return target_ids


def list_active_group_post_target_ids(db_path: Path) -> set[str]:
    """相容舊呼叫端：回傳 resident 支援且 desired active 的 target ids。"""

    return list_active_resident_target_ids(db_path)


def load_resident_target(db_path: Path, target_id: str) -> ResidentTarget:
    """載入 resident worker 掃描 target 所需資料。"""

    with SqliteApplicationContext(db_path) as app:
        target = app.repositories.targets.get(target_id)
        if target is None:
            raise WorkerFailure("target_missing", f"Target not found: {target_id}")
        validate_resident_target_route(target)
        config = app.services.targets.get_config_for_target(target)
        return ResidentTarget(target=target, config=config)


def validate_resident_target_route(target: TargetDescriptor) -> None:
    """確認 resident worker 支援 target kind 與 canonical route。"""

    if target.target_kind == TargetKind.POSTS:
        validate_target_route(target)
        return
    if target.target_kind == TargetKind.COMMENTS:
        try:
            route = detect_group_comments_route(target.canonical_url)
        except RouteDetectionError as exc:
            raise WorkerFailure("target_invalid", str(exc)) from exc
        if route.group_id != target.group_id or route.parent_post_id != target.parent_post_id:
            raise WorkerFailure("target_invalid", "Comments target route does not match saved scope.")
        return
    raise WorkerFailure("target_kind_unsupported", f"Unsupported target kind: {target.target_kind.value}")


def prepare_resident_page(
    *,
    page: Any,
    target: TargetDescriptor,
    timeout_ms: float,
) -> None:
    """讓 page 停在 target route；同一 route 只 reload 以保留排序狀態。"""

    current_url = str(getattr(page, "url", "") or "")
    if should_reload_resident_page(current_url, target.canonical_url):
        page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
    else:
        page.goto(target.canonical_url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(5000)


def should_reload_resident_page(current_url: str, canonical_url: str) -> bool:
    """判斷 resident page 是否已在同一 target route，可用 reload 取代 goto。"""

    current_key = _resident_route_key(current_url)
    canonical_key = _resident_route_key(canonical_url)
    return bool(current_key and current_key == canonical_key)


def _resident_route_key(url: str) -> tuple[str, ...] | None:
    """回傳 resident target route key，支援 posts feed 與 comments parent post。"""

    return _group_post_route_key(url) or _group_feed_route_key(url)


def _group_post_route_key(url: str) -> tuple[str, str, str] | None:
    """回傳 Facebook group post route key，供 comments target reload 判斷。"""

    try:
        route = detect_group_comments_route(url)
    except RouteDetectionError:
        return None
    return ("comments", route.group_id, route.parent_post_id)


def _group_feed_route_key(url: str) -> tuple[str, str] | None:
    """回傳 Facebook group feed route key；單篇貼文或 groups 入口不視為 feed。"""

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in FACEBOOK_HOSTS:
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 2 or path_parts[0] != "groups":
        return None
    group_id = path_parts[1].strip()
    if not group_id or group_id.lower() == "feed":
        return None
    return ("groups", group_id)


def select_resident_scan_page(target: TargetDescriptor) -> ResidentScanCallable:
    """依 target kind 選擇 resident sync 掃描函式。"""

    if target.target_kind == TargetKind.COMMENTS:
        return scan_comments_page
    return scan_group_posts_page


def run_resident_worker_loop(
    options: ResidentWorkerOptions,
    *,
    context_factory: ContextFactory | None = None,
    scan_page: ResidentScanCallable = scan_group_posts_page,
    sleep_fn: SleepCallable = sleep,
    should_stop: StopCheckCallable | None = None,
    on_cycle: CycleObserver | None = None,
) -> list[ResidentCycleSummary]:
    """執行常駐 worker loop；max_cycles 為 None 時會持續執行。"""

    if not options.profile_dir.exists():
        raise WorkerFailure("profile_missing", str(options.profile_dir))

    summaries: list[ResidentCycleSummary] = []
    cycle_index = 0
    selected_context_factory = context_factory or _open_persistent_browser_context
    with selected_context_factory(options) as browser_context:
        page_pool = ResidentPagePool(browser_context)
        schedule_planner = TargetSchedulePlanner()
        try:
            stop_requested = should_stop or _never_stop
            while (
                not stop_requested()
                and (options.max_cycles is None or cycle_index < options.max_cycles)
            ):
                cycle_index += 1
                summary = run_resident_worker_cycle(
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


def run_resident_worker_cycle(
    *,
    options: ResidentWorkerOptions,
    page_pool: ResidentPagePool,
    scan_page: ResidentScanCallable,
    cycle_index: int,
    schedule_planner: TargetSchedulePlanner | None = None,
) -> ResidentCycleSummary:
    """執行 resident worker 單輪掃描。"""

    planner = schedule_planner or TargetSchedulePlanner()
    recover_stale_running_targets(options.db_path, options.stale_running_after_seconds)
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
            _mark_target_error(options.db_path, target_id, f"{exc.reason}: {exc}")
            failure_count += 1
            continue

        with SqliteApplicationContext(options.db_path) as app:
            locked_state = app.services.targets.try_mark_target_running(target_id, worker_id)
        if locked_state is None:
            skipped_count += 1
            continue
        planner.mark_dispatched(due_target)

        page = None
        try:
            page, opened = page_pool.get(resident_target.target)
            opened_page_count += int(opened)
            reused_page_count += int(not opened)
            prepare_resident_page(
                page=page,
                target=resident_target.target,
                timeout_ms=max(options.scan_timeout_seconds, 10) * 1000,
            )
            with SqliteApplicationContext(options.db_path) as app:
                selected_scan_page = (
                    scan_page
                    if resident_target.target.target_kind == TargetKind.POSTS
                    else select_resident_scan_page(resident_target.target)
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
            _record_resident_failure(
                options.db_path,
                resident_target.target,
                exc.reason,
                str(exc),
            )
            if exc.reason in RETRYABLE_IDLE_FAILURE_REASONS:
                _mark_target_idle(options.db_path, target_id)
            else:
                _mark_target_error(options.db_path, target_id, f"{exc.reason}: {exc}")
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            failure_count += 1
            reason = classify_exception(exc)
            _record_resident_failure(
                options.db_path,
                resident_target.target,
                reason,
                str(exc),
            )
            _mark_target_error(options.db_path, target_id, f"{reason}: {exc}")
            page_pool.discard(target_id)
        except Exception as exc:
            failure_count += 1
            _record_resident_failure(
                options.db_path,
                resident_target.target,
                "unknown",
                str(exc),
            )
            _mark_target_error(options.db_path, target_id, f"unknown: {exc}")
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
def _open_persistent_browser_context(
    options: ResidentWorkerOptions,
) -> AbstractContextManager[Any]:
    """開啟 resident worker 共用的 Playwright persistent context。"""

    try:
        with acquire_profile_lease(options.profile_dir, "resident worker"):
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(options.profile_dir),
                    headless=not options.headed_compat,
                    viewport={"width": 1366, "height": 900},
                    timeout=max(options.scan_timeout_seconds, 10) * 1000,
                )
                try:
                    context.set_default_timeout(max(options.scan_timeout_seconds, 10) * 1000)
                    context.set_default_navigation_timeout(
                        max(options.scan_timeout_seconds, 10) * 1000
                    )
                    yield context
                finally:
                    context.close()
    except ProfileLeaseError as exc:
        raise WorkerFailure("profile_locked", str(exc)) from exc


def _record_resident_failure(
    db_path: Path,
    target: TargetDescriptor,
    reason: str,
    message: str,
) -> None:
    """記錄 resident worker 的失敗 scan run。"""

    with SqliteApplicationContext(db_path) as app:
        app.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message=f"{reason}: {message}",
                metadata={"worker": "phase_c_resident_worker"},
            )
        )


def _mark_target_error(db_path: Path, target_id: str, message: str) -> None:
    """將 target runtime state 標成 error；target 已不存在時忽略。"""

    with SqliteApplicationContext(db_path) as app:
        if app.repositories.targets.get(target_id) is None:
            return
        app.services.targets.mark_target_error(target_id, message)


def _mark_target_idle(db_path: Path, target_id: str) -> None:
    """將 target runtime state 標回 idle；target 已不存在時忽略。"""

    with SqliteApplicationContext(db_path) as app:
        if app.repositories.targets.get(target_id) is None:
            return
        app.services.targets.mark_target_idle(target_id)


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
    """預設不要求 resident worker 停止。"""

    return False
