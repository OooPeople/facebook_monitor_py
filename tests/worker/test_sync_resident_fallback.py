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
from facebook_monitor.core.facebook_access import FacebookAccessBlockSignal
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookWorkSourceKind
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.runtime.paths import FACEBOOK_AUTOMATION_SESSION_GUARDS_DIR_NAME
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.posts_pipeline import scan_posts_page_sync_and_finalize
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.fallback_automation_admission import GovernedFallbackPostsWork
from facebook_monitor.worker.fallback_automation_admission import governed_fallback_posts_work
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardStore,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    derive_facebook_automation_profile_alias,
)
from facebook_monitor.worker.scan_orchestration import FacebookPageGuardDiagnostics
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.resident_shared import should_reload_resident_page
from tests.worker.scan_finalize_test_helpers import record_protective_skip_for_test
from facebook_monitor.worker.sync_resident_fallback import SyncResidentPagePool
from facebook_monitor.worker.sync_resident_fallback import prepare_sync_resident_page
from facebook_monitor.worker.sync_resident_fallback import run_sync_resident_fallback_cycle
from facebook_monitor.worker.sync_resident_fallback import run_sync_resident_fallback_loop
from facebook_monitor.worker.sync_resident_fallback import select_sync_finalizing_scan_page


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


class FakeGovernedWork:
    """讓 cycle 單元測試保留正式的 application-context owner guard 形狀。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.context_closed = False

    def application_context(self) -> SqliteApplicationContext:
        """回傳與正式 work 相同邊界的 SQLite application context。"""

        return SqliteApplicationContext(self.db_path)

    def record_temporary_block_incident(self, **_kwargs: Any) -> bool:
        """cycle 單元測試不在此重複驗證 profile incident。"""

        return False

    def note_browser_context_closed(self) -> None:
        """記錄 fake context 已正常退出。"""

        self.context_closed = True


def _governed_work(db_path: Path) -> GovernedFallbackPostsWork:
    """將結構相容的 fake work 限縮在測試 helper 內。"""

    return cast(GovernedFallbackPostsWork, FakeGovernedWork(db_path))


def _guard_store(
    data_dir: Path,
    work: GovernedFallbackPostsWork,
) -> FacebookAutomationSessionGuardStore:
    """取得 production governed work 所使用的 session sentinel store。"""

    return FacebookAutomationSessionGuardStore(
        data_dir / FACEBOOK_AUTOMATION_SESSION_GUARDS_DIR_NAME,
        profile_alias=derive_facebook_automation_profile_alias(work.profile_scope_key),
    )


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


def test_sync_finalizing_selector_rejects_comments_fallback() -> None:
    """sync fallback selector 對 comments 必須明確拒絕，不可回 direct scanner。"""

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
    with pytest.raises(WorkerFailure) as exc_info:
        select_sync_finalizing_scan_page(comments_target)
    assert exc_info.value.reason == "unsupported_in_fallback"


def test_sync_resident_comments_fails_before_page_navigation_or_scanner(
    tmp_path: Path,
) -> None:
    """comments sync attempt 在 page pool 前失敗，且保存 guarded diagnostics。"""

    db_path = tmp_path / "app.db"
    context = FakeBrowserContext()
    page_pool = SyncResidentPagePool(context)
    scan_calls = 0
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

    def forbidden_scan(**kwargs: Any) -> PostsScanSummary:
        nonlocal scan_calls
        scan_calls += 1
        raise AssertionError("comments sync fallback must not call scanner")

    summary = run_sync_resident_fallback_cycle(
        options=ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            interval_seconds=0,
        ),
        page_pool=page_pool,
        scan_page=forbidden_scan,
        cycle_index=1,
        governed_work=_governed_work(db_path),
    )
    with SqliteApplicationContext(db_path) as app:
        scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert summary.selected_count == 1
    assert summary.failure_count == 1
    assert summary.opened_page_count == 0
    assert summary.reused_page_count == 0
    assert context.pages == []
    assert scan_calls == 0
    assert scan is not None
    assert scan.metadata["reason"] == "unsupported_in_fallback"
    assert scan.metadata["failure_diagnostics"]["fallback_guard"] == {
        "detector": "fallback_capability_guard",
        "detector_version": 1,
        "classification": "unsupported_in_fallback",
        "fallback_mode": "sync_resident_fallback",
        "target_kind": "comments",
        "browser_work_started": False,
    }


def test_comments_only_sync_resident_loop_does_not_launch_browser(
    tmp_path: Path,
) -> None:
    """comments-only sync loop 不得取得 profile lease 或建立 browser context。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    context_factory_calls = 0
    scan_calls = 0
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

    def forbidden_context_factory(
        options: ResidentRuntimeOptions,
    ) -> AbstractContextManager[FakeBrowserContext]:
        nonlocal context_factory_calls
        context_factory_calls += 1
        raise AssertionError("comments-only sync fallback must not launch browser")

    def forbidden_scan(**kwargs: Any) -> PostsScanSummary:
        nonlocal scan_calls
        scan_calls += 1
        raise AssertionError("comments-only sync fallback must not call scanner")

    summaries = run_sync_resident_fallback_loop(
        ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=profile_dir,
            interval_seconds=0,
            max_cycles=1,
        ),
        context_factory=forbidden_context_factory,
        scan_page=forbidden_scan,
    )

    assert context_factory_calls == 0
    assert scan_calls == 0
    assert len(summaries) == 1
    assert summaries[0].failure_count == 1


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
        governed_work=_governed_work(db_path),
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
        governed_work=_governed_work(db_path),
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
        governed_work=_governed_work(db_path),
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
            governed_work=_governed_work(db_path),
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


