"""Application service tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON


def test_target_runtime_state_default_is_stopped() -> None:
    """直接建立 runtime state 時，預設應符合新 target 停止語義。"""

    state = TargetRuntimeState(target_id="target-1")

    assert state.desired_state == TargetDesiredState.STOPPED


def test_page_load_timeout_failure_streak_marks_error_on_third_failure(
    tmp_path: Path,
) -> None:
    """page_load_timeout 連續失敗由 runtime state 累計，第三次才停止 target。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

        first = app.services.targets.decide_scan_failure(
            target.id,
            "page_load_timeout",
            source="playwright",
        )
        app.services.targets.apply_scan_failure_decision(target.id, first, "timeout")
        first_state = app.repositories.runtime_states.get(target.id)

        second = app.services.targets.decide_scan_failure(
            target.id,
            "page_load_timeout",
            source="playwright",
        )
        app.services.targets.apply_scan_failure_decision(target.id, second, "timeout")
        second_state = app.repositories.runtime_states.get(target.id)

        third = app.services.targets.decide_scan_failure(
            target.id,
            "page_load_timeout",
            source="playwright",
        )
        app.services.targets.apply_scan_failure_decision(target.id, third, "timeout")
        third_state = app.repositories.runtime_states.get(target.id)

    assert first_state is not None
    assert first_state.runtime_status == TargetRuntimeStatus.IDLE
    assert first_state.last_error == ""
    assert first_state.consecutive_failure_reason == "page_load_timeout"
    assert first_state.consecutive_failure_count == 1
    assert second_state is not None
    assert second_state.runtime_status == TargetRuntimeStatus.IDLE
    assert second_state.consecutive_failure_count == 2
    assert third_state is not None
    assert third_state.runtime_status == TargetRuntimeStatus.ERROR
    assert third_state.consecutive_failure_reason == "page_load_timeout"
    assert third_state.consecutive_failure_count == 3
    assert "已連續 3 次失敗" in third_state.last_error


def test_success_idle_resets_failure_streak(tmp_path: Path) -> None:
    """成功回 idle 時需清除先前可重試失敗 streak。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        decision = app.services.targets.decide_scan_failure(
            target.id,
            "page_load_timeout",
            source="playwright",
        )
        app.services.targets.apply_scan_failure_decision(target.id, decision, "timeout")
        app.services.targets.mark_target_idle(target.id)
        state = app.repositories.runtime_states.get(target.id)

    assert state is not None
    assert state.consecutive_failure_reason == ""
    assert state.consecutive_failure_count == 0


def test_scan_skip_streak_escalates_on_third_skip(tmp_path: Path) -> None:
    """排序保護性 skip 連續三次才升級成 recoverable failure。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

        first = app.services.targets.decide_scan_skip(
            target.id,
            SORT_ADJUST_UNCONFIRMED_REASON,
            skip_limit=3,
        )
        app.services.targets.apply_scan_skip_decision(target.id, first)
        first_state = app.repositories.runtime_states.get(target.id)

        second = app.services.targets.decide_scan_skip(
            target.id,
            SORT_ADJUST_UNCONFIRMED_REASON,
            skip_limit=3,
        )
        app.services.targets.apply_scan_skip_decision(target.id, second)

        third = app.services.targets.decide_scan_skip(
            target.id,
            SORT_ADJUST_UNCONFIRMED_REASON,
            skip_limit=3,
        )
        third_state = app.repositories.runtime_states.get(target.id)

    assert first_state is not None
    assert first_state.consecutive_scan_skip_reason == SORT_ADJUST_UNCONFIRMED_REASON
    assert first_state.consecutive_scan_skip_count == 1
    assert not first.escalate
    assert not second.escalate
    assert third.escalate
    assert third_state is not None
    assert third_state.consecutive_scan_skip_count == 2


