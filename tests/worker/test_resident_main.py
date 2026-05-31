"""Resident main worker tests。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sqlite3
from typing import Any

from pytest import MonkeyPatch
from playwright.async_api import Error as AsyncPlaywrightError

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import TargetConfigPatch
from facebook_monitor.application.services import UpsertCommentsTargetRequest
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetCoverImageRefreshStatus
from facebook_monitor.core.models import TargetCoverImageRefreshResult
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import PAGE_LOAD_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.scheduler.planner import DueTarget
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.worker.comments_pipeline import CommentsScanSummary
from facebook_monitor.worker.resident_main import _is_playwright_driver_shutdown_exception
from facebook_monitor.worker.resident_main import dispatch_pending_notification_outbox
from facebook_monitor.worker.resident_main import refresh_requested_target_metadata
from facebook_monitor.worker.resident_main import refresh_target_group_cover_image_from_context
from facebook_monitor.worker.resident_main import run_bounded_retention_maintenance_if_due
from facebook_monitor.worker.resident_main import run_resident_main_cycle
from facebook_monitor.worker.resident_main import run_resident_main_loop
from facebook_monitor.worker.resident_main import run_resident_main_scheduler_tick
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_executor import prepare_resident_main_page
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import QueueItem
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.resident_shared import ResidentTarget
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.resident_shared import list_active_resident_target_ids
from facebook_monitor.worker.scan_finalize import record_skipped_scan


class FakeAsyncPage:
    """測試用 async page，記錄導航與關閉狀態。"""

    def __init__(self) -> None:
        self.url = "about:blank"
        self.goto_count = 0
        self.reload_count = 0
        self.closed = False

    async def goto(self, url: str, wait_until: str, timeout: float) -> None:
        """模擬 async 導航。"""

        self.url = url.rstrip("/")
        self.goto_count += 1

    async def reload(self, wait_until: str, timeout: float) -> None:
        """模擬 async reload。"""

        self.reload_count += 1

    async def wait_for_timeout(self, milliseconds: int) -> None:
        """模擬 Playwright 等待。"""

    def is_closed(self) -> bool:
        """回傳 page 是否關閉。"""

        return self.closed

    async def close(self) -> None:
        """標記 page 已關閉。"""

        self.closed = True


class FakeAsyncBrowserContext:
    """測試用 async browser context。"""

    def __init__(self) -> None:
        self.pages: list[FakeAsyncPage] = []
        self.closed = False
        self.default_timeout = 0.0
        self.default_navigation_timeout = 0.0

    def set_default_timeout(self, timeout: float) -> None:
        """記錄 context default timeout。"""

        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout: float) -> None:
        """記錄 context default navigation timeout。"""

        self.default_navigation_timeout = timeout

    async def new_page(self) -> FakeAsyncPage:
        """建立 fake async page。"""

        page = FakeAsyncPage()
        self.pages.append(page)
        return page

    async def close(self) -> None:
        """標記 browser context 已關閉。"""

        self.closed = True


def test_list_active_resident_target_ids_excludes_error_runtime(tmp_path: Path) -> None:
    """resident page pool 不應保留已進入 error 的 active target page。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        active = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="active",
                canonical_url="https://www.facebook.com/groups/active",
            )
        )
        errored = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="errored",
                canonical_url="https://www.facebook.com/groups/errored",
            )
        )
        app.services.targets.restart_target_monitoring(active.id)
        app.services.targets.restart_target_monitoring(errored.id)
        app.services.targets.mark_target_error(errored.id, "terminal error")

    assert list_active_resident_target_ids(db_path) == {active.id}


class FakeMetadataLocator:
    """metadata refresh 測試用 locator。"""

    async def inner_text(self, timeout: int) -> str:
        """回傳已登入狀態的 body text。"""

        return "Facebook group page"


class FakeLoggedOutMetadataLocator(FakeMetadataLocator):
    """metadata refresh 失敗測試用 locator。"""

    async def inner_text(self, timeout: int) -> str:
        """回傳未登入頁面的 body text。"""

        return "Log into Facebook"


class FakeMetadataPage:
    """metadata refresh 測試用 page。"""

    def __init__(self) -> None:
        self.url = "about:blank"
        self.closed = False

    async def goto(
        self,
        url: str,
        wait_until: str,
        timeout: float | None = None,
    ) -> None:
        """記錄 metadata refresh 導航 URL。"""

        self.url = url

    async def reload(self, wait_until: str, timeout: float) -> None:
        """模擬 resident scan page reload。"""

    def is_closed(self) -> bool:
        """回傳 page 是否已關閉。"""

        return self.closed

    async def wait_for_timeout(self, milliseconds: int) -> None:
        """模擬等待頁面 title 更新。"""

    def locator(self, selector: str) -> FakeMetadataLocator:
        """回傳 body locator。"""

        return FakeMetadataLocator()

    async def title(self) -> str:
        """回傳可清理的 Facebook title。"""

        return "(2) 測試社團 | Facebook"

    async def evaluate(self, script: str) -> str:
        """回傳 metadata resolver 抽到的 cover image URL。"""

        return "https://scontent.xx.fbcdn.net/group-cover.jpg"

    async def close(self) -> None:
        """標記 metadata page 已關閉。"""

        self.closed = True


class FakeLoggedOutMetadataPage(FakeMetadataPage):
    """metadata refresh 失敗測試用 page。"""

    def locator(self, selector: str) -> FakeLoggedOutMetadataLocator:
        """回傳未登入頁面的 body locator。"""

        return FakeLoggedOutMetadataLocator()


class FakeMetadataBrowserContext:
    """metadata refresh 測試用 browser context。"""

    def __init__(self) -> None:
        self.pages: list[FakeMetadataPage] = []

    async def new_page(self) -> FakeMetadataPage:
        """建立 metadata page。"""

        page = FakeMetadataPage()
        self.pages.append(page)
        return page


class RuntimeRefreshMetadataBrowserContext(FakeMetadataBrowserContext):
    """可作為 resident persistent context 的 metadata 測試 context。"""

    def __init__(self, *, fail_new_page: bool = False) -> None:
        super().__init__()
        self.fail_new_page = fail_new_page
        self.closed = False
        self.default_timeout = 0.0
        self.default_navigation_timeout = 0.0

    def set_default_timeout(self, timeout: float) -> None:
        """記錄 context default timeout。"""

        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout: float) -> None:
        """記錄 context default navigation timeout。"""

        self.default_navigation_timeout = timeout

    async def new_page(self) -> FakeMetadataPage:
        """建立 metadata page；必要時模擬 browser runtime 已中斷。"""

        if self.fail_new_page:
            raise AsyncPlaywrightError("Connection closed while reading from the driver")
        return await super().new_page()

    async def close(self) -> None:
        """標記 browser context 已關閉。"""

        self.closed = True


class RuntimeClosedOnPausedPage(FakeMetadataPage):
    """只有進入 paused target URL 時才模擬 browser runtime closed。"""

    async def goto(
        self,
        url: str,
        wait_until: str,
        timeout: float | None = None,
    ) -> None:
        """paused maintenance 若未被 filter 會在此觸發 runtime failure。"""

        if "groups/paused" in url:
            raise AsyncPlaywrightError("Connection closed while reading from the driver")
        await super().goto(url, wait_until=wait_until, timeout=timeout)


class RuntimeClosedOnPausedBrowserContext(RuntimeRefreshMetadataBrowserContext):
    """paused maintenance starvation 測試用 browser context。"""

    async def new_page(self) -> RuntimeClosedOnPausedPage:
        """建立只在 paused target 導航時失敗的 page。"""

        page = RuntimeClosedOnPausedPage()
        self.pages.append(page)
        return page


class FakeLoggedOutMetadataBrowserContext:
    """metadata refresh 失敗測試用 browser context。"""

    def __init__(self) -> None:
        self.pages: list[FakeLoggedOutMetadataPage] = []

    async def new_page(self) -> FakeLoggedOutMetadataPage:
        """建立未登入 fake metadata page。"""

        page = FakeLoggedOutMetadataPage()
        self.pages.append(page)
        return page


