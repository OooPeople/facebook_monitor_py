"""Sync resident fallback worker tests。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from typing import cast

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.comments_pipeline import scan_comments_target_page_sync_and_finalize
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.posts_pipeline import scan_posts_page_sync_and_finalize
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.facebook_automation_runtime import FacebookAutomationTripSignal
from facebook_monitor.worker.facebook_automation_runtime import (
    FacebookAutomationRuntimeTripped,
)
from facebook_monitor.worker.facebook_automation_runtime import (
    FacebookTemporaryBlockIncidentRecorded,
)
from facebook_monitor.worker.facebook_fallback_work import FacebookFallbackWork
from facebook_monitor.worker.scan_orchestration import FacebookPageGuardDiagnostics
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.resident_shared import should_reload_resident_page
from tests.worker.scan_finalize_test_helpers import record_protective_skip_for_test
from facebook_monitor.worker.sync_resident_fallback import SyncResidentPagePool
from facebook_monitor.worker.sync_resident_fallback import prepare_sync_resident_page
from facebook_monitor.worker.sync_resident_fallback import run_sync_resident_fallback_cycle
from facebook_monitor.worker.sync_resident_fallback import run_sync_resident_fallback_loop
from facebook_monitor.worker.sync_resident_fallback import select_sync_finalizing_scan_page
from facebook_monitor.worker import sync_resident_fallback as sync_resident_fallback_module


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


class FakeFallbackWork:
    """讓 cycle 單元測試保留正式的 application-context 形狀。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.context_closed = False
        self.signal = FacebookAutomationTripSignal()

    def ensure_io_allowed(self) -> None:
        """Fake runtime 未 trip，允許執行 browser action。"""

    def application_context(self) -> SqliteApplicationContext:
        """回傳與正式 work 相同邊界的 SQLite application context。"""

        return SqliteApplicationContext(self.db_path)

    def record_temporary_block_incident(self, **_kwargs: Any) -> bool:
        """cycle 單元測試不在此重複驗證 profile incident。"""

        return False

    def note_browser_context_closed(self) -> None:
        """記錄 fake context 已正常退出。"""

        self.context_closed = True


def _fallback_work(db_path: Path) -> FacebookFallbackWork:
    """將結構相容的 fake work 限縮在測試 helper 內。"""

    return cast(FacebookFallbackWork, FakeFallbackWork(db_path))


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


def test_sync_finalizing_selector_supports_posts_and_comments() -> None:
    """sync fallback 應依 target kind 選擇原本的 posts/comments scanner。"""

    posts_target = TargetDescriptor.for_group_posts(
        group_id="111",
        canonical_url="https://www.facebook.com/groups/111",
    )
    comments_target = TargetDescriptor.for_comments(
        group_id="111",
        parent_post_id="222",
        canonical_url="https://www.facebook.com/groups/111/posts/222",
    )

    assert select_sync_finalizing_scan_page(posts_target) is scan_posts_page_sync_and_finalize
    assert (
        select_sync_finalizing_scan_page(comments_target)
        is scan_comments_target_page_sync_and_finalize
    )


def test_comments_only_sync_resident_loop_opens_direct_url_and_scans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """comments-only sync fallback 應開啟 canonical URL 並執行 comments scanner。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    context = FakeBrowserContext()
    context_factory_calls = 0
    scan_calls: list[str] = []
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
                ),
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    def context_factory(
        options: ResidentRuntimeOptions,
    ) -> AbstractContextManager[FakeBrowserContext]:
        nonlocal context_factory_calls
        context_factory_calls += 1
        return FakeContextManager(context)

    def fake_comments_scan(**kwargs: Any) -> PostsScanSummary:
        """記錄 comments scanner 收到已導航 page。"""

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

    @contextmanager
    def fake_work_factory(**_kwargs: Any) -> Iterator[FacebookFallbackWork]:
        """提供 loop 測試所需的 transitional governed owner。"""

        yield _fallback_work(db_path)

    monkeypatch.setattr(
        sync_resident_fallback_module,
        "scan_comments_target_page_sync_and_finalize",
        fake_comments_scan,
    )

    summaries = run_sync_resident_fallback_loop(
        ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=profile_dir,
            interval_seconds=0,
            max_cycles=1,
        ),
        context_factory=context_factory,
        automation_work_factory=fake_work_factory,
    )

    assert context_factory_calls == 1
    assert scan_calls == [target.id]
    assert len(summaries) == 1
    assert summaries[0].success_count == 1
    assert summaries[0].opened_page_count == 1
    assert len(context.pages) == 1
    assert context.pages[0].goto_count == 1


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
        app.services.targets.restart_target_monitoring(target.id)

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
        fallback_work=_fallback_work(db_path),
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
        fallback_work=_fallback_work(db_path),
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


def test_sync_resident_loop_keeps_one_context_and_reuses_page_across_cycles(
    tmp_path: Path,
) -> None:
    """正式 sync loop 不應為每個 cycle 重建 context，target page 應直接重用。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    context = FakeBrowserContext()
    work = FakeFallbackWork(db_path)
    context_factory_calls = 0

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="loop-reuse",
                canonical_url="https://www.facebook.com/groups/loop-reuse",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    def context_factory(
        _options: ResidentRuntimeOptions,
    ) -> AbstractContextManager[FakeBrowserContext]:
        nonlocal context_factory_calls
        context_factory_calls += 1
        return FakeContextManager(context)

    @contextmanager
    def fake_work_factory(**_kwargs: Any) -> Iterator[FacebookFallbackWork]:
        """提供 loop reuse 測試所需的 transitional governed owner。"""

        yield cast(FacebookFallbackWork, work)

    def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        """回傳最小 summary，讓 interval=0 的 target 下一輪仍可再次 due。"""

        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    def request_second_cycle(summary: Any) -> None:
        """第一輪完成後建立 manual scan request，確保第二輪立即 due。"""

        if getattr(summary, "cycle_index", 0) != 1:
            return
        with SqliteApplicationContext(db_path) as app:
            app.services.targets.request_target_scan(target.id)

    summaries = run_sync_resident_fallback_loop(
        ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=profile_dir,
            interval_seconds=0,
            scheduler_tick_seconds=0,
            max_cycles=2,
        ),
        context_factory=context_factory,
        scan_page=fake_scan_page,
        on_cycle=request_second_cycle,
        automation_work_factory=fake_work_factory,
    )

    assert context_factory_calls == 1
    assert len(summaries) == 2
    assert summaries[0].opened_page_count == 1
    assert summaries[1].reused_page_count == 1
    assert len(context.pages) == 1
    assert context.pages[0].goto_count == 1
    assert context.pages[0].reload_count == 1
    assert context.pages[0].closed