def test_scan_skip_preserves_failure_streak_until_real_success(
    tmp_path: Path,
) -> None:
    """排序 skipped success 不代表恢復，不能清掉已折算的 failure streak。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        failure = app.services.targets.decide_scan_failure(
            target.id,
            SORT_ADJUST_UNCONFIRMED_REASON,
            source="worker_failure",
        )
        app.services.targets.apply_scan_failure_decision(target.id, failure, "sort failed")

        first_skip = app.services.targets.decide_scan_skip(
            target.id,
            SORT_ADJUST_UNCONFIRMED_REASON,
            skip_limit=3,
        )
        app.services.targets.apply_scan_skip_decision(target.id, first_skip)
        skipped_state = app.repositories.runtime_states.get(target.id)
        app.services.targets.mark_target_idle(target.id)
        success_state = app.repositories.runtime_states.get(target.id)

    assert skipped_state is not None
    assert skipped_state.consecutive_failure_reason == SORT_ADJUST_UNCONFIRMED_REASON
    assert skipped_state.consecutive_failure_count == 1
    assert skipped_state.consecutive_scan_skip_count == 1
    assert success_state is not None
    assert success_state.consecutive_failure_reason == ""
    assert success_state.consecutive_failure_count == 0
    assert success_state.consecutive_scan_skip_reason == ""
    assert success_state.consecutive_scan_skip_count == 0


def test_target_status_update_resets_runtime_state(tmp_path: Path) -> None:
    """target 停止時 runtime reset 需清除錯誤與 retry streak。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        decision = app.services.targets.decide_scan_failure(
            target.id,
            "page_load_timeout",
            source="playwright",
        )
        app.services.targets.apply_scan_failure_decision(target.id, decision, "timeout")
        skip = app.services.targets.decide_scan_skip(
            target.id,
            SORT_ADJUST_UNCONFIRMED_REASON,
            skip_limit=3,
        )
        app.services.targets.apply_scan_skip_decision(target.id, skip)
        app.services.targets.pause_target_monitoring(target.id)
        state = app.repositories.runtime_states.get(target.id)

    assert state is not None
    assert state.desired_state == TargetDesiredState.STOPPED
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.scan_requested_at is None
    assert state.last_error == ""
    assert state.consecutive_failure_reason == ""
    assert state.consecutive_failure_count == 0
    assert state.consecutive_scan_skip_reason == ""
    assert state.consecutive_scan_skip_count == 0


def test_restart_target_monitoring_resets_runtime_and_requests_scan(
    tmp_path: Path,
) -> None:
    """target 開始時需清 runtime failure 並要求下一輪立即掃描。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        decision = app.services.targets.decide_scan_failure(
            target.id,
            "page_load_timeout",
            source="playwright",
        )
        app.services.targets.apply_scan_failure_decision(target.id, decision, "timeout")
        skip = app.services.targets.decide_scan_skip(
            target.id,
            SORT_ADJUST_UNCONFIRMED_REASON,
            skip_limit=3,
        )
        app.services.targets.apply_scan_skip_decision(target.id, skip)
        seeded_state = app.repositories.runtime_states.get(target.id)
        app.services.targets.restart_target_monitoring(target.id)
        state = app.repositories.runtime_states.get(target.id)

    assert seeded_state is not None
    assert seeded_state.consecutive_failure_count == 1
    assert seeded_state.consecutive_scan_skip_count == 1
    assert state is not None
    assert state.desired_state == TargetDesiredState.ACTIVE
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.scan_requested_at is not None
    assert state.last_error == ""
    assert state.consecutive_failure_reason == ""
    assert state.consecutive_failure_count == 0
    assert state.consecutive_scan_skip_reason == ""
    assert state.consecutive_scan_skip_count == 0


def test_scan_request_during_running_survives_current_scan_finish(
    tmp_path: Path,
) -> None:
    """target running 時再按 scan-once，完成目前掃描後仍保留下一輪要求。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.clear_target_scan_request(target.id)
        app.services.targets.mark_target_running(target.id, "worker-1")
        requested_state = app.services.targets.request_target_scan(target.id)
        finished_state = app.services.targets.mark_target_idle(target.id)

    assert requested_state.scan_requested_at is not None
    assert finished_state.runtime_status == TargetRuntimeStatus.IDLE
    assert finished_state.scan_requested_at == requested_state.scan_requested_at