class FakeShutdownMetadataBrowserContext:
    """模擬 scheduler 停止時 Playwright driver 已關閉的 browser context。"""

    def __init__(self, on_new_page: Any) -> None:
        self.on_new_page = on_new_page
        self.new_page_count = 0

    async def new_page(self) -> FakeMetadataPage:
        """在開頁時切換 stop 狀態並丟出 Playwright shutdown 例外。"""

        self.new_page_count += 1
        self.on_new_page()
        raise Exception(
            "BrowserContext.new_page: Connection closed while reading from the driver"
        )


class RecordingSchedulePlanner(TargetSchedulePlanner):
    """記錄 dispatch 時機，避免 async resident 在 queue 階段推進 next_due_at。"""

    def __init__(self) -> None:
        super().__init__()
        self.dispatched_target_ids: list[str] = []

    def mark_dispatched(self, due_target: DueTarget, *, now: Any = None) -> None:
        self.dispatched_target_ids.append(due_target.target_id)
        super().mark_dispatched(due_target, now=now)


def _stub_runtime_outbox_dispatch(monkeypatch: MonkeyPatch) -> list[Path]:
    """避免 runtime failure 通知測試觸發外部 I/O，並記錄 dispatch DB。"""

    dispatch_calls: list[Path] = []

    def fake_dispatch(**kwargs: object) -> int:
        db_path = kwargs["db_path"]
        assert isinstance(db_path, Path)
        dispatch_calls.append(db_path)
        return 0

    monkeypatch.setattr(
        "facebook_monitor.notifications.outbox_service.dispatch_new_pending_notification_outbox_for_db",
        fake_dispatch,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.dispatch_new_pending_notification_outbox_for_db",
        fake_dispatch,
    )
    return dispatch_calls


def test_playwright_driver_shutdown_exception_is_classified() -> None:
    """只把 Playwright driver 關閉期間的已知背景 future 例外視為可消化噪音。"""

    assert _is_playwright_driver_shutdown_exception(
        Exception("Connection closed while reading from the driver")
    )
    assert not _is_playwright_driver_shutdown_exception(Exception("other error"))


def test_resident_main_loop_restarts_browser_context_on_scheduler_runtime(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """scheduler_runtime_restart 要關閉舊 persistent context 並建立新 context。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[FakeAsyncBrowserContext] = []
    scan_calls = 0
    stop_event = asyncio.Event()

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    class FakePlaywrightManager:
        """測試用 async_playwright context manager。"""

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    async def fake_launch_persistent_context_async(
        _playwright: object,
        _options: object,
    ) -> FakeAsyncBrowserContext:
        context = FakeAsyncBrowserContext()
        contexts.append(context)
        return context

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        nonlocal scan_calls
        scan_calls += 1
        if scan_calls == 1:
            raise AsyncPlaywrightError("Target page, context or browser has been closed")
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch_persistent_context_async,
    )

    def stop_after_success(summary: Any) -> None:
        if summary.success_count > 0:
            stop_event.set()

    async def run_test() -> None:
        await asyncio.wait_for(
            run_resident_main_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    scheduler_tick_seconds=0,
                ),
                scan_page=fake_scan_page,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_success,
            ),
            timeout=2,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert len(contexts) == 2
    assert contexts[0].closed is True
    assert contexts[1].closed is True
    assert scan_calls == 2
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.consecutive_failure_reason == ""
    assert latest_scan is not None
    assert latest_scan.metadata["reason"] == SCHEDULER_RUNTIME_REASON


def test_resident_main_loop_runtime_restart_wakes_scheduler_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """sleep 期間偵測到 runtime restart 時，要立刻關閉並重建 context。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[FakeAsyncBrowserContext] = []
    scan_calls = 0
    scan_ready = asyncio.Event()
    release_scan_failure = asyncio.Event()
    sleep_started = asyncio.Event()
    sleep_cancelled = asyncio.Event()
    second_scan_done = asyncio.Event()
    stop_event = asyncio.Event()

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    class FakePlaywrightManager:
        """測試用 async_playwright context manager。"""

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    async def fake_launch_persistent_context_async(
        _playwright: object,
        _options: object,
    ) -> FakeAsyncBrowserContext:
        context = FakeAsyncBrowserContext()
        contexts.append(context)
        return context

    async def fake_sleep(_seconds: float) -> None:
        sleep_started.set()
        await scan_ready.wait()
        release_scan_failure.set()
        if len(contexts) > 1:
            await second_scan_done.wait()
            return
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            sleep_cancelled.set()
            raise

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        nonlocal scan_calls
        scan_calls += 1
        if scan_calls == 1:
            scan_ready.set()
            await sleep_started.wait()
            await release_scan_failure.wait()
            raise AsyncPlaywrightError("Target page, context or browser has been closed")
        second_scan_done.set()
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    def stop_after_success(summary: Any) -> None:
        if summary.success_count > 0:
            stop_event.set()

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch_persistent_context_async,
    )

    async def run_test() -> None:
        await asyncio.wait_for(
            run_resident_main_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    scheduler_tick_seconds=300,
                ),
                scan_page=fake_scan_page,
                sleep_fn=fake_sleep,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_success,
            ),
            timeout=2,
        )

    asyncio.run(run_test())

    assert sleep_cancelled.is_set()
    assert len(contexts) == 2
    assert contexts[0].closed is True
    assert contexts[1].closed is True
    assert scan_calls == 2


def test_resident_main_loop_retries_other_running_targets_after_runtime_restart(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """runtime restart 取消的其他 running targets 要在新 context 立即補掃。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[FakeAsyncBrowserContext] = []
    first_target_started = asyncio.Event()
    second_target_started = asyncio.Event()
    stop_event = asyncio.Event()
    success_counts: dict[str, int] = {}

    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="First",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
                group_name="Second",
            )
        )
        app.services.targets.restart_target_monitoring(first.id)
        app.services.targets.restart_target_monitoring(second.id)

    class FakePlaywrightManager:
        """測試用 async_playwright context manager。"""

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    async def fake_launch_persistent_context_async(
        _playwright: object,
        _options: object,
    ) -> FakeAsyncBrowserContext:
        context = FakeAsyncBrowserContext()
        contexts.append(context)
        return context

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        target = kwargs["target"]
        if target.id == first.id and not first_target_started.is_set():
            first_target_started.set()
            await second_target_started.wait()
            raise AsyncPlaywrightError("Target page, context or browser has been closed")
        if target.id == second.id and not second_target_started.is_set():
            second_target_started.set()
            await asyncio.sleep(10)
        success_counts[target.id] = success_counts.get(target.id, 0) + 1
        return PostsScanSummary(
            target_id=target.id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    def stop_after_both_succeed(summary: Any) -> None:
        if summary.success_count >= 2:
            stop_event.set()

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch_persistent_context_async,
    )

    async def run_test() -> None:
        await asyncio.wait_for(
            run_resident_main_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    scheduler_tick_seconds=0,
                    max_concurrent_scans=2,
                ),
                scan_page=fake_scan_page,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_both_succeed,
            ),
            timeout=3,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        first_state = app.repositories.runtime_states.get(first.id)
        second_state = app.repositories.runtime_states.get(second.id)
        first_latest = app.repositories.scan_runs.latest_by_target(first.id)
        second_latest = app.repositories.scan_runs.latest_by_target(second.id)

    assert len(contexts) == 2
    assert contexts[0].closed is True
    assert contexts[1].closed is True
    assert success_counts == {first.id: 1, second.id: 1}
    assert first_state is not None
    assert second_state is not None
    assert first_state.runtime_status == TargetRuntimeStatus.IDLE
    assert second_state.runtime_status == TargetRuntimeStatus.IDLE
    assert first_latest is not None
    assert second_latest is not None
    assert first_latest.metadata["reason"] == SCHEDULER_RUNTIME_REASON
    assert second_latest.metadata["reason"] == SCHEDULER_RUNTIME_REASON


def test_resident_main_loop_keeps_non_active_metadata_runtime_failure_pending(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """非 active metadata runtime failure 不可重啟 context 或寫 scan failure。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[RuntimeRefreshMetadataBrowserContext] = []
    stop_event = asyncio.Event()

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.mark_target_metadata_refresh_pending(target.id)

    class FakePlaywrightManager:
        """測試用 async_playwright context manager。"""

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    async def fake_launch_persistent_context_async(
        _playwright: object,
        _options: object,
    ) -> RuntimeRefreshMetadataBrowserContext:
        context = RuntimeRefreshMetadataBrowserContext(fail_new_page=not contexts)
        contexts.append(context)
        return context

    def stop_after_first_cycle(_summary: Any) -> None:
        stop_event.set()

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch_persistent_context_async,
    )

    async def run_test() -> None:
        await asyncio.wait_for(
            run_resident_main_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    scheduler_tick_seconds=0,
                ),
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_first_cycle,
            ),
            timeout=2,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert len(contexts) == 1
    assert contexts[0].closed is True
    assert updated is not None
    assert updated.metadata_status == TargetMetadataStatus.PENDING
    assert updated.metadata_error == ""
    assert latest_scan is None