def test_sync_resident_fallback_preserves_typed_failure_diagnostics(
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

    summary = run_sync_resident_fallback_cycle(
        options=ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            interval_seconds=0,
        ),
        page_pool=page_pool,
        scan_page=failing_scan_page,
        cycle_index=1,
        governed_work=_governed_work(db_path),
    )
    with SqliteApplicationContext(db_path) as app:
        scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert summary.failure_count == 1
    assert scan is not None
    assert scan.metadata["failure_diagnostics"] == diagnostics.to_safe_mapping()


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
            governed_work=_governed_work(db_path),
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
        governed_work=_governed_work(db_path),
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
        governed_work=_governed_work(db_path),
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


def test_production_governed_failure_finalize_uses_one_writer(
    tmp_path: Path,
) -> None:
    """正式 fenced transaction 內的失敗寫回不可再開第二個 SQLite writer。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    profile_dir.mkdir(parents=True)
    context = FakeBrowserContext()
    captured_work: list[GovernedFallbackPostsWork] = []
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="production-failure-finalize",
                canonical_url=(
                    "https://www.facebook.com/groups/production-failure-finalize"
                ),
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    @contextmanager
    def recording_work_factory(**kwargs: Any) -> Iterator[GovernedFallbackPostsWork]:
        with governed_fallback_posts_work(**kwargs) as work:
            captured_work.append(work)
            yield work

    def deterministic_failure(**_kwargs: Any) -> PostsScanSummary:
        raise WorkerFailure(
            SORT_ADJUST_UNCONFIRMED_REASON,
            "deterministic production-style failure",
        )

    summaries = run_sync_resident_fallback_loop(
        ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=profile_dir,
            interval_seconds=0,
            max_cycles=1,
        ),
        context_factory=lambda _options: FakeContextManager(context),
        scan_page=deterministic_failure,
        automation_work_factory=recording_work_factory,
    )

    assert summaries[0].failure_count == 1
    assert captured_work[0].browser_context_closed
    with SqliteApplicationContext(db_path) as app:
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        runtime_state = app.repositories.runtime_states.get(target.id)
    assert latest_scan is not None
    assert latest_scan.metadata["reason"] == SORT_ADJUST_UNCONFIRMED_REASON
    assert "database is locked" not in latest_scan.error_message.casefold()
    assert runtime_state is not None
    assert runtime_state.runtime_status == TargetRuntimeStatus.IDLE


def test_visible_write_rejection_still_acknowledges_successful_context_close(
    tmp_path: Path,
) -> None:
    """外部 durable trip 拒絕 visible write 時，成功的 context exit 仍可清 marker。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    profile_dir.mkdir(parents=True)
    captured_work: list[GovernedFallbackPostsWork] = []
    context_launches = 0
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="external-trip",
                canonical_url="https://www.facebook.com/groups/external-trip",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    @contextmanager
    def recording_work_factory(**kwargs: Any) -> Iterator[GovernedFallbackPostsWork]:
        with governed_fallback_posts_work(**kwargs) as work:
            captured_work.append(work)
            yield work

    class TripBeforeVisibleWritePage(FakeResidentPage):
        """在導航準備完成後由另一 DB owner 開啟 durable circuit。"""

        tripped = False

        def wait_for_timeout(self, milliseconds: int) -> None:
            if self.tripped:
                return
            self.tripped = True
            work = captured_work[-1]
            with SqliteApplicationContext(db_path) as app:
                result = app.services.facebook_access_circuit.trip(
                    FacebookAccessBlockSignal(
                        admission_token=work.lease.admission_token,
                        source_kind=FacebookWorkSourceKind.SCAN,
                        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
                        trigger_action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
                        source_owner_token=work.lease.process_lease.operation_id,
                        target_id=target.id,
                        evidence_code="external_trip_test_v1",
                    ),
                    source_owner_is_valid=True,
                )
            assert result.state.status.value == "open"

    class TripBeforeVisibleWriteContext(FakeBrowserContext):
        def new_page(self) -> FakeResidentPage:
            page = TripBeforeVisibleWritePage()
            self.pages.append(page)
            return page

    def context_factory(
        _options: ResidentRuntimeOptions,
    ) -> AbstractContextManager[FakeBrowserContext]:
        nonlocal context_launches
        context_launches += 1
        return FakeContextManager(TripBeforeVisibleWriteContext())

    first = run_sync_resident_fallback_loop(
        ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=profile_dir,
            interval_seconds=0,
            max_cycles=1,
        ),
        context_factory=context_factory,
        automation_work_factory=recording_work_factory,
    )
    with SqliteApplicationContext(db_path) as app:
        app.services.targets.pause_target_monitoring(target.id)
        app.services.targets.restart_target_monitoring(target.id)
    second = run_sync_resident_fallback_loop(
        ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=profile_dir,
            interval_seconds=0,
            stale_running_after_seconds=0,
            max_cycles=1,
        ),
        context_factory=context_factory,
        automation_work_factory=recording_work_factory,
    )

    assert first[0].skipped_count == 1
    assert second[0].skipped_count == 1
    assert context_launches == 1
    assert len(captured_work) == 1
    assert captured_work[0].browser_context_closed
    assert _guard_store(tmp_path, captured_work[0]).read() is None


