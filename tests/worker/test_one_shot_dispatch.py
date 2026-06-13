"""One-shot dispatch fallback tests。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.scan_recording_service import RecordScanRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.one_shot_dispatch import OneShotScanOptions
from facebook_monitor.worker.one_shot_dispatch import record_failure
from facebook_monitor.worker.one_shot_dispatch import run_one_shot_scan
from facebook_monitor.worker.one_shot_dispatch import select_one_shot_target
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import record_skipped_scan


class FakePlaywrightManager:
    """提供 one-shot 測試用的 sync_playwright context manager。"""

    def __enter__(self) -> object:
        """回傳假 Playwright 物件。"""

        return object()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """不攔截例外。"""

        return None


class FakeOneShotPage:
    """提供 one-shot 測試所需的同步 page API。"""

    def __init__(self) -> None:
        self.url = ""

    def goto(self, url: str, *, wait_until: str, timeout: float) -> None:
        """記錄目前 URL。"""

        self.url = url

    def wait_for_timeout(self, timeout: int) -> None:
        """測試不需要實際等待。"""


class FakeOneShotContext:
    """提供 one-shot 測試所需的同步 browser context API。"""

    def __init__(self) -> None:
        self.page = FakeOneShotPage()
        self.closed = False

    def set_default_timeout(self, timeout: float) -> None:
        """接受 timeout 設定。"""

    def set_default_navigation_timeout(self, timeout: float) -> None:
        """接受 navigation timeout 設定。"""

    def new_page(self) -> FakeOneShotPage:
        """回傳假 page。"""

        return self.page

    def close(self) -> None:
        """記錄 context 已關閉。"""

        self.closed = True


def test_select_one_shot_target_by_group_id_when_multiple_targets_exist(tmp_path: Path) -> None:
    """fallback/debug one-shot dispatch 可用 group id 選取指定 posts target。"""

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

        selected = select_one_shot_target(app, target_id="", group_id="222")

        assert selected.id != first.id
        assert selected.id == second.id


def test_run_one_shot_scan_records_sort_skip_escalation_after_context_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """debug one-shot 第三次排序 skip 升級時，應先 rollback 再走 failure policy。"""

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
        target = app.services.targets.restart_target_monitoring(target.id)
        record_skipped_scan(
            app=app,
            target=target,
            metadata={"worker": "one_shot_posts_scan"},
            commit_guard=None,
        )
        record_skipped_scan(
            app=app,
            target=target,
            metadata={"worker": "one_shot_posts_scan"},
            commit_guard=None,
        )

    def fake_scan_posts_page(**kwargs: Any) -> PostsScanSummary:
        """模擬 one-shot 掃描中再次遇到排序未確認。"""

        result = record_skipped_scan(
            app=kwargs["app"],
            target=kwargs["target"],
            metadata={
                "worker": "one_shot_posts_scan",
                "skip_reason": SORT_ADJUST_UNCONFIRMED_REASON,
            },
            commit_guard=kwargs["commit_guard"],
        )
        target_id = kwargs["target"].id
        return PostsScanSummary(
            target_id=target_id,
            url=str(kwargs["page"].url),
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=result.scan_run_id,
            round_stats=(),
        )

    monkeypatch.setattr(
        "facebook_monitor.worker.one_shot_dispatch.sync_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.one_shot_dispatch.launch_persistent_context_sync",
        lambda _playwright, _options: FakeOneShotContext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.one_shot_dispatch.scan_posts_page",
        fake_scan_posts_page,
    )

    with pytest.raises(WorkerFailure) as excinfo:
        run_one_shot_scan(
            OneShotScanOptions(
                db_path=db_path,
                profile_dir=profile_dir,
                target_id=target.id,
            )
        )

    assert excinfo.value.reason == SORT_ADJUST_UNCONFIRMED_REASON
    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        scan_count = app.repositories.scan_runs.connection.execute(
            "SELECT COUNT(*) FROM scan_runs WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]

    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.FAILED
    assert latest_scan.metadata["reason"] == SORT_ADJUST_UNCONFIRMED_REASON
    assert latest_scan.metadata["retryable"] is True
    assert scan_count == 3
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.scan_requested_at is not None
    assert state.consecutive_failure_reason == SORT_ADJUST_UNCONFIRMED_REASON
    assert state.consecutive_failure_count == 1
    assert state.consecutive_scan_skip_count == 0


def test_run_one_shot_scan_success_clears_direct_runtime_streaks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """debug one-shot 真正成功時，也需清除先前錯誤與排序 skip streak。"""

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
        target = app.services.targets.restart_target_monitoring(target.id)
        failure = app.services.targets.decide_scan_failure(
            target.id,
            SORT_ADJUST_UNCONFIRMED_REASON,
            source="worker_failure",
        )
        app.services.targets.apply_scan_failure_decision(target.id, failure, "sort failed")
        skip = app.services.targets.decide_scan_skip(
            target.id,
            SORT_ADJUST_UNCONFIRMED_REASON,
            skip_limit=3,
        )
        app.services.targets.apply_scan_skip_decision(target.id, skip)

    def fake_scan_posts_page(**kwargs: Any) -> PostsScanSummary:
        """模擬真正成功的 posts scan finalize。"""

        target_id = kwargs["target"].id
        scan_run_id = kwargs["app"].services.scans.record_scan(
            RecordScanRequest(
                target_id=target_id,
                status=ScanStatus.SUCCESS,
                item_count=1,
                metadata={"worker": "one_shot_posts_scan"},
            )
        )
        return PostsScanSummary(
            target_id=target_id,
            url=str(kwargs["page"].url),
            item_count=1,
            new_count=1,
            matched_count=0,
            scan_run_id=scan_run_id,
            round_stats=(),
        )

    monkeypatch.setattr(
        "facebook_monitor.worker.one_shot_dispatch.sync_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.one_shot_dispatch.launch_persistent_context_sync",
        lambda _playwright, _options: FakeOneShotContext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.one_shot_dispatch.scan_posts_page",
        fake_scan_posts_page,
    )

    summary = run_one_shot_scan(
        OneShotScanOptions(
            db_path=db_path,
            profile_dir=profile_dir,
            target_id=target.id,
        )
    )

    assert summary.item_count == 1
    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.SUCCESS
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.consecutive_failure_reason == ""
    assert state.consecutive_failure_count == 0
    assert state.consecutive_scan_skip_reason == ""
    assert state.consecutive_scan_skip_count == 0


def test_record_failure_with_guard_does_not_fallback_after_owner_changed(
    tmp_path: Path,
) -> None:
    """有 owner guard 的 one-shot failure 不可污染 stale attempt diagnostics。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        target = app.services.targets.restart_target_monitoring(target.id)
        assert app.services.targets.try_claim_target_running(
            target.id,
            "current-worker",
            page_id="current-page",
        )
        current_state = app.repositories.runtime_states.get(target.id)
        assert current_state is not None
        assert current_state.last_started_at is not None

    record_failure(
        db_path,
        target,
        UNKNOWN_REASON,
        "stale failure",
        commit_guard=ScanCommitGuard(
            worker_id="stale-worker",
            page_id="stale-page",
            started_at=current_state.last_started_at,
        ),
    )

    with SqliteApplicationContext(db_path) as app:
        scan_count = app.repositories.scan_runs.connection.execute(
            "SELECT COUNT(*) FROM scan_runs WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]
        state = app.repositories.runtime_states.get(target.id)

    assert scan_count == 0
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == "current-worker"


def test_run_one_shot_scan_skipped_success_preserves_direct_failure_streak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """debug one-shot skipped success 不是恢復，不能清除 failure streak。"""

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
        target = app.services.targets.restart_target_monitoring(target.id)
        failure = app.services.targets.decide_scan_failure(
            target.id,
            SORT_ADJUST_UNCONFIRMED_REASON,
            source="worker_failure",
        )
        app.services.targets.apply_scan_failure_decision(target.id, failure, "sort failed")

    def fake_scan_posts_page(**kwargs: Any) -> PostsScanSummary:
        """模擬排序未確認但尚未達錯誤門檻的 skipped scan。"""

        result = record_skipped_scan(
            app=kwargs["app"],
            target=kwargs["target"],
            metadata={
                "worker": "one_shot_posts_scan",
                "skip_reason": SORT_ADJUST_UNCONFIRMED_REASON,
            },
            commit_guard=kwargs["commit_guard"],
        )
        target_id = kwargs["target"].id
        return PostsScanSummary(
            target_id=target_id,
            url=str(kwargs["page"].url),
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=result.scan_run_id,
            round_stats=(),
            scan_skipped=True,
        )

    monkeypatch.setattr(
        "facebook_monitor.worker.one_shot_dispatch.sync_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.one_shot_dispatch.launch_persistent_context_sync",
        lambda _playwright, _options: FakeOneShotContext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.one_shot_dispatch.scan_posts_page",
        fake_scan_posts_page,
    )

    summary = run_one_shot_scan(
        OneShotScanOptions(
            db_path=db_path,
            profile_dir=profile_dir,
            target_id=target.id,
        )
    )

    assert summary.scan_skipped
    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.SUCCESS
    assert latest_scan.metadata["scan_skipped"] is True
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.consecutive_failure_reason == SORT_ADJUST_UNCONFIRMED_REASON
    assert state.consecutive_failure_count == 1
    assert state.consecutive_scan_skip_reason == SORT_ADJUST_UNCONFIRMED_REASON
    assert state.consecutive_scan_skip_count == 1
