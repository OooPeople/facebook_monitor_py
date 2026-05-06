"""Phase C async resident worker tests。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import CreateCommentsTargetRequest
from facebook_monitor.application.services import CreateGroupPostsTargetRequest
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.scheduler.planner import DueTarget
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.comments import GroupCommentsScanSummary
from facebook_monitor.worker.async_resident import run_async_resident_worker_cycle
from facebook_monitor.worker.async_resident import run_async_resident_scheduler_tick
from facebook_monitor.worker.group_posts import GroupPostsScanSummary
from facebook_monitor.worker.resident_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_executor import prepare_async_resident_page
from facebook_monitor.worker.resident_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_queue import QueueItem
from facebook_monitor.worker.resident_queue import TargetQueue
from facebook_monitor.worker.resident import ResidentTarget
from facebook_monitor.worker.resident import ResidentWorkerOptions


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

    async def new_page(self) -> FakeAsyncPage:
        """建立 fake async page。"""

        page = FakeAsyncPage()
        self.pages.append(page)
        return page


def test_target_queue_snapshot_keeps_enqueue_order() -> None:
    """TargetQueue diagnostics 應保留排隊順序，供 UI 顯示 queue_position。"""

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


def test_async_resident_page_reload_keeps_same_group_feed_sorting_url() -> None:
    """async resident 同一 group feed 帶 sorting query 時應 reload，不應 goto。"""

    async def run_test() -> None:
        """建立 fake page 並檢查 prepare_async_resident_page 的導航行為。"""

        page = FakeAsyncPage()
        page.url = "https://www.facebook.com/groups/111/?sorting_setting=CHRONOLOGICAL"
        resident_target = ResidentTarget(
            target=TargetDescriptor.for_group_posts(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            ),
            config=TargetConfig(target_id="target-1"),
        )

        await prepare_async_resident_page(
            page=page,
            target=resident_target,
            timeout_ms=1000,
        )

        assert page.reload_count == 1
        assert page.goto_count == 0

    asyncio.run(run_test())


def test_async_resident_page_reload_keeps_same_comment_post_url() -> None:
    """async resident 同一 comments parent post 應 reload，不應 goto canonical URL。"""

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

        await prepare_async_resident_page(
            page=page,
            target=resident_target,
            timeout_ms=1000,
        )

        assert page.reload_count == 1
        assert page.goto_count == 0

    asyncio.run(run_test())


def test_async_resident_cycle_runs_due_targets_concurrently(tmp_path: Path) -> None:
    """async resident cycle 會以 max_concurrent_scans 讓多 target 同時掃描。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )

    active_count = 0
    max_active_count = 0
    scanned_target_ids: list[str] = []

    async def fake_scan_page(**kwargs: Any) -> GroupPostsScanSummary:
        """記錄同時執行數，證明 executor 不是序列化掃描。"""

        nonlocal active_count, max_active_count
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        scanned_target_ids.append(kwargs["target"].id)
        await asyncio.sleep(0.01)
        active_count -= 1
        return GroupPostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    async def run_test() -> None:
        summary = await run_async_resident_worker_cycle(
            options=ResidentWorkerOptions(
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


def test_async_resident_cycle_dispatches_comments_target_to_comments_worker(
    tmp_path: Path,
) -> None:
    """D4 comments target 會進 resident queue，並派發到 comments scan callable。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            CreateCommentsTargetRequest(
                group_id="11111111",
                parent_post_id="99999999",
                canonical_url="https://www.facebook.com/groups/11111111/posts/99999999",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    post_calls: list[str] = []
    comment_calls: list[str] = []

    async def fake_post_scan_page(**kwargs: Any) -> GroupPostsScanSummary:
        """若 comments target 被錯派到 posts worker，測試應失敗。"""

        post_calls.append(kwargs["target"].id)
        raise AssertionError("comments target should not use posts scan callable")

    async def fake_comment_scan_page(**kwargs: Any) -> GroupCommentsScanSummary:
        """記錄 comments worker 派發結果。"""

        comment_calls.append(kwargs["target"].id)
        return GroupCommentsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    async def run_test() -> None:
        summary = await run_async_resident_worker_cycle(
            options=ResidentWorkerOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
                max_concurrent_scans=1,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            scan_page=fake_post_scan_page,
            scan_comments_page=fake_comment_scan_page,
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


def test_async_resident_cycle_reuses_page_and_reloads_same_group_feed(
    tmp_path: Path,
) -> None:
    """async resident 跨 cycle 應重用同 target page，並以 reload 保留排序頁狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )

    scan_calls = 0

    async def fake_scan_page(**kwargs: Any) -> GroupPostsScanSummary:
        """模擬 auto_adjust_sort 後 page URL 帶 sorting query。"""

        nonlocal scan_calls
        scan_calls += 1
        page = kwargs["page"]
        page.url = "https://www.facebook.com/groups/111/?sorting_setting=CHRONOLOGICAL"
        return GroupPostsScanSummary(
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
        options = ResidentWorkerOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            interval_seconds=3600,
            max_concurrent_scans=1,
        )

        first_summary = await run_async_resident_worker_cycle(
            options=options,
            page_pool=page_pool,
            scan_page=fake_scan_page,
            schedule_planner=planner,
            cycle_index=1,
        )
        with SqliteApplicationContext(db_path) as app:
            app.services.targets.request_target_scan(target.id)
        second_summary = await run_async_resident_worker_cycle(
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


def test_async_resident_executor_keeps_third_target_queued(
    tmp_path: Path,
) -> None:
    """queue-based executor 會讓兩個 target running，第三個保持 queued。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        targets = [
            app.services.targets.upsert_group_posts_target(
                CreateGroupPostsTargetRequest(
                    group_id=str(index),
                    canonical_url=f"https://www.facebook.com/groups/{index}",
                )
            )
            for index in (111, 222, 333)
        ]

    started = asyncio.Event()
    release = asyncio.Event()
    active_count = 0

    async def blocking_scan_page(**kwargs: Any) -> GroupPostsScanSummary:
        """讓前兩個 worker 保持 running，方便檢查第三個 target queued。"""

        nonlocal active_count
        active_count += 1
        if active_count == 2:
            started.set()
        await release.wait()
        active_count -= 1
        return GroupPostsScanSummary(
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
            options=ResidentWorkerOptions(
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
            summary = await run_async_resident_scheduler_tick(
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