def test_resident_main_loop_keeps_non_active_cover_runtime_failure_pending(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """非 active cover runtime failure 不可重啟 context 或寫 scan failure。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[RuntimeRefreshMetadataBrowserContext] = []
    stop_event = asyncio.Event()

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )

    class FakePlaywrightManager:
        """測試用 async_playwright context manager。"""

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    async def fake_launch_persistent_context_async(
        _playwright: object,
        _options: object,
    ) -> RuntimeRefreshMetadataBrowserContext:
        context = RuntimeRefreshMetadataBrowserContext(fail_new_page=not contexts)
        contexts.append(context)
        return context

    def stop_after_first_cycle(_summary: Any) -> None:
        stop_event.set()

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch_persistent_context_async,
    )

    async def run_test() -> None:
        await asyncio.wait_for(
            run_resident_main_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    scheduler_tick_seconds=0,
                ),
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_first_cycle,
            ),
            timeout=2,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.cover_image_refreshes.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert len(contexts) == 1
    assert contexts[0].closed is True
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.PENDING
    assert latest_scan is None


def test_active_metadata_runtime_failure_notifies_after_scan_retries(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """metadata runtime failure 要接回 target retry streak 與 outbox。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    dispatch_calls = _stub_runtime_outbox_dispatch(monkeypatch)
    contexts: list[RuntimeRefreshMetadataBrowserContext] = []
    stop_event = asyncio.Event()
    scan_calls = 0

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="Runtime metadata",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.clear_target_scan_request(target.id)
        app.services.targets.mark_target_metadata_refresh_pending(target.id)

    class FakePlaywrightManager:
        """測試用 async_playwright context manager。"""

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    async def fake_launch_persistent_context_async(
        _playwright: object,
        _options: object,
    ) -> RuntimeRefreshMetadataBrowserContext:
        context = RuntimeRefreshMetadataBrowserContext(fail_new_page=True)
        contexts.append(context)
        return context

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        nonlocal scan_calls
        scan_calls += 1
        raise AsyncPlaywrightError("Target page, context or browser has been closed")

    def stop_after_terminal_failure(_summary: Any) -> None:
        with SqliteApplicationContext(db_path) as app:
            state = app.repositories.runtime_states.get(target.id)
        if state is not None and state.runtime_status == TargetRuntimeStatus.ERROR:
            stop_event.set()

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch_persistent_context_async,
    )

    async def run_test() -> None:
        await asyncio.wait_for(
            run_resident_main_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    scheduler_tick_seconds=0,
                ),
                scan_page=fake_scan_page,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_terminal_failure,
            ),
            timeout=3,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        entries = app.repositories.notification_outbox.list_pending()
        run_count = app.repositories.scan_runs.connection.execute(
            "SELECT COUNT(*) FROM scan_runs WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]

    assert 3 <= len(contexts) <= 4
    assert all(context.closed for context in contexts)
    assert scan_calls == 0
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.ERROR
    assert state.consecutive_failure_reason == SCHEDULER_RUNTIME_REASON
    assert state.consecutive_failure_count == 3
    assert latest_scan is not None
    assert latest_scan.metadata["reason"] == SCHEDULER_RUNTIME_REASON
    assert run_count == 3
    assert len(entries) == 1
    assert entries[0].failure_reason == SCHEDULER_RUNTIME_REASON
    assert entries[0].failure_count == 3
    assert db_path in dispatch_calls


def test_active_cover_runtime_failure_defers_refresh_until_scan_retry(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """cover runtime failure 後，要先讓正式 scan retry，不可反覆擋住掃描。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[RuntimeRefreshMetadataBrowserContext] = []
    stop_event = asyncio.Event()
    scan_calls = 0

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.clear_target_scan_request(target.id)
        app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )

    class FakePlaywrightManager:
        """測試用 async_playwright context manager。"""

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    async def fake_launch_persistent_context_async(
        _playwright: object,
        _options: object,
    ) -> RuntimeRefreshMetadataBrowserContext:
        context = RuntimeRefreshMetadataBrowserContext(fail_new_page=not contexts)
        contexts.append(context)
        return context

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        nonlocal scan_calls
        scan_calls += 1
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    def stop_after_scan_success(summary: Any) -> None:
        if summary.success_count > 0:
            stop_event.set()

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch_persistent_context_async,
    )

    async def run_test() -> None:
        await asyncio.wait_for(
            run_resident_main_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    scheduler_tick_seconds=0,
                ),
                scan_page=fake_scan_page,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_scan_success,
            ),
            timeout=3,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        cover_state = app.repositories.cover_image_refreshes.get(target.id)

    assert len(contexts) == 2
    assert contexts[0].closed is True
    assert contexts[1].closed is True
    assert scan_calls == 1
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.consecutive_failure_reason == ""
    assert latest_scan is not None
    assert latest_scan.metadata["reason"] == SCHEDULER_RUNTIME_REASON
    assert cover_state is not None
    assert cover_state.status == TargetCoverImageRefreshStatus.IDLE
    assert cover_state.last_result == TargetCoverImageRefreshResult.SUCCEEDED_CHANGED


def test_paused_metadata_runtime_failure_does_not_starve_active_scan(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """paused target 的 metadata job 不可重啟 runtime 並擋住 active scan。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[RuntimeRefreshMetadataBrowserContext] = []
    stop_event = asyncio.Event()
    scan_calls = 0

    with SqliteApplicationContext(db_path) as app:
        paused = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="paused",
                canonical_url="https://www.facebook.com/groups/paused",
            )
        )
        active = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="active",
                canonical_url="https://www.facebook.com/groups/active",
            )
        )
        app.services.targets.restart_target_monitoring(paused.id)
        app.services.targets.clear_target_scan_request(paused.id)
        app.services.targets.mark_target_metadata_refresh_pending(paused.id)
        app.services.targets.pause_target_monitoring(paused.id)
        app.services.targets.restart_target_monitoring(active.id)

    class FakePlaywrightManager:
        """測試用 async_playwright context manager。"""

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    async def fake_launch_persistent_context_async(
        _playwright: object,
        _options: object,
    ) -> RuntimeClosedOnPausedBrowserContext:
        context = RuntimeClosedOnPausedBrowserContext()
        contexts.append(context)
        return context

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        nonlocal scan_calls
        scan_calls += 1
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    def stop_after_scan_success(summary: Any) -> None:
        if summary.success_count > 0:
            stop_event.set()

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch_persistent_context_async,
    )

    async def run_test() -> None:
        await asyncio.wait_for(
            run_resident_main_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    scheduler_tick_seconds=0,
                ),
                scan_page=fake_scan_page,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_scan_success,
            ),
            timeout=2,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        paused_state = app.repositories.runtime_states.get(paused.id)
        active_state = app.repositories.runtime_states.get(active.id)
        paused_latest_scan = app.repositories.scan_runs.latest_by_target(paused.id)

    assert len(contexts) == 1
    assert contexts[0].closed is True
    assert scan_calls == 1
    assert paused_state is not None
    assert active_state is not None
    assert paused_state.runtime_status == TargetRuntimeStatus.IDLE
    assert active_state.runtime_status == TargetRuntimeStatus.IDLE
    assert paused_latest_scan is None


