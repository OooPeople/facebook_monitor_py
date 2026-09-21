"""正式 scheduler runtime recovery tests。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.scheduler.runtime_recovery import recover_stale_running_targets
from facebook_monitor.scheduler.runtime_recovery import recover_stale_runtime_targets


def _list_due_posts(
    db_path: Path,
    *,
    now: datetime,
) -> tuple[str, ...]:
    """以正式 planner 列出 runtime recovery 後到期的 posts targets。"""

    planner = TargetSchedulePlanner(scannable_target_kinds=frozenset({TargetKind.POSTS}))
    return tuple(
        due_target.target_id
        for due_target in planner.list_due_targets(
            db_path,
            default_interval_seconds=60,
            now=now,
        )
    )


def test_recover_stale_running_targets_requeues_stale_target(tmp_path: Path) -> None:
    """Scheduler 入口可重啟上次 process 中斷留下的 running 狀態。"""

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

    recovered_count = recover_stale_running_targets(
        db_path,
        stale_after_seconds=180,
    )

    with SqliteApplicationContext(db_path) as app:
        loaded = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert recovered_count == 1
    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.IDLE
    assert loaded.scan_requested_at is not None
    assert loaded.last_error == ""
    assert loaded.last_skip_reason == "target_page_restart: retry 1/3"
    assert latest_scan is not None
    assert latest_scan.metadata["auto_restart"] is True
    assert latest_scan.metadata["recovery_action"] == "target_page_restart"
    assert _list_due_posts(db_path, now=now) == (target.id,)


def test_recover_stale_running_inactive_target_does_not_record_scan_failure(
    tmp_path: Path,
) -> None:
    """Inactive target 的 stale running cleanup 不產生 scan failure/run。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="stopped-stale",
                canonical_url="https://www.facebook.com/groups/stopped-stale",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        state = app.services.targets.mark_target_running(target.id, "dead-worker")
        app.repositories.runtime_states.save(
            replace(
                state,
                desired_state=TargetDesiredState.STOPPED,
                last_heartbeat_at=now - timedelta(seconds=240),
                updated_at=now - timedelta(seconds=240),
            )
        )

    recovered_count = recover_stale_running_targets(
        db_path,
        stale_after_seconds=180,
    )

    with SqliteApplicationContext(db_path) as app:
        loaded = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert recovered_count == 1
    assert loaded is not None
    assert loaded.desired_state == TargetDesiredState.STOPPED
    assert loaded.runtime_status == TargetRuntimeStatus.IDLE
    assert loaded.scan_requested_at is None
    assert loaded.active_worker_id == ""
    assert latest_scan is None
    assert _list_due_posts(db_path, now=now) == ()


def test_recover_stale_runtime_targets_requeues_stale_queued_target(
    tmp_path: Path,
) -> None:
    """Scheduler recovery 會讓卡在 queued 的手動掃描重新變成可排程。"""

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
        queued_state = app.services.targets.mark_target_queued(
            target.id,
            "manual_request",
        )
        app.repositories.runtime_states.save(
            replace(
                queued_state,
                last_enqueued_at=now - timedelta(seconds=240),
                updated_at=now - timedelta(seconds=240),
            )
        )

    assert _list_due_posts(db_path, now=now) == ()

    recovered_count = recover_stale_runtime_targets(
        db_path,
        stale_after_seconds=180,
    )

    assert recovered_count == 1
    assert _list_due_posts(db_path, now=now) == (target.id,)
