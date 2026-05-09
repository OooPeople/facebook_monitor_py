"""Resident worker tests。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.resident_shared import should_reload_resident_page
from facebook_monitor.worker.sync_resident_fallback import SyncResidentPagePool
from facebook_monitor.worker.sync_resident_fallback import prepare_sync_resident_page
from facebook_monitor.worker.sync_resident_fallback import run_sync_resident_fallback_cycle
from facebook_monitor.worker.sync_resident_fallback import run_sync_resident_fallback_loop


class FakeResidentPage:
    """測試用 page，記錄 goto/reload/close 狀態。"""

    def __init__(self) -> None:
        self.url = "about:blank"
        self.goto_count = 0
        self.reload_count = 0
        self.closed = False

    def goto(self, url: str, wait_until: str, timeout: float) -> None:
        """模擬導航到 target URL。"""

        self.url = url.rstrip("/")
        self.goto_count += 1

    def reload(self, wait_until: str, timeout: float) -> None:
        """模擬重新整理目前 target page。"""

        self.reload_count += 1

    def wait_for_timeout(self, milliseconds: int) -> None:
        """模擬 Playwright 等待。"""

    def is_closed(self) -> bool:
        """回傳 page 是否已關閉。"""

        return self.closed

    def close(self) -> None:
        """標記 page 已關閉。"""

        self.closed = True


class FakeBrowserContext:
    """測試用 browser context，避免真的啟動 Playwright。"""

    def __init__(self) -> None:
        self.pages: list[FakeResidentPage] = []

    def new_page(self) -> FakeResidentPage:
        """建立一個 fake page。"""

        page = FakeResidentPage()
        self.pages.append(page)
        return page


class FakeContextManager(AbstractContextManager[FakeBrowserContext]):
    """測試用 context manager。"""

    def __init__(self, context: FakeBrowserContext) -> None:
        self.context = context

    def __enter__(self) -> FakeBrowserContext:
        """回傳 fake browser context。"""

        return self.context

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """結束 fake context，不需額外清理。"""


def test_resident_page_reload_keeps_same_group_feed_sorting_url() -> None:
    """同一 group feed 即使帶 sorting query 也應 reload，不應 goto canonical URL。"""

    page = FakeResidentPage()
    page.url = "https://www.facebook.com/groups/111/?sorting_setting=CHRONOLOGICAL"
    target = TargetDescriptor.for_group_posts(
        group_id="111",
        canonical_url="https://www.facebook.com/groups/111",
    )

    prepare_sync_resident_page(page=page, target=target, timeout_ms=1000)

    assert page.reload_count == 1
    assert page.goto_count == 0


def test_resident_page_does_not_reload_post_permalink() -> None:
    """單篇貼文 permalink 不是 group feed，resident page 應回到 canonical feed URL。"""

    assert not should_reload_resident_page(
        "https://www.facebook.com/groups/111/posts/222",
        "https://www.facebook.com/groups/111",
    )


def test_resident_page_reload_keeps_same_comment_post_url() -> None:
    """comments target 同一 parent post 應 reload，避免重打 canonical URL。"""

    assert should_reload_resident_page(
        "https://www.facebook.com/groups/11111111/posts/22222222?comment_id=33333333",
        "https://www.facebook.com/groups/11111111/posts/22222222",
    )
    assert not should_reload_resident_page(
        "https://www.facebook.com/groups/11111111/posts/33333333",
        "https://www.facebook.com/groups/11111111/posts/22222222",
    )


def test_resident_fallback_reuses_target_page_between_cycles(tmp_path: Path) -> None:
    """resident main worker 會在下一次 target 到期時重用既有 target page。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    context = FakeBrowserContext()
    scan_calls: list[str] = []

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )

    def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        """記錄掃描呼叫但不寫入 scan run，讓第二輪仍維持 due。"""

        scan_calls.append(kwargs["target"].id)
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    page_pool = SyncResidentPagePool(context)
    planner = TargetSchedulePlanner()
    first_summary = run_sync_resident_fallback_cycle(
        options=ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=profile_dir,
            interval_seconds=0,
        ),
        page_pool=page_pool,
        scan_page=fake_scan_page,
        schedule_planner=planner,
        cycle_index=1,
    )
    with SqliteApplicationContext(db_path) as app:
        app.services.targets.request_target_scan(target.id)
    second_summary = run_sync_resident_fallback_cycle(
        options=ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=profile_dir,
            interval_seconds=0,
        ),
        page_pool=page_pool,
        scan_page=fake_scan_page,
        schedule_planner=planner,
        cycle_index=2,
    )

    assert scan_calls == [target.id, target.id]
    assert len(context.pages) == 1
    assert context.pages[0].goto_count == 1
    assert context.pages[0].reload_count == 1
    assert first_summary.opened_page_count == 1
    assert second_summary.reused_page_count == 1
    with SqliteApplicationContext(db_path) as app:
        runtime_state = app.repositories.runtime_states.get(target.id)
    assert runtime_state is not None
    assert runtime_state.runtime_status == TargetRuntimeStatus.IDLE