def test_paused_cover_runtime_failure_does_not_starve_active_scan(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """paused target 的 cover job 不可重啟 runtime 並擋住 active scan。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[RuntimeClosedOnPausedBrowserContext] = []
    stop_event = asyncio.Event()
    scan_calls = 0

    with SqliteApplicationContext(db_path) as app:
        paused = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="paused",
                canonical_url="https://www.facebook.com/groups/paused",
                group_cover_image_url="https://scontent.xx.fbcdn.net/paused.jpg",
            )
        )
        active = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="active",
                canonical_url="https://www.facebook.com/groups/active",
            )
        )
        app.services.targets.restart_target_monitoring(paused.id)
        app.services.targets.clear_target_scan_request(paused.id)
        app.services.targets.request_target_cover_image_refresh(
            paused.id,
            reported_url="https://scontent.xx.fbcdn.net/paused.jpg",
            min_interval_seconds=21600,
        )
        app.services.targets.pause_target_monitoring(paused.id)
        app.services.targets.restart_target_monitoring(active.id)

    class FakePlaywrightManager:
        """測試用 async_playwright context manager。"""

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    async def fake_launch_persistent_context_async(
        _playwright: object,
        _options: object,
    ) -> RuntimeClosedOnPausedBrowserContext:
        context = RuntimeClosedOnPausedBrowserContext()
        contexts.append(context)
        return context

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        nonlocal scan_calls
        scan_calls += 1
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    def stop_after_scan_success(summary: Any) -> None:
        if summary.success_count > 0:
            stop_event.set()

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch_persistent_context_async,
    )

    async def run_test() -> None:
        await asyncio.wait_for(
            run_resident_main_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    scheduler_tick_seconds=0,
                ),
                scan_page=fake_scan_page,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_scan_success,
            ),
            timeout=2,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        paused_state = app.repositories.runtime_states.get(paused.id)
        active_state = app.repositories.runtime_states.get(active.id)
        paused_latest_scan = app.repositories.scan_runs.latest_by_target(paused.id)
        cover_state = app.repositories.cover_image_refreshes.get(paused.id)

    assert len(contexts) == 1
    assert contexts[0].closed is True
    assert scan_calls == 1
    assert paused_state is not None
    assert active_state is not None
    assert paused_state.runtime_status == TargetRuntimeStatus.IDLE
    assert active_state.runtime_status == TargetRuntimeStatus.IDLE
    assert paused_latest_scan is None
    assert cover_state is not None
    assert cover_state.status == TargetCoverImageRefreshStatus.PENDING


def test_runtime_restart_pending_retry_preserves_failure_streak(
    tmp_path: Path,
) -> None:
    """runtime restart 取消 queued retry 時，不可清掉既有 failure streak。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="queued",
                canonical_url="https://www.facebook.com/groups/queued",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        state = app.services.targets.mark_target_queued(target.id, "manual")
        app.repositories.runtime_states.save(
            replace(
                state,
                scan_requested_at=None,
                consecutive_failure_reason=SCHEDULER_RUNTIME_REASON,
                consecutive_failure_count=2,
            )
        )

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試只檢查 retry helper，不會執行掃描。"""

        raise AssertionError("scan should not run")

    executor = ExecutorWorkerPool(
        options=ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
        ),
        page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
        target_queue=TargetQueue(),
        schedule_planner=TargetSchedulePlanner(),
        scan_page=scan_page,
    )
    executor._request_target_retry_after_runtime_restart(target.id)

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.runtime_states.get(target.id)

    assert updated is not None
    assert updated.runtime_status == TargetRuntimeStatus.IDLE
    assert updated.scan_requested_at is not None
    assert updated.consecutive_failure_reason == SCHEDULER_RUNTIME_REASON
    assert updated.consecutive_failure_count == 2


def test_resident_scheduler_tick_refreshes_requested_target_metadata(tmp_path: Path) -> None:
    """resident scheduler 會用既有 browser context 補齊 fallback target name。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("metadata refresh should not enqueue scans")

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                metadata_refresh_provider=lambda: (target.id,),
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=scan_page,
        )
        await executor.start()
        try:
            metadata_context = FakeMetadataBrowserContext()
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                browser_context=metadata_context,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.closed_page_count == 1
        assert summary.metadata_refresh_count == 1
        assert summary.cover_image_refresh_count == 0
        assert len(metadata_context.pages) == 1
        assert metadata_context.pages[0].url == "https://www.facebook.com/groups/222518561920110"
        assert metadata_context.pages[0].closed

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
    assert updated is not None
    assert updated.name == "測試社團"
    assert updated.group_name == "測試社團"
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"
    assert updated.metadata_status == TargetMetadataStatus.RESOLVED
    assert updated.metadata_error == ""


def test_metadata_refresh_defers_while_failure_retry_scan_is_pending(
    tmp_path: Path,
) -> None:
    """page retry 等待期間，maintenance metadata refresh 不應搶先執行。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="retrying",
                canonical_url="https://www.facebook.com/groups/retrying",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.mark_target_metadata_refresh_pending(target.id)
        decision = app.services.targets.decide_scan_failure(
            target.id,
            PAGE_LOAD_TIMEOUT_REASON,
            source="playwright",
        )
        app.services.targets.apply_scan_failure_decision(
            target.id,
            decision,
            "page load timeout",
        )

    context = FakeMetadataBrowserContext()
    refreshed_count = asyncio.run(
        refresh_requested_target_metadata(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            browser_context=context,
        )
    )

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
        state = app.repositories.runtime_states.get(target.id)

    assert refreshed_count == 0
    assert context.pages == []
    assert updated is not None
    assert updated.metadata_status == TargetMetadataStatus.PENDING
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.scan_requested_at is not None
    assert state.consecutive_failure_reason == PAGE_LOAD_TIMEOUT_REASON
    assert state.consecutive_failure_count == 1


def test_resident_scheduler_tick_dispatches_existing_pending_outbox(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """resident tick 會 drain 已存在 pending outbox，補上 after-commit hook 漏跑情境。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:item-1:ntfy",
                target_id=target.id,
                item_key="item-1",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )
    dispatched_db_paths: list[Path] = []

    def fake_dispatch(**kwargs: object) -> int:
        db_path_arg = kwargs["db_path"]
        assert isinstance(db_path_arg, Path)
        dispatched_db_paths.append(db_path_arg)
        return 1

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.dispatch_new_pending_notification_outbox_for_db",
        fake_dispatch,
    )

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("pending outbox dispatch should not enqueue scans")

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=scan_page,
        )
        await executor.start()
        try:
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                browser_context=None,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.notification_dispatch_count == 1

    asyncio.run(run_test())

    assert dispatched_db_paths == [db_path]


def test_dispatch_pending_notification_outbox_treats_sqlite_lock_as_transient(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    caplog: Any,
) -> None:
    """resident tick 遇到暫時性 SQLite lock 時保留 pending outbox 給下輪重試。"""

    db_path = tmp_path / "app.db"

    def raise_locked(**_kwargs: object) -> int:
        """模擬 dispatch 開頭遇到其他 writer 持鎖。"""

        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.dispatch_new_pending_notification_outbox_for_db",
        raise_locked,
    )

    with caplog.at_level(logging.WARNING, logger="facebook_monitor.worker.resident_main"):
        dispatched_count = dispatch_pending_notification_outbox(
            ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            )
        )

    assert dispatched_count == 0
    assert "database locked" in caplog.text
    assert "Traceback" not in caplog.text