def test_context_close_failure_does_not_acknowledge_browser_context(
    tmp_path: Path,
) -> None:
    """context exit 失敗時保留 normal marker，下一次啟動必須 fail closed。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    profile_dir.mkdir(parents=True)
    captured_work: list[GovernedFallbackPostsWork] = []
    restart_context_launches = 0
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="close-failure",
                canonical_url="https://www.facebook.com/groups/close-failure",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    @contextmanager
    def recording_work_factory(**kwargs: Any) -> Iterator[GovernedFallbackPostsWork]:
        with governed_fallback_posts_work(**kwargs) as work:
            captured_work.append(work)
            yield work

    class CloseFailureContextManager(FakeContextManager):
        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            raise RuntimeError("browser context close failed")

    with pytest.raises(RuntimeError, match="browser context close failed"):
        run_sync_resident_fallback_loop(
            ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=profile_dir,
                interval_seconds=0,
                max_cycles=1,
            ),
            context_factory=lambda _options: CloseFailureContextManager(
                FakeBrowserContext()
            ),
            automation_work_factory=recording_work_factory,
        )

    assert len(captured_work) == 1
    assert not captured_work[0].browser_context_closed
    assert _guard_store(tmp_path, captured_work[0]).read() is not None

    def forbidden_restart_context(
        _options: ResidentRuntimeOptions,
    ) -> AbstractContextManager[FakeBrowserContext]:
        nonlocal restart_context_launches
        restart_context_launches += 1
        raise AssertionError("stale normal marker must block browser relaunch")

    restarted = run_sync_resident_fallback_loop(
        ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=profile_dir,
            interval_seconds=0,
            max_cycles=1,
        ),
        context_factory=forbidden_restart_context,
        automation_work_factory=recording_work_factory,
    )

    assert restarted[0].skipped_count == 1
    assert restart_context_launches == 0
