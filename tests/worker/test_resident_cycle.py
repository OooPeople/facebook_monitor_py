"""Resident main worker tests。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.comments_pipeline import CommentsScanSummary
from facebook_monitor.worker.resident_main import run_resident_main_cycle
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.resident_main_page_prepare import prepare_resident_main_page
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_shared import ResidentTarget
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions


from tests.worker.resident_main_test_helpers import FakeAsyncPage
from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext


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

    scanned_target_ids: list[str] = []
    started_target_ids: set[str] = set()
    both_scans_started = asyncio.Event()

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        """記錄同時執行數，證明 executor 不是序列化掃描。"""

        target_id = kwargs["target"].id
        scanned_target_ids.append(target_id)
        started_target_ids.add(target_id)
        if len(started_target_ids) == 2:
            both_scans_started.set()
        await asyncio.wait_for(both_scans_started.wait(), timeout=1)
        return PostsScanSummary(
            target_id=target_id,
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
    assert both_scans_started.is_set()
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