def test_bounded_retention_maintenance_runs_once_per_interval(
    tmp_path: Path,
) -> None:
    """resident bounded retention 每個 DB path 依 interval 節流。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        outbox = app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:old:desktop",
                target_id=target.id,
                item_key="old",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.DESKTOP,
                title="old",
                message="old",
            )
        )
        assert outbox.id is not None
        app.repositories.notification_outbox.mark_result(
            entry_id=outbox.id,
            status=NotificationOutboxStatus.SENT,
            attempts=1,
        )
        app.repositories.notification_outbox.connection.execute(
            """
            UPDATE notification_outbox
            SET updated_at = ?
            WHERE id = ?
            """,
            (encode_datetime(utc_now() - timedelta(days=8)), outbox.id),
        )

    options = ResidentRuntimeOptions(
        db_path=db_path,
        profile_dir=tmp_path / "profile",
    )
    first_deleted = run_bounded_retention_maintenance_if_due(options)
    second_deleted = run_bounded_retention_maintenance_if_due(options)

    with SqliteApplicationContext(db_path) as app:
        remaining_outbox_count = app.repositories.notification_outbox.connection.execute(
            "SELECT COUNT(*) FROM notification_outbox"
        ).fetchone()[0]

    assert first_deleted == 1
    assert second_deleted == 0
    assert remaining_outbox_count == 0


def test_resident_scheduler_tick_refreshes_pending_custom_named_target_cover(
    tmp_path: Path,
) -> None:
    """手動 metadata refresh 不因已有名稱而跳過封面抓取。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="1370511589953459",
                parent_post_id="2772468963091041",
                canonical_url=(
                    "https://www.facebook.com/groups/1370511589953459/"
                    "posts/2772468963091041"
                ),
                name="自訂留言 target",
                group_name="既有社團名稱",
            )
        )
        app.services.targets.mark_target_metadata_refresh_pending(target.id)

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("metadata refresh should not enqueue scans")

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=scan_page,
        )
        await executor.start()
        try:
            metadata_context = FakeMetadataBrowserContext()
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                browser_context=metadata_context,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.closed_page_count == 1
        assert summary.metadata_refresh_count == 1
        assert summary.cover_image_refresh_count == 0
        assert len(metadata_context.pages) == 1
        assert metadata_context.pages[0].url == "https://www.facebook.com/groups/1370511589953459"

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
    assert updated is not None
    assert updated.name == "測試社團"
    assert updated.group_name == "測試社團"
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"
    assert updated.metadata_status == TargetMetadataStatus.RESOLVED
    assert updated.metadata_error == ""


def test_resident_scheduler_tick_refreshes_cover_image_without_renaming_target(
    tmp_path: Path,
) -> None:
    """自動 cover-only refresh 不覆蓋使用者自訂 target 名稱。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的自訂名稱",
                group_name="既有社團名稱",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("cover image refresh should not enqueue scans")

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=scan_page,
        )
        await executor.start()
        try:
            metadata_context = FakeMetadataBrowserContext()
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                browser_context=metadata_context,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.closed_page_count == 1
        assert summary.metadata_refresh_count == 0
        assert summary.cover_image_refresh_count == 1
        assert len(metadata_context.pages) == 1
        assert metadata_context.pages[0].url == "https://www.facebook.com/groups/222518561920110"

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
        state = app.repositories.cover_image_refreshes.get(target.id)
    assert updated is not None
    assert updated.name == "我的自訂名稱"
    assert updated.group_name == "既有社團名稱"
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"
    assert updated.metadata_status == TargetMetadataStatus.RESOLVED
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.IDLE
    assert state.last_reported_url == "https://scontent.xx.fbcdn.net/old.jpg"
    assert state.last_resolved_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"
    assert state.last_result == "succeeded_changed"
    assert state.changed is True


def test_resident_scheduler_tick_skips_stale_cover_image_refresh_job(
    tmp_path: Path,
) -> None:
    """worker 消化前 URL 已被更新時，不應用舊壞圖 job 再開 Facebook。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的自訂名稱",
                group_name="既有社團名稱",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )
        app.services.targets.refresh_target_group_cover_image(
            target.id,
            "https://scontent.xx.fbcdn.net/manual-new.jpg",
        )

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("stale cover image refresh should not enqueue scans")

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=scan_page,
        )
        await executor.start()
        try:
            metadata_context = FakeMetadataBrowserContext()
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                browser_context=metadata_context,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.closed_page_count == 0
        assert summary.metadata_refresh_count == 0
        assert summary.cover_image_refresh_count == 0
        assert metadata_context.pages == []

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
        state = app.repositories.cover_image_refreshes.get(target.id)
    assert updated is not None
    assert updated.name == "我的自訂名稱"
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/manual-new.jpg"
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.IDLE
    assert state.last_reported_url == "https://scontent.xx.fbcdn.net/old.jpg"
    assert state.last_resolved_url == "https://scontent.xx.fbcdn.net/manual-new.jpg"
    assert state.last_result == "stale_skipped"
    assert state.changed is False


def test_cover_image_refresh_stale_worker_does_not_clear_newer_request(
    tmp_path: Path,
) -> None:
    """舊 worker 只能完成自己讀到的 cover refresh request，不可清掉較新的 pending row。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的自訂名稱",
                group_name="既有社團名稱",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )
        stale_worker_state = app.repositories.cover_image_refreshes.get(target.id)
        app.services.targets.refresh_target_group_cover_image(
            target.id,
            "https://scontent.xx.fbcdn.net/new-current.jpg",
        )
        app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/new-current.jpg",
            min_interval_seconds=21600,
        )
    assert stale_worker_state is not None

    async def run_test() -> bool:
        """以舊 state 跑一次 worker refresh，模擬途中又收到新壞圖 request。"""

        return await refresh_target_group_cover_image_from_context(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            browser_context=FakeMetadataBrowserContext(),
            state=stale_worker_state,
        )

    refreshed = asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.cover_image_refreshes.get(target.id)
    assert refreshed is False
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.PENDING
    assert state.last_reported_url == "https://scontent.xx.fbcdn.net/new-current.jpg"
    assert state.last_result == "queued"
    assert state.last_resolved_url == ""


def test_resident_scheduler_tick_records_cover_image_refresh_failure(
    tmp_path: Path,
) -> None:
    """cover-only refresh 失敗要留在獨立狀態，不污染 target metadata 狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的自訂名稱",
                group_name="既有社團名稱",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("cover image refresh failure should not enqueue scans")

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=scan_page,
        )
        await executor.start()
        try:
            metadata_context = FakeLoggedOutMetadataBrowserContext()
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                browser_context=metadata_context,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.closed_page_count == 0
        assert summary.metadata_refresh_count == 0
        assert summary.cover_image_refresh_count == 0
        assert len(metadata_context.pages) == 1

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
        state = app.repositories.cover_image_refreshes.get(target.id)
    assert updated is not None
    assert updated.name == "我的自訂名稱"
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/old.jpg"
    assert updated.metadata_status == TargetMetadataStatus.RESOLVED
    assert updated.metadata_error == ""
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.FAILED
    assert state.last_reported_url == "https://scontent.xx.fbcdn.net/old.jpg"
    assert state.last_resolved_url == ""
    assert state.last_result == "failed"
    assert state.changed is False
    assert "Facebook 尚未登入" in state.error


def test_resident_scheduler_tick_keeps_cover_refresh_pending_on_shutdown(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """scheduler 停止造成的 Playwright 關閉不應被記成 cover refresh 失敗。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的自訂名稱",
                group_name="既有社團名稱",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("shutdown should stop before enqueueing scans")

    async def run_test() -> None:
        stop_requested = False

        def request_stop() -> None:
            nonlocal stop_requested
            stop_requested = True

        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=scan_page,
        )
        await executor.start()
        try:
            shutdown_context = FakeShutdownMetadataBrowserContext(request_stop)
            with caplog.at_level(
                logging.INFO,
                logger="facebook_monitor.worker.resident_main",
            ):
                summary = await run_resident_main_scheduler_tick(
                    options=executor.options,
                    browser_context=shutdown_context,
                    page_pool=page_pool,
                    target_queue=target_queue,
                    executor=executor,
                    schedule_planner=planner,
                    cycle_index=1,
                    should_stop=lambda: stop_requested,
                )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.metadata_refresh_count == 0
        assert summary.cover_image_refresh_count == 0
        assert shutdown_context.new_page_count == 1

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.cover_image_refreshes.get(target.id)
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.PENDING
    assert state.last_result == TargetCoverImageRefreshResult.ATTEMPTED
    assert state.error == ""
    assert "cover image refresh skipped because scheduler is stopping" in caplog.text
    assert "cover image refresh failed" not in caplog.text


def test_resident_scheduler_tick_marks_pending_metadata_failed(tmp_path: Path) -> None:
    """resident metadata refresh 失敗會寫回 failed，避免 UI 永久顯示等待。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app.services.targets.mark_target_metadata_refresh_pending(target.id)

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("metadata refresh should not enqueue scans")

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=scan_page,
        )
        await executor.start()
        try:
            metadata_context = FakeLoggedOutMetadataBrowserContext()
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                browser_context=metadata_context,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.closed_page_count == 0
        assert len(metadata_context.pages) == 1
        assert metadata_context.pages[0].closed

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
    assert updated is not None
    assert updated.metadata_status == TargetMetadataStatus.FAILED
    assert "Facebook 尚未登入" in updated.metadata_error