def test_sync_fallback_owner_guard_rejects_finalize_after_runtime_owner_changes(
    tmp_path: Path,
) -> None:
    """scan 中途被新 runtime owner 取代時，舊 fallback 不得 finalize 新狀態。"""

    db_path = tmp_path / "app.db"
    context = FakeBrowserContext()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="owner-guard",
                canonical_url="https://www.facebook.com/groups/owner-guard",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    def replace_runtime_owner(**kwargs: Any) -> PostsScanSummary:
        """模擬掃描中由使用者重啟 target，讓舊 commit guard 失效。"""

        app = kwargs["app"]
        app.services.targets.pause_target_monitoring(target.id)
        app.services.targets.restart_target_monitoring(target.id)
        return PostsScanSummary(
            target_id=target.id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    summary = run_sync_resident_fallback_cycle(
        options=ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            interval_seconds=0,
        ),
        page_pool=SyncResidentPagePool(context),
        scan_page=replace_runtime_owner,
        cycle_index=1,
        fallback_work=_fallback_work(db_path),
    )

    assert summary.success_count == 0
    assert summary.skipped_count == 1
    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE


def test_resident_main_fallback_retries_extractor_empty_until_third_failure(
    tmp_path: Path,
) -> None:
    """sync fallback 也要重啟 target page，第三次 extractor_empty 才停止 target。"""

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
        app.services.targets.restart_target_monitoring(target.id)

    def failing_scan_page(**kwargs: Any) -> PostsScanSummary:
        """模擬 extractor 沒抽到貼文。"""

        raise WorkerFailure("extractor_empty", "No post-like items were extracted.")

    for attempt in range(1, 4):
        with SqliteApplicationContext(db_path) as app:
            app.services.targets.request_target_scan(target.id)
        summary = run_sync_resident_fallback_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=page_pool,
            scan_page=failing_scan_page,
            cycle_index=attempt,
            fallback_work=_fallback_work(db_path),
        )

        assert summary.failure_count == 1
        assert summary.opened_page_count == 1
        assert summary.reused_page_count == 0
        assert len(page_pool.pages) == 0
        assert context.pages[-1].closed
        with SqliteApplicationContext(db_path) as app:
            runtime_state = app.repositories.runtime_states.get(target.id)
            latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        assert runtime_state is not None
        assert latest_scan is not None
        assert latest_scan.status == ScanStatus.FAILED
        assert latest_scan.metadata["worker"] == "sync_resident_fallback"
        assert latest_scan.metadata["reason"] == "extractor_empty"
        assert latest_scan.metadata["retry_streak"] == attempt
        assert latest_scan.metadata["retry_limit"] == 3
        if attempt < 3:
            assert runtime_state.runtime_status == TargetRuntimeStatus.IDLE
            assert runtime_state.last_error == ""
            assert latest_scan.metadata["runtime_action"] == "will_retry"
            assert latest_scan.metadata["retryable"] is True
        else:
            assert runtime_state.runtime_status == TargetRuntimeStatus.ERROR
            assert "已連續 3 次失敗" in runtime_state.last_error
            assert "已連續 3 次失敗" in latest_scan.error_message
            assert "會重啟" not in latest_scan.error_message
            assert latest_scan.metadata["runtime_action"] == "error"
            assert latest_scan.metadata["retryable"] is False