def test_resident_main_fallback_records_extractor_empty_but_returns_target_to_idle(
    tmp_path: Path,
) -> None:
    """extractor_empty 會記錄 failed scan run，但 target 回到 idle 供下輪重試。"""

    db_path = tmp_path / "app.db"
    context = FakeBrowserContext()

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )

    def failing_scan_page(**kwargs: Any) -> PostsScanSummary:
        """模擬 extractor 沒抽到貼文。"""

        raise WorkerFailure("extractor_empty", "No post-like items were extracted.")

    summary = run_sync_resident_fallback_cycle(
        options=ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            interval_seconds=0,
        ),
        page_pool=SyncResidentPagePool(context),
        scan_page=failing_scan_page,
        cycle_index=1,
    )

    assert summary.failure_count == 1
    with SqliteApplicationContext(db_path) as app:
        runtime_state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
    assert runtime_state is not None
    assert runtime_state.runtime_status == TargetRuntimeStatus.IDLE
    assert runtime_state.last_error == ""
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.FAILED
    assert latest_scan.metadata["worker"] == "resident_main"


def test_resident_fallback_closes_page_after_target_stop(tmp_path: Path) -> None:
    """target 停止後 resident main worker 會關閉該 target 的常駐 page。"""

    db_path = tmp_path / "app.db"
    context = FakeBrowserContext()
    page_pool = SyncResidentPagePool(context)

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )

    def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        """回傳假掃描摘要。"""

        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    first_summary = run_sync_resident_fallback_cycle(
        options=ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            interval_seconds=0,
        ),
        page_pool=page_pool,
        scan_page=fake_scan_page,
        cycle_index=1,
    )
    with SqliteApplicationContext(db_path) as app:
        app.services.targets.pause_target_monitoring(target.id)
    second_summary = run_sync_resident_fallback_cycle(
        options=ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            interval_seconds=0,
        ),
        page_pool=page_pool,
        scan_page=fake_scan_page,
        cycle_index=2,
    )

    assert first_summary.opened_page_count == 1
    assert second_summary.selected_count == 0
    assert second_summary.closed_page_count == 1
    assert context.pages[0].closed


def test_resident_fallback_reports_profile_locked_before_playwright(tmp_path: Path) -> None:
    """resident main worker 遇到 profile lease 衝突時，不會再啟動 Playwright。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    with SqliteApplicationContext(db_path) as app:
        app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )

    with acquire_profile_lease(profile_dir, "test holder"):
        try:
            run_sync_resident_fallback_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    max_cycles=1,
                ),
            )
        except WorkerFailure as exc:
            assert exc.reason == "profile_locked"
        else:
            raise AssertionError("resident main worker should report profile_locked")