def test_target_queue_snapshot_keeps_enqueue_order() -> None:
    """TargetQueue diagnostics 應保留排隊順序，供 runtime 診斷使用。"""

    async def run_test() -> None:
        """建立三筆 queue item 並檢查 snapshot 順序。"""

        target_queue = TargetQueue()
        for target_id in ("target-a", "target-b", "target-c"):
            accepted = await target_queue.enqueue(
                QueueItem(
                    due_target=DueTarget(
                        target_id=target_id,
                        interval_seconds=60,
                        due_at=utc_now(),
                        scan_requested=False,
                    ),
                    enqueue_reason="due",
                    enqueued_at=utc_now(),
                )
            )
            assert accepted
        queued_count, running_count, queued_ids = await target_queue.snapshot()
        assert queued_count == 3
        assert running_count == 0
        assert queued_ids == ("target-a", "target-b", "target-c")

    asyncio.run(run_test())


def test_target_queue_old_owner_complete_does_not_clear_new_attempt() -> None:
    """舊 attempt complete 不可移除新 attempt 尚未 bind 的 running guard。"""

    async def run_test() -> None:
        target_queue = TargetQueue()
        first_item = QueueItem(
            due_target=DueTarget(
                target_id="target-a",
                interval_seconds=60,
                due_at=utc_now(),
            ),
            enqueue_reason="due",
            enqueued_at=utc_now(),
        )
        assert await target_queue.enqueue(first_item)
        assert await target_queue.get() is not None
        await target_queue.bind_running_owner("target-a", "old-owner")
        assert await target_queue.release_running_if_owner("target-a", "old-owner")

        second_item = QueueItem(
            due_target=DueTarget(
                target_id="target-a",
                interval_seconds=60,
                due_at=utc_now(),
            ),
            enqueue_reason="retry",
            enqueued_at=utc_now(),
        )
        assert await target_queue.enqueue(second_item)
        assert await target_queue.get() is not None

        await target_queue.complete("target-a", owner_key="old-owner")
        _queued_count, running_count, _queued_ids = await target_queue.snapshot()
        assert running_count == 1

        await target_queue.bind_running_owner("target-a", "new-owner")
        await target_queue.complete("target-a", owner_key="new-owner")
        _queued_count, running_count, _queued_ids = await target_queue.snapshot()
        assert running_count == 0

    asyncio.run(run_test())


def test_async_resident_dispatches_schedule_after_running_lock(tmp_path: Path) -> None:
    """async resident 進 queue 時不推進 next_due_at，取得 running lock 後才推進。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = RecordingSchedulePlanner()
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=60,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=fake_scan_page,
        )
        due_target = DueTarget(
            target_id=target.id,
            interval_seconds=60,
            due_at=utc_now(),
        )
        enqueued_count = await executor.enqueue_due_targets((due_target,))
        assert enqueued_count == 1
        assert planner.dispatched_target_ids == []
        with SqliteApplicationContext(db_path) as app:
            queued_state = app.repositories.runtime_states.get(target.id)
        assert queued_state is not None
        assert queued_state.runtime_status == TargetRuntimeStatus.QUEUED

        item = await target_queue.get()
        assert item is not None
        result = await executor._run_queue_item("worker-1", item)  # noqa: SLF001
        assert result.success
        assert planner.dispatched_target_ids == [target.id]

    asyncio.run(run_test())


def test_stale_recovery_cancels_attempt_stuck_in_page_prepare(tmp_path: Path) -> None:
    """target restart recovery 應取消卡在 goto/reload 的整個 attempt。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    goto_started = asyncio.Event()
    goto_cancelled = asyncio.Event()

    class BlockingPreparePage(FakeAsyncPage):
        """第一個 page 會卡在 goto，直到 attempt 被 recovery 取消。"""

        async def goto(self, url: str, wait_until: str, timeout: float) -> None:
            self.url = url.rstrip("/")
            self.goto_count += 1
            goto_started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                goto_cancelled.set()
                raise

    class FirstPageBlocksContext(FakeAsyncBrowserContext):
        """第一個 page 卡住，後續 page 正常完成。"""

        async def new_page(self) -> FakeAsyncPage:
            page: FakeAsyncPage
            if not self.pages:
                page = BlockingPreparePage()
            else:
                page = FakeAsyncPage()
            self.pages.append(page)
            return page

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FirstPageBlocksContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
                max_concurrent_scans=1,
                stale_running_after_seconds=180,
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=fake_scan_page,
        )
        await executor.start()
        try:
            await run_resident_main_scheduler_tick(
                options=executor.options,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
            await asyncio.wait_for(goto_started.wait(), timeout=1)
            with SqliteApplicationContext(db_path) as app:
                state = app.repositories.runtime_states.get(target.id)
                assert state is not None
                app.repositories.runtime_states.save(
                    replace(
                        state,
                        last_heartbeat_at=now - timedelta(seconds=240),
                        updated_at=now - timedelta(seconds=240),
                    )
                )

            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=2,
            )
            await asyncio.wait_for(goto_cancelled.wait(), timeout=1)
            await asyncio.wait_for(target_queue.join(), timeout=1)
        finally:
            await executor.stop()

        assert summary.recovered_runtime_count == 1
        with SqliteApplicationContext(db_path) as app:
            recovered_state = app.repositories.runtime_states.get(target.id)
        assert recovered_state is not None
        assert recovered_state.runtime_status == TargetRuntimeStatus.IDLE

    asyncio.run(run_test())


def test_stale_recovery_cancels_attempt_stuck_in_new_page(tmp_path: Path) -> None:
    """page 建立階段卡住時，也要能靠 running owner recovery 取消。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    new_page_started = asyncio.Event()
    new_page_cancelled = asyncio.Event()

    class FirstNewPageBlocksContext(FakeAsyncBrowserContext):
        """第一次建立 page 卡住，後續 page 正常。"""

        def __init__(self) -> None:
            super().__init__()
            self.blocked_once = False

        async def new_page(self) -> FakeAsyncPage:
            if not self.blocked_once:
                self.blocked_once = True
                new_page_started.set()
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    new_page_cancelled.set()
                    raise
            return await super().new_page()

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FirstNewPageBlocksContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
                max_concurrent_scans=1,
                stale_running_after_seconds=180,
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=fake_scan_page,
        )
        await executor.start()
        try:
            await run_resident_main_scheduler_tick(
                options=executor.options,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
            await asyncio.wait_for(new_page_started.wait(), timeout=1)
            with SqliteApplicationContext(db_path) as app:
                state = app.repositories.runtime_states.get(target.id)
                assert state is not None
                assert state.runtime_status == TargetRuntimeStatus.RUNNING
                app.repositories.runtime_states.save(
                    replace(
                        state,
                        last_heartbeat_at=now - timedelta(seconds=240),
                        updated_at=now - timedelta(seconds=240),
                    )
                )

            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=2,
            )
            await asyncio.wait_for(new_page_cancelled.wait(), timeout=1)
            await asyncio.wait_for(target_queue.join(), timeout=1)
        finally:
            await executor.stop()

        assert summary.recovered_runtime_count == 1
        with SqliteApplicationContext(db_path) as app:
            recovered_state = app.repositories.runtime_states.get(target.id)
        assert recovered_state is not None
        assert recovered_state.runtime_status == TargetRuntimeStatus.IDLE

    asyncio.run(run_test())


def test_async_resident_consumes_manual_scan_request_when_enqueued(
    tmp_path: Path,
) -> None:
    """manual scan request 進入 executor queue 時先清除，避免目前掃描完成後重跑。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    async def run_test() -> None:
        target_queue = TargetQueue()
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=60,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            target_queue=target_queue,
            schedule_planner=TargetSchedulePlanner(),
            scan_page=fake_scan_page,
        )
        enqueued_count = await executor.enqueue_due_targets(
            (
                DueTarget(
                    target_id=target.id,
                    interval_seconds=60,
                    due_at=utc_now(),
                    scan_requested=True,
                ),
            )
        )

        assert enqueued_count == 1
        with SqliteApplicationContext(db_path) as app:
            queued_state = app.repositories.runtime_states.get(target.id)
        assert queued_state is not None
        assert queued_state.runtime_status == TargetRuntimeStatus.QUEUED
        assert queued_state.scan_requested_at is None

    asyncio.run(run_test())


