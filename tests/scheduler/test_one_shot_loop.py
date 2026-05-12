"""One-shot fallback scheduler tests。"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import TargetConfigPatch
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.application.services import RecordScanRequest
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.scheduler.one_shot_loop import SchedulerOptions
from facebook_monitor.scheduler.one_shot_loop import list_schedulable_target_ids
from facebook_monitor.scheduler.one_shot_loop import run_one_shot_scheduler_loop
from facebook_monitor.scheduler.runtime_recovery import recover_stale_running_targets
from facebook_monitor.scheduler.runtime_recovery import recover_stale_runtime_targets
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.one_shot_dispatch import OneShotScanOptions


def test_list_schedulable_target_ids_respects_target_stop(tmp_path: Path) -> None:
    """scheduler 只選取啟用且 desired active 的 posts target。"""

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
        app.services.targets.pause_target_monitoring(second.id)

    assert list_schedulable_target_ids(db_path) == (first.id,)


def test_list_schedulable_target_ids_skips_currently_running_target(tmp_path: Path) -> None:
    """scheduler 不會選取仍在 running 的 target，避免重複掃描同一 profile。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        running_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        idle_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app.services.targets.restart_target_monitoring(running_target.id)
        app.services.targets.restart_target_monitoring(idle_target.id)
        app.services.targets.mark_target_running(running_target.id, "worker-1")

    assert list_schedulable_target_ids(db_path) == (idle_target.id,)


def test_list_schedulable_target_ids_respects_per_target_interval(tmp_path: Path) -> None:
    """scheduler 會依 target fixed_refresh_sec 判斷是否到期。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                config=TargetConfigPatch(fixed_refresh_sec=300),
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
            )
        )
        app.services.targets.clear_target_scan_request(target.id)

    assert (
        list_schedulable_target_ids(
            db_path,
            default_interval_seconds=60,
            now=now + timedelta(seconds=120),
        )
        == ()
    )
    assert list_schedulable_target_ids(
        db_path,
        default_interval_seconds=60,
        now=now + timedelta(seconds=360),
    ) == (target.id,)


def test_list_schedulable_target_ids_honors_manual_scan_request(tmp_path: Path) -> None:
    """manual-start 要求會讓 target 不等固定間隔立即到期。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                config=TargetConfigPatch(fixed_refresh_sec=300),
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
            )
        )
        app.services.targets.clear_target_scan_request(target.id)
        app.services.targets.request_target_scan(target.id)

    assert list_schedulable_target_ids(
        db_path,
        default_interval_seconds=60,
        now=now + timedelta(seconds=10),
    ) == (target.id,)


def test_list_schedulable_target_ids_uses_jitter_range_when_fixed_is_empty(
    tmp_path: Path,
) -> None:
    """沒有固定秒數時，scheduler 會使用 target jitter 範圍判斷到期。"""

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
        app.repositories.configs.save_for_target(
            target,
            TargetConfig(
                target_id=target.id,
                fixed_refresh_sec=None,
                min_refresh_sec=25,
                max_refresh_sec=35,
                jitter_enabled=True,
            )
        )
        app.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
            )
        )
        app.services.targets.clear_target_scan_request(target.id)

    assert (
        list_schedulable_target_ids(
            db_path,
            default_interval_seconds=60,
            now=now + timedelta(seconds=10),
        )
        == ()
    )
    assert list_schedulable_target_ids(
        db_path,
        default_interval_seconds=60,
        now=now + timedelta(seconds=40),
    ) == (target.id,)


def test_recover_stale_running_targets_marks_stale_target_error(tmp_path: Path) -> None:
    """scheduler 入口可修復上次 process 中斷留下的 running 狀態。"""

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
        state = app.services.targets.mark_target_running(target.id, "dead-worker")
        app.repositories.runtime_states.save(
            replace(
                state,
                last_heartbeat_at=now - timedelta(seconds=240),
                updated_at=now - timedelta(seconds=240),
            )
        )

    recovered_count = recover_stale_running_targets(db_path, stale_after_seconds=180)

    with SqliteApplicationContext(db_path) as app:
        loaded = app.repositories.runtime_states.get(target.id)
    assert recovered_count == 1
    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.ERROR
    assert "stale_running" in loaded.last_error