def test_sync_resident_fallback_records_temporary_block_incident(
    tmp_path: Path,
) -> None:
    """sync fallback 的 WorkerFailure diagnostics 應一路寫入 failed scan。"""

    db_path = tmp_path / "app.db"
    context = FakeBrowserContext()
    page_pool = SyncResidentPagePool(context)
    diagnostics = FacebookPageGuardDiagnostics(
        classification="facebook_temporary_block",
        facebook_host=True,
        matched_heading=True,
        matched_detail=True,
        article_count=0,
        stable_observation_count=2,
        body_text_length=48,
        url_kind="group_feed",
    )
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="guard",
                canonical_url="https://www.facebook.com/groups/guard",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    def failing_scan_page(**kwargs: Any) -> PostsScanSummary:
        raise WorkerFailure(
            "facebook_temporary_block",
            "Facebook temporary access block detected.",
            diagnostics=diagnostics,
        )

    signal = FacebookAutomationTripSignal()
    with pytest.raises(FacebookTemporaryBlockIncidentRecorded):
        run_sync_resident_fallback_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=page_pool,
            scan_page=failing_scan_page,
            cycle_index=1,
            fallback_work=FacebookFallbackWork(db_path=db_path, signal=signal),
        )
    with SqliteApplicationContext(db_path) as app:
        scan = app.repositories.scan_runs.latest_by_target(target.id)
        warning = app.services.facebook_temporary_block_warning.get()
        loaded = app.repositories.targets.get(target.id)

    assert signal.is_tripped()
    assert scan is not None
    assert scan.metadata["worker"] == "facebook_access_incident"
    assert scan.metadata["failure_diagnostics"] == diagnostics.to_safe_mapping()
    assert warning is not None
    assert warning.operation_kind.value == "posts_access"
    assert warning.action_kind.value == "group_feed_document"
    assert loaded is not None and loaded.paused


def test_sync_resident_fallback_pretripped_signal_opens_no_page(
    tmp_path: Path,
) -> None:
    """Process-local trip 後不得再建立或準備 Facebook page。"""

    db_path = tmp_path / "app.db"
    context = FakeBrowserContext()
    page_pool = SyncResidentPagePool(context)
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="pretripped",
                canonical_url="https://www.facebook.com/groups/pretripped",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    signal = FacebookAutomationTripSignal()
    assert signal.try_trip()
    with pytest.raises(FacebookAutomationRuntimeTripped):
        run_sync_resident_fallback_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=page_pool,
            scan_page=scan_posts_page_sync_and_finalize,
            cycle_index=1,
            fallback_work=FacebookFallbackWork(db_path=db_path, signal=signal),
        )

    assert context.pages == []


def test_sync_comments_block_records_comments_direct_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Comments fallback direct navigation 必須保存 comments operation/action。"""

    db_path = tmp_path / "app.db"
    context = FakeBrowserContext()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/"
                    "posts/2187454285426518"
                ),
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    def blocked_comments_scan(**_kwargs: Any) -> PostsScanSummary:
        raise WorkerFailure("facebook_temporary_block", "blocked")

    monkeypatch.setattr(
        sync_resident_fallback_module,
        "scan_comments_target_page_sync_and_finalize",
        blocked_comments_scan,
    )

    signal = FacebookAutomationTripSignal()
    with pytest.raises(FacebookTemporaryBlockIncidentRecorded):
        run_sync_resident_fallback_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=SyncResidentPagePool(context),
            scan_page=blocked_comments_scan,
            cycle_index=1,
            fallback_work=FacebookFallbackWork(db_path=db_path, signal=signal),
        )

    with SqliteApplicationContext(db_path) as app:
        warning = app.services.facebook_temporary_block_warning.get()
    assert warning is not None
    assert warning.operation_kind.value == "comments_access"
    assert warning.action_kind.value == "direct_document"


def test_sync_resident_fallback_escalates_sort_skip_after_three_skips(
    tmp_path: Path,
) -> None:
    """sync fallback 的 sort skip 第三次才折算 failure 並丟棄 target page。"""

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
        app.services.targets.restart_target_monitoring(target.id)

    def skipping_scan_page(**kwargs: Any) -> PostsScanSummary:
        """模擬排序未確認時的 shared skipped finalize。"""

        result = record_protective_skip_for_test(
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

    for attempt in range(1, 4):
        with SqliteApplicationContext(db_path) as app:
            app.services.targets.request_target_scan(target.id)
        summary = run_sync_resident_fallback_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=page_pool,
            scan_page=skipping_scan_page,
            cycle_index=attempt,
            fallback_work=_fallback_work(db_path),
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
            assert len(page_pool.pages) == 0
            assert context.pages[-1].closed


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
        app.services.targets.restart_target_monitoring(target.id)

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
        fallback_work=_fallback_work(db_path),
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
        fallback_work=_fallback_work(db_path),
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
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

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