def test_resident_main_page_reload_keeps_same_group_feed_sorting_url() -> None:
    """resident main 同一 group feed 帶 sorting query 時應 reload，不應 goto。"""

    async def run_test() -> None:
        """建立 fake page 並檢查 prepare_resident_main_page 的導航行為。"""

        page = FakeAsyncPage()
        page.url = "https://www.facebook.com/groups/111/?sorting_setting=CHRONOLOGICAL"
        resident_target = ResidentTarget(
            target=TargetDescriptor.for_group_posts(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            ),
            config=TargetConfig(target_id="target-1"),
        )

        await prepare_resident_main_page(
            page=page,
            target=resident_target,
            timeout_ms=1000,
        )

        assert page.reload_count == 1
        assert page.goto_count == 0

    asyncio.run(run_test())


def test_resident_main_page_reload_keeps_same_comment_post_url() -> None:
    """resident main 同一 comments parent post 應 reload，不應 goto canonical URL。"""

    async def run_test() -> None:
        """建立 comments target fake page 並檢查 reload 判斷。"""

        page = FakeAsyncPage()
        page.url = "https://www.facebook.com/groups/11111111/posts/99999999?comment_id=12345678"
        resident_target = ResidentTarget(
            target=TargetDescriptor.for_comments(
                group_id="11111111",
                parent_post_id="99999999",
                canonical_url="https://www.facebook.com/groups/11111111/posts/99999999",
            ),
            config=TargetConfig(target_id="target-1"),
        )

        await prepare_resident_main_page(
            page=page,
            target=resident_target,
            timeout_ms=1000,
        )

        assert page.reload_count == 1
        assert page.goto_count == 0

    asyncio.run(run_test())


def test_resident_main_cycle_runs_due_targets_concurrently(tmp_path: Path) -> None:
    """resident main cycle 會以 max_concurrent_scans 讓多 target 同時掃描。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app.services.targets.restart_target_monitoring(first.id)
        app.services.targets.restart_target_monitoring(second.id)

    active_count = 0
    max_active_count = 0
    scanned_target_ids: list[str] = []

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        """記錄同時執行數，證明 executor 不是序列化掃描。"""

        nonlocal active_count, max_active_count
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        scanned_target_ids.append(kwargs["target"].id)
        await asyncio.sleep(0.01)
        active_count -= 1
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    async def run_test() -> None:
        summary = await run_resident_main_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
                max_concurrent_scans=2,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            scan_page=fake_scan_page,
            schedule_planner=TargetSchedulePlanner(),
            cycle_index=1,
        )
        assert summary.selected_count == 2
        assert summary.success_count == 2

    asyncio.run(run_test())

    assert set(scanned_target_ids) == {first.id, second.id}
    assert max_active_count == 2
    with SqliteApplicationContext(db_path) as app:
        first_state = app.repositories.runtime_states.get(first.id)
        second_state = app.repositories.runtime_states.get(second.id)
    assert first_state is not None
    assert second_state is not None
    assert first_state.runtime_status == TargetRuntimeStatus.IDLE
    assert second_state.runtime_status == TargetRuntimeStatus.IDLE
    assert first_state.last_page_reloaded_at is not None
    assert second_state.last_page_reloaded_at is not None


def test_resident_main_cycle_dispatches_comments_target_to_comments_worker(
    tmp_path: Path,
) -> None:
    """D4 comments target 會進 resident queue，並派發到 comments scan callable。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="11111111",
                parent_post_id="99999999",
                canonical_url="https://www.facebook.com/groups/11111111/posts/99999999",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    post_calls: list[str] = []
    comment_calls: list[str] = []

    async def fake_post_scan_page(**kwargs: Any) -> PostsScanSummary:
        """若 comments target 被錯派到 posts worker，測試應失敗。"""

        post_calls.append(kwargs["target"].id)
        raise AssertionError("comments target should not use posts scan callable")

    async def fake_comment_scan_page(**kwargs: Any) -> CommentsScanSummary:
        """記錄 comments worker 派發結果。"""

        comment_calls.append(kwargs["target"].id)
        return CommentsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    async def run_test() -> None:
        summary = await run_resident_main_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
                max_concurrent_scans=1,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            scan_page=fake_post_scan_page,
            scan_comments_target_page=fake_comment_scan_page,
            schedule_planner=TargetSchedulePlanner(),
            cycle_index=1,
        )
        assert summary.selected_count == 1
        assert summary.success_count == 1

    asyncio.run(run_test())

    assert post_calls == []
    assert comment_calls == [target.id]
    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE


def test_resident_main_cycle_reuses_page_and_reloads_same_group_feed(
    tmp_path: Path,
) -> None:
    """resident main 跨 cycle 應重用同 target page，並以 reload 保留排序頁狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    scan_calls = 0

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        """模擬 auto_adjust_sort 後 page URL 帶 sorting query。"""

        nonlocal scan_calls
        scan_calls += 1
        page = kwargs["page"]
        page.url = "https://www.facebook.com/groups/111/?sorting_setting=CHRONOLOGICAL"
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=page.url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=scan_calls,
            round_stats=(),
        )

    async def run_test() -> None:
        context = FakeAsyncBrowserContext()
        page_pool = AsyncResidentPagePool(context)
        planner = TargetSchedulePlanner()
        options = ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            interval_seconds=3600,
            max_concurrent_scans=1,
        )

        first_summary = await run_resident_main_cycle(
            options=options,
            page_pool=page_pool,
            scan_page=fake_scan_page,
            schedule_planner=planner,
            cycle_index=1,
        )
        with SqliteApplicationContext(db_path) as app:
            app.services.targets.request_target_scan(target.id)
        second_summary = await run_resident_main_cycle(
            options=options,
            page_pool=page_pool,
            scan_page=fake_scan_page,
            schedule_planner=planner,
            cycle_index=2,
        )

        assert first_summary.selected_count == 1
        assert first_summary.success_count == 1
        assert first_summary.opened_page_count == 1
        assert first_summary.reused_page_count == 0
        assert second_summary.selected_count == 1
        assert second_summary.success_count == 1
        assert second_summary.opened_page_count == 0
        assert second_summary.reused_page_count == 1
        assert len(context.pages) == 1
        assert context.pages[0].goto_count == 1
        assert context.pages[0].reload_count == 1

    asyncio.run(run_test())

    assert scan_calls == 2
    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
    assert state is not None
    assert state.last_page_reloaded_at is not None


def test_resident_main_executor_keeps_third_target_queued(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """queue-based executor 會讓兩個 target running，第三個保持 queued。"""

    caplog.set_level(logging.INFO, logger="facebook_monitor.worker")
    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        targets = [
            app.services.targets.upsert_group_posts_target(
                UpsertGroupPostsTargetRequest(
                    group_id=str(index),
                    canonical_url=f"https://www.facebook.com/groups/{index}",
                )
            )
            for index in (111, 222, 333)
        ]
        for target in targets:
            app.services.targets.restart_target_monitoring(target.id)

    started = asyncio.Event()
    release = asyncio.Event()
    active_count = 0

    async def blocking_scan_page(**kwargs: Any) -> PostsScanSummary:
        """讓前兩個 worker 保持 running，方便檢查第三個 target queued。"""

        nonlocal active_count
        active_count += 1
        if active_count == 2:
            started.set()
        await release.wait()
        active_count -= 1
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
                max_concurrent_scans=2,
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=blocking_scan_page,
        )
        await executor.start()
        try:
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            with SqliteApplicationContext(db_path) as app:
                states = [app.repositories.runtime_states.get(target.id) for target in targets]
            assert summary.selected_count == 3
            assert sum(
                1
                for state in states
                if state is not None and state.runtime_status == TargetRuntimeStatus.RUNNING
            ) == 2
            assert sum(
                1
                for state in states
                if state is not None and state.runtime_status == TargetRuntimeStatus.QUEUED
            ) == 1
            release.set()
            await target_queue.join()
        finally:
            await executor.stop()

    asyncio.run(run_test())
    log_text = caplog.text
    assert "resident_executor_start max_concurrent_scans=2" in log_text
    assert "resident_target_enqueued target_id=" in log_text
    assert "resident_target_running target_id=" in log_text
    assert "resident_scheduler_tick cycle=1 selected=3" in log_text


def test_resident_main_scan_timeout_retries_until_third_failure(tmp_path: Path) -> None:
    """scan_timeout_seconds 會中止卡住的 scan，並重啟 page 後重試。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def slow_scan_page(**kwargs: Any) -> PostsScanSummary:
        await asyncio.sleep(0.2)
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    async def run_test() -> None:
        context = FakeAsyncBrowserContext()
        page_pool = AsyncResidentPagePool(context)
        for attempt in range(1, 4):
            with SqliteApplicationContext(db_path) as app:
                app.services.targets.request_target_scan(target.id)
            summary = await run_resident_main_cycle(
                options=ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=tmp_path / "profile",
                    interval_seconds=0,
                    scan_timeout_seconds=0.01,
                    heartbeat_interval_seconds=0.01,
                ),
                page_pool=page_pool,
                scan_page=slow_scan_page,
                schedule_planner=TargetSchedulePlanner(),
                cycle_index=attempt,
            )
            assert summary.failure_count == 1
            assert await page_pool.size() == 0
            assert context.pages[-1].closed is True

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.ERROR
    assert state.consecutive_failure_reason == "scan_timeout"
    assert state.consecutive_failure_count == 3
    assert "已連續 3 次失敗" in state.last_error
    assert latest_scan is not None
    assert "已連續 3 次失敗" in latest_scan.error_message
    assert "會重啟" not in latest_scan.error_message
    assert latest_scan.metadata["reason"] == "scan_timeout"
    assert latest_scan.metadata["runtime_action"] == "error"
    assert latest_scan.metadata["retryable"] is False
    assert latest_scan.metadata["retry_streak"] == 3
    assert latest_scan.metadata["retry_limit"] == 3