def test_recover_stale_runtime_targets_requeues_stale_queued_target(tmp_path: Path) -> None:
    """scheduler recovery 會讓卡在 queued 的手動掃描重新變成可排程。"""

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
        queued_state = app.services.targets.mark_target_queued(target.id, "manual_request")
        app.repositories.runtime_states.save(
            replace(
                queued_state,
                last_enqueued_at=now - timedelta(seconds=240),
                updated_at=now - timedelta(seconds=240),
            )
        )

    assert (
        list_schedulable_target_ids(
            db_path,
            default_interval_seconds=60,
            now=now,
        )
        == ()
    )
    recovered_count = recover_stale_runtime_targets(db_path, stale_after_seconds=180)

    assert recovered_count == 1
    assert list_schedulable_target_ids(
        db_path,
        default_interval_seconds=60,
        now=now,
    ) == (target.id,)


def test_scheduler_loop_scans_targets_sequentially_and_updates_runtime_state(
    tmp_path: Path,
) -> None:
    """scheduler 會以 bounded selection 掃描可執行 targets 並標回 idle。"""

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

    def fake_scan_once(options: OneShotScanOptions) -> PostsScanSummary:
        """記錄 scheduler 呼叫順序並回傳假掃描摘要。"""

        assert options.scan_timeout_seconds == 45
        scanned_target_ids.append(options.target_id)
        return PostsScanSummary(
            target_id=options.target_id,
            url="https://www.facebook.com/groups/example",
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    summaries = run_one_shot_scheduler_loop(
        SchedulerOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scan_timeout_seconds=45,
            max_cycles=1,
        ),
        scan_once=fake_scan_once,
        sleep_fn=lambda _seconds: None,
    )

    assert scanned_target_ids == [first.id, second.id]
    assert summaries[0].selected_count == 2
    assert summaries[0].success_count == 2
    with SqliteApplicationContext(db_path) as app:
        first_state = app.repositories.runtime_states.get(first.id)
        second_state = app.repositories.runtime_states.get(second.id)
    assert first_state is not None
    assert second_state is not None
    assert first_state.runtime_status == TargetRuntimeStatus.IDLE
    assert second_state.runtime_status == TargetRuntimeStatus.IDLE


def test_scheduler_loop_uses_bounded_selection_without_losing_due_targets(
    tmp_path: Path,
) -> None:
    """max_concurrent_scans 會限制單 tick 取用數，未取用 target 保持到期。"""

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
        third = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="333",
                canonical_url="https://www.facebook.com/groups/333",
            )
        )
        app.services.targets.restart_target_monitoring(first.id)
        app.services.targets.restart_target_monitoring(second.id)
        app.services.targets.restart_target_monitoring(third.id)

    scanned_target_ids: list[str] = []

    def fake_scan_once(options: OneShotScanOptions) -> PostsScanSummary:
        """記錄 bounded scheduler 每次提交的 target。"""

        scanned_target_ids.append(options.target_id)
        return PostsScanSummary(
            target_id=options.target_id,
            url="https://www.facebook.com/groups/example",
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    summaries = run_one_shot_scheduler_loop(
        SchedulerOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            max_concurrent_scans=1,
            scheduler_tick_seconds=0,
            max_cycles=2,
        ),
        scan_once=fake_scan_once,
        sleep_fn=lambda _seconds: None,
    )

    assert scanned_target_ids == [first.id, second.id]
    assert [summary.selected_count for summary in summaries] == [1, 1]


def test_target_scan_guard_records_skip_reason(tmp_path: Path) -> None:
    """同一 target 已 running 時，scan guard 會拒絕重入並保存原因。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.mark_target_running(target.id, "worker-a")
        locked_state = app.services.targets.try_mark_target_running(target.id, "worker-b")
        state = app.repositories.runtime_states.get(target.id)

    assert locked_state is None
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert "scan_guard_skipped" in state.last_skip_reason
    assert "worker-a" in state.last_skip_reason


def test_scheduler_loop_marks_extractor_empty_as_idle_after_failed_scan(
    tmp_path: Path,
) -> None:
    """extractor_empty 會記錄失敗但 target 回到 idle，讓下一輪可再嘗試。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    def fake_scan_once(_options: OneShotScanOptions) -> PostsScanSummary:
        """模擬 extractor 空結果。"""

        raise WorkerFailure("extractor_empty", "No post-like items were extracted.")

    summaries = run_one_shot_scheduler_loop(
        SchedulerOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            max_cycles=1,
        ),
        scan_once=fake_scan_once,
        sleep_fn=lambda _seconds: None,
    )

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
    assert summaries[0].failure_count == 1
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.last_error == ""