def test_scan_request_during_queued_survives_current_scan_finish(
    tmp_path: Path,
) -> None:
    """target queued 時再按 scan-once，也應在本輪完成後保留下一輪要求。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.clear_target_scan_request(target.id)
        app.services.targets.mark_target_queued(target.id, "due")
        requested_state = app.services.targets.request_target_scan(target.id)
        app.services.targets.mark_target_running(target.id, "worker-1")
        finished_state = app.services.targets.mark_target_idle(target.id)

    assert requested_state.scan_requested_at is not None
    assert finished_state.runtime_status == TargetRuntimeStatus.IDLE
    assert finished_state.scan_requested_at == requested_state.scan_requested_at


def test_try_mark_target_running_claims_only_active_non_running_state(
    tmp_path: Path,
) -> None:
    """running claim 必須由 DB conditional update 原子判定。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        active_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="active",
                canonical_url="https://www.facebook.com/groups/active",
            )
        )
        stopped_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="stopped",
                canonical_url="https://www.facebook.com/groups/stopped",
            )
        )
        app.services.targets.restart_target_monitoring(active_target.id)
        claimed = app.services.targets.try_mark_target_running(
            active_target.id,
            "worker-a",
            page_id="page-a",
        )
        duplicate = app.services.targets.try_mark_target_running(
            active_target.id,
            "worker-b",
            page_id="page-b",
        )
        stopped_claim = app.services.targets.try_mark_target_running(
            stopped_target.id,
            "worker-c",
        )
        active_state = app.repositories.runtime_states.get(active_target.id)
        stopped_state = app.repositories.runtime_states.get(stopped_target.id)

    assert claimed is not None
    assert claimed.runtime_status == TargetRuntimeStatus.RUNNING
    assert claimed.active_worker_id == "worker-a"
    assert claimed.active_page_id == "page-a"
    assert duplicate is None
    assert stopped_claim is None
    assert active_state is not None
    assert active_state.active_worker_id == "worker-a"
    assert active_state.last_skip_reason.startswith("scan_guard_skipped: target_already_running")
    assert active_state.scan_guard_count == 1
    assert stopped_state is not None
    assert stopped_state.desired_state == TargetDesiredState.STOPPED
    assert stopped_state.runtime_status == TargetRuntimeStatus.IDLE
    assert "target_not_active" in stopped_state.last_skip_reason


def test_runtime_transition_invariants_for_running_finish_and_stop(
    tmp_path: Path,
) -> None:
    """running attempt 完成、失敗與 target stop 應保留既有 runtime 不變式。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        idle_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="idle",
                canonical_url="https://www.facebook.com/groups/idle",
            )
        )
        error_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="error",
                canonical_url="https://www.facebook.com/groups/error",
            )
        )
        stopped_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="stop",
                canonical_url="https://www.facebook.com/groups/stop",
            )
        )

        app.services.targets.restart_target_monitoring(idle_target.id)
        app.services.targets.clear_target_scan_request(idle_target.id)
        idle_running = app.services.targets.try_mark_target_running(
            idle_target.id,
            "worker-idle",
        )
        idle_requested = app.services.targets.request_target_scan(idle_target.id)
        idle_finished = app.services.targets.mark_target_idle(idle_target.id)

        app.services.targets.restart_target_monitoring(error_target.id)
        error_running = app.services.targets.try_mark_target_running(
            error_target.id,
            "worker-error",
        )
        error_state = app.services.targets.mark_target_error(
            error_target.id,
            "scan failed",
            failure_reason="unknown",
            failure_count=1,
        )

        stopped_claim = app.services.targets.try_mark_target_running(
            stopped_target.id,
            "worker-stop",
        )

    assert idle_running is not None
    assert idle_running.runtime_status == TargetRuntimeStatus.RUNNING
    assert idle_requested.scan_requested_at is not None
    assert idle_finished.runtime_status == TargetRuntimeStatus.IDLE
    assert idle_finished.scan_requested_at == idle_requested.scan_requested_at
    assert idle_finished.active_worker_id == ""
    assert idle_finished.last_error == ""

    assert error_running is not None
    assert error_state.runtime_status == TargetRuntimeStatus.ERROR
    assert error_state.scan_requested_at is None
    assert error_state.active_worker_id == ""
    assert error_state.last_error == "scan failed"
    assert error_state.consecutive_failure_reason == "unknown"
    assert error_state.consecutive_failure_count == 1

    assert stopped_claim is None


def test_owner_guarded_idle_transition_ignores_late_worker(
    tmp_path: Path,
) -> None:
    """舊 worker completion 不可覆蓋後續新 worker 的 running ownership。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="owner",
                canonical_url="https://www.facebook.com/groups/owner",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        stale_running = app.services.targets.try_mark_target_running(
            target.id,
            "worker-old",
            page_id="page-old",
        )
        assert stale_running is not None
        assert stale_running.last_started_at is not None
        app.services.targets.restart_target_monitoring(target.id)
        current_running = app.services.targets.try_mark_target_running(
            target.id,
            "worker-new",
            page_id="page-new",
        )
        stale_update = app.services.targets.mark_target_idle_if_owner(
            target.id,
            worker_id="worker-old",
            started_at=stale_running.last_started_at,
            page_id="page-old",
        )
        loaded = app.repositories.runtime_states.get(target.id)

    assert current_running is not None
    assert stale_update is None
    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.RUNNING
    assert loaded.active_worker_id == "worker-new"
    assert loaded.active_page_id == "page-new"