def test_resident_main_escalates_sort_skip_after_three_skipped_scans(
    tmp_path: Path,
) -> None:
    """async resident 的 sort skip 前兩次只跳過，第三次折算 recoverable failure。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def skipping_scan_page(**kwargs: Any) -> PostsScanSummary:
        result = record_skipped_scan(
            app=kwargs["app"],
            target=kwargs["target"],
            metadata={
                "worker": "resident_main",
                "skip_reason": SORT_ADJUST_UNCONFIRMED_REASON,
            },
            commit_guard=kwargs["commit_guard"],
        )
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=str(kwargs["page"].url),
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=result.scan_run_id,
            round_stats=(),
        )

    async def run_test() -> None:
        context = FakeAsyncBrowserContext()
        page_pool = AsyncResidentPagePool(context)
        for attempt in range(1, 4):
            with SqliteApplicationContext(db_path) as app:
                app.services.targets.request_target_scan(target.id)
            summary = await run_resident_main_cycle(
                options=ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=tmp_path / "profile",
                    interval_seconds=0,
                ),
                page_pool=page_pool,
                scan_page=skipping_scan_page,
                schedule_planner=TargetSchedulePlanner(),
                cycle_index=attempt,
            )
            with SqliteApplicationContext(db_path) as app:
                state = app.repositories.runtime_states.get(target.id)
                latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
            assert state is not None
            assert latest_scan is not None
            if attempt < 3:
                assert summary.failure_count == 0
                assert summary.skipped_count == 1
                assert latest_scan.status == ScanStatus.SUCCESS
                assert state.consecutive_scan_skip_count == attempt
            else:
                assert summary.failure_count == 1
                assert latest_scan.status == ScanStatus.FAILED
                assert latest_scan.metadata["reason"] == SORT_ADJUST_UNCONFIRMED_REASON
                assert latest_scan.metadata["retry_streak"] == 1
                assert state.runtime_status == TargetRuntimeStatus.IDLE
                assert state.consecutive_failure_count == 1
                assert state.consecutive_scan_skip_count == 0
                assert await page_pool.size() == 0
                assert context.pages[-1].closed is True

    asyncio.run(run_test())


def test_resident_main_page_load_timeout_retries_until_third_failure(
    tmp_path: Path,
) -> None:
    """page_load_timeout 前兩次只略過本輪，第三次才讓 target 進 error。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def failing_scan_page(**_kwargs: Any) -> PostsScanSummary:
        raise AsyncPlaywrightError(
            "Page.evaluate: Execution context was destroyed, "
            "most likely because of a navigation."
        )

    async def run_test() -> None:
        context = FakeAsyncBrowserContext()
        page_pool = AsyncResidentPagePool(context)
        for attempt in range(1, 4):
            with SqliteApplicationContext(db_path) as app:
                app.services.targets.request_target_scan(target.id)
            summary = await run_resident_main_cycle(
                options=ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=tmp_path / "profile",
                    interval_seconds=0,
                ),
                page_pool=page_pool,
                scan_page=failing_scan_page,
                schedule_planner=TargetSchedulePlanner(),
                cycle_index=attempt,
            )
            assert summary.failure_count == 1
            assert await page_pool.size() == 0
            assert context.pages[-1].closed is True

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.ERROR
    assert state.consecutive_failure_count == 3
    assert "已連續 3 次失敗" in state.last_error
    assert latest_scan is not None
    assert "已連續 3 次失敗" in latest_scan.error_message
    assert "Execution context was destroyed" not in latest_scan.error_message
    assert "會重啟" not in latest_scan.error_message
    assert latest_scan.metadata["reason"] == "page_load_timeout"
    assert latest_scan.metadata["retryable"] is False
    assert latest_scan.metadata["runtime_action"] == "error"
    assert latest_scan.metadata["retry_streak"] == 3
    assert latest_scan.metadata["retry_limit"] == 3
    assert "Execution context was destroyed" in latest_scan.metadata["raw_failure_detail"]


def test_resident_main_browser_context_closed_retries_until_third_failure(
    tmp_path: Path,
) -> None:
    """browser/context closed 應歸類為 scheduler_runtime，第三次才進 error。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def failing_scan_page(**_kwargs: Any) -> PostsScanSummary:
        raise AsyncPlaywrightError("Target page, context or browser has been closed")

    async def run_test() -> None:
        context = FakeAsyncBrowserContext()
        page_pool = AsyncResidentPagePool(context)
        for attempt in range(1, 4):
            with SqliteApplicationContext(db_path) as app:
                app.services.targets.request_target_scan(target.id)
            summary = await run_resident_main_cycle(
                options=ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=tmp_path / "profile",
                    interval_seconds=0,
                ),
                page_pool=page_pool,
                scan_page=failing_scan_page,
                schedule_planner=TargetSchedulePlanner(),
                cycle_index=attempt,
            )
            assert summary.failure_count == 1
            assert context.pages[-1].closed is True

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.ERROR
    assert state.consecutive_failure_reason == SCHEDULER_RUNTIME_REASON
    assert state.consecutive_failure_count == 3
    assert latest_scan is not None
    assert latest_scan.metadata["reason"] == SCHEDULER_RUNTIME_REASON
    assert latest_scan.metadata["runtime_action"] == "error"
    assert latest_scan.metadata["retry_streak"] == 3
    assert latest_scan.metadata["retry_limit"] == 3


def test_resident_main_cancels_scan_when_target_is_stopped(tmp_path: Path) -> None:
    """target 停止後，正在跑的 resident scan 會被 watchdog 取消且不寫失敗。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    started = asyncio.Event()

    async def blocking_scan_page(**kwargs: Any) -> PostsScanSummary:
        started.set()
        await asyncio.sleep(10)
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    async def run_test() -> None:
        task = asyncio.create_task(
            run_resident_main_cycle(
                options=ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=tmp_path / "profile",
                    interval_seconds=0,
                    scan_timeout_seconds=5,
                    heartbeat_interval_seconds=0.01,
                ),
                page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
                scan_page=blocking_scan_page,
                schedule_planner=TargetSchedulePlanner(),
                cycle_index=1,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        with SqliteApplicationContext(db_path) as app:
            app.services.targets.pause_target_monitoring(target.id)
        summary = await asyncio.wait_for(task, timeout=1)
        assert summary.failure_count == 0
        assert summary.skipped_count == 1

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.last_error == ""