def test_owner_guarded_heartbeat_and_page_reload_ignore_late_worker(
    tmp_path: Path,
) -> None:
    """舊 worker heartbeat/page reload 不可覆蓋新 worker ownership。"""

    db_path = tmp_path / "app.db"
    reloaded_at = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="owner-heartbeat",
                canonical_url="https://www.facebook.com/groups/owner-heartbeat",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        old_running = app.services.targets.try_mark_target_running(
            target.id,
            "worker-old",
            page_id="page-old",
        )
        assert old_running is not None
        assert old_running.last_started_at is not None
        app.services.targets.restart_target_monitoring(target.id)
        new_running = app.services.targets.try_mark_target_running(
            target.id,
            "worker-new",
            page_id="page-new",
        )
        assert new_running is not None
        assert new_running.last_started_at is not None
        stale_heartbeat = app.services.targets.record_target_heartbeat_if_owner(
            target.id,
            worker_id="worker-old",
            started_at=old_running.last_started_at,
            page_id="page-old",
        )
        stale_page_reload = app.services.targets.mark_target_page_reloaded_if_owner(
            target.id,
            worker_id="worker-old",
            started_at=old_running.last_started_at,
            page_id="page-old",
            reloaded_at=reloaded_at,
        )
        current_page_reload = app.services.targets.mark_target_page_reloaded_if_owner(
            target.id,
            worker_id="worker-new",
            started_at=new_running.last_started_at,
            page_id="page-new",
            reloaded_at=reloaded_at,
        )
        loaded = app.repositories.runtime_states.get(target.id)

    assert stale_heartbeat is None
    assert stale_page_reload is None
    assert current_page_reload is not None
    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.RUNNING
    assert loaded.active_worker_id == "worker-new"
    assert loaded.active_page_id == "page-new"
    assert loaded.last_page_reloaded_at == reloaded_at


def test_clear_consumed_scan_request_preserves_newer_request(
    tmp_path: Path,
) -> None:
    """已入隊 request 的清除動作不得刪掉稍後送出的 scan-once。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        consumed_state = app.repositories.runtime_states.get(target.id)
        assert consumed_state is not None
        assert consumed_state.scan_requested_at is not None
        newer_state = app.services.targets.request_target_scan(target.id)

        cleared_state = app.services.targets.clear_target_scan_request_if_not_newer(
            target.id,
            consumed_state.scan_requested_at,
        )

    assert newer_state.scan_requested_at is not None
    assert cleared_state.scan_requested_at == newer_state.scan_requested_at
