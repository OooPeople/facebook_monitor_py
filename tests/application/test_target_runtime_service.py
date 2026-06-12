"""Application service tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
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


def test_mark_target_queued_only_updates_active_non_running_state(
    tmp_path: Path,
) -> None:
    """queue patch 只允許 active target，避免 stopped target 短暫顯示 queued。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        active_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="active-queue",
                canonical_url="https://www.facebook.com/groups/active-queue",
            )
        )
        running_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="running-queue",
                canonical_url="https://www.facebook.com/groups/running-queue",
            )
        )
        stopped_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="stopped-queue",
                canonical_url="https://www.facebook.com/groups/stopped-queue",
            )
        )
        app.services.targets.restart_target_monitoring(active_target.id)
        app.services.targets.restart_target_monitoring(running_target.id)
        running_state = app.services.targets.try_mark_target_running(
            running_target.id,
            "worker-running",
        )

        active_queued = app.services.targets.mark_target_queued(
            active_target.id,
            "manual_request",
        )
        running_queued = app.services.targets.mark_target_queued(
            running_target.id,
            "manual_request",
        )
        stopped_queued = app.services.targets.mark_target_queued(
            stopped_target.id,
            "manual_request",
        )

    assert running_state is not None
    assert active_queued.desired_state == TargetDesiredState.ACTIVE
    assert active_queued.runtime_status == TargetRuntimeStatus.QUEUED
    assert active_queued.enqueue_reason == "manual_request"

    assert running_queued.desired_state == TargetDesiredState.ACTIVE
    assert running_queued.runtime_status == TargetRuntimeStatus.RUNNING
    assert running_queued.active_worker_id == "worker-running"

    assert stopped_queued.desired_state == TargetDesiredState.STOPPED
    assert stopped_queued.runtime_status == TargetRuntimeStatus.IDLE
    assert stopped_queued.enqueue_reason == ""


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


def test_try_claim_target_running_alias_preserves_scan_lock_semantics(
    tmp_path: Path,
) -> None:
    """try_claim alias 必須維持原子 claim 與 duplicate rejection 語義。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="claim-alias",
                canonical_url="https://www.facebook.com/groups/claim-alias",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        claimed = app.services.targets.try_claim_target_running(
            target.id,
            "worker-a",
            page_id="page-a",
        )
        duplicate = app.services.targets.try_claim_target_running(
            target.id,
            "worker-b",
            page_id="page-b",
        )
        loaded = app.repositories.runtime_states.get(target.id)

    assert claimed is not None
    assert duplicate is None
    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.RUNNING
    assert loaded.active_worker_id == "worker-a"
    assert loaded.active_page_id == "page-a"
    assert loaded.scan_guard_count == 1


def test_guarded_runtime_aliases_ignore_late_worker(
    tmp_path: Path,
) -> None:
    """guarded aliases 不可讓舊 owner 覆蓋較新的 running attempt。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="guarded-alias",
                canonical_url="https://www.facebook.com/groups/guarded-alias",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        old_running = app.services.targets.try_claim_target_running(
            target.id,
            "worker-old",
            page_id="page-old",
        )
        assert old_running is not None
        assert old_running.last_started_at is not None
        app.services.targets.restart_target_monitoring(target.id)
        new_running = app.services.targets.try_claim_target_running(
            target.id,
            "worker-new",
            page_id="page-new",
        )
        assert new_running is not None

        skip_decision = app.services.targets.decide_scan_skip(
            target.id,
            SORT_ADJUST_UNCONFIRMED_REASON,
            skip_limit=3,
        )
        failure_decision = app.services.targets.decide_scan_failure(
            target.id,
            "page_load_timeout",
            source="playwright",
        )
        stale_heartbeat = app.services.targets.guarded_record_target_heartbeat(
            target.id,
            worker_id="worker-old",
            started_at=old_running.last_started_at,
            page_id="page-old",
        )
        stale_reload = app.services.targets.guarded_mark_target_page_reloaded(
            target.id,
            worker_id="worker-old",
            started_at=old_running.last_started_at,
            page_id="page-old",
        )
        stale_skip = app.services.targets.guarded_apply_scan_skip_decision(
            target.id,
            skip_decision,
            worker_id="worker-old",
            started_at=old_running.last_started_at,
            page_id="page-old",
        )
        stale_failure = app.services.targets.guarded_apply_scan_failure_decision(
            target.id,
            failure_decision,
            "timeout",
            worker_id="worker-old",
            started_at=old_running.last_started_at,
            page_id="page-old",
        )
        stale_retriable_failure = app.services.targets.guarded_mark_target_retriable_failure(
            target.id,
            failure_decision,
            worker_id="worker-old",
            started_at=old_running.last_started_at,
            page_id="page-old",
        )
        stale_error = app.services.targets.guarded_mark_target_error(
            target.id,
            "forced error",
            worker_id="worker-old",
            started_at=old_running.last_started_at,
            page_id="page-old",
        )
        stale_idle = app.services.targets.guarded_mark_target_idle(
            target.id,
            worker_id="worker-old",
            started_at=old_running.last_started_at,
            page_id="page-old",
        )
        loaded = app.repositories.runtime_states.get(target.id)

    assert stale_heartbeat is None
    assert stale_reload is None
    assert stale_skip is None
    assert stale_failure is None
    assert stale_retriable_failure is None
    assert stale_error is None
    assert stale_idle is None
    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.RUNNING
    assert loaded.active_worker_id == "worker-new"
    assert loaded.active_page_id == "page-new"
    assert loaded.last_error == ""
    assert loaded.last_skip_reason == ""


def test_guarded_retriable_failure_alias_accepts_matching_owner(
    tmp_path: Path,
) -> None:
    """guarded retriable failure alias 在 owner 相符時會回 idle 並保留 streak。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="guarded-retry-alias",
                canonical_url="https://www.facebook.com/groups/guarded-retry-alias",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        running = app.services.targets.try_claim_target_running(
            target.id,
            "worker-current",
            page_id="page-current",
        )
        assert running is not None
        assert running.last_started_at is not None
        decision = app.services.targets.decide_scan_failure(
            target.id,
            "page_load_timeout",
            source="playwright",
        )

        updated = app.services.targets.guarded_mark_target_retriable_failure(
            target.id,
            decision,
            worker_id="worker-current",
            started_at=running.last_started_at,
            page_id="page-current",
        )

    assert updated is not None
    assert updated.runtime_status == TargetRuntimeStatus.IDLE
    assert updated.active_worker_id == ""
    assert updated.active_page_id == ""
    assert updated.consecutive_failure_reason == "page_load_timeout"
    assert updated.consecutive_failure_count == 1


def test_force_runtime_aliases_explicitly_override_running_owner(
    tmp_path: Path,
) -> None:
    """force aliases 可覆寫 running owner，但語義必須由呼叫端顯式選用。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="force-alias",
                canonical_url="https://www.facebook.com/groups/force-alias",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        running = app.services.targets.try_claim_target_running(
            target.id,
            "worker-running",
            page_id="page-running",
        )
        assert running is not None
        forced_idle = app.services.targets.force_mark_target_idle(target.id)
        app.services.targets.force_mark_target_running(
            target.id,
            "worker-forced",
            page_id="page-forced",
        )
        forced_reload = app.services.targets.force_mark_target_page_reloaded(
            target.id,
            page_id="page-reloaded",
        )
        skip_decision = app.services.targets.decide_scan_skip(
            target.id,
            SORT_ADJUST_UNCONFIRMED_REASON,
            skip_limit=3,
        )
        forced_skip = app.services.targets.force_apply_scan_skip_decision(
            target.id,
            skip_decision,
        )
        app.services.targets.force_mark_target_running(
            target.id,
            "worker-retry",
            page_id="page-retry",
        )
        failure_decision = app.services.targets.decide_scan_failure(
            target.id,
            "page_load_timeout",
            source="playwright",
        )
        forced_retriable = app.services.targets.force_mark_target_retriable_failure(
            target.id,
            failure_decision,
        )
        app.services.targets.force_mark_target_running(
            target.id,
            "worker-failure",
            page_id="page-failure",
        )
        forced_failure = app.services.targets.force_apply_scan_failure_decision(
            target.id,
            failure_decision,
            "timeout",
        )
        forced_error = app.services.targets.force_mark_target_error(
            target.id,
            "terminal error",
            failure_reason="manual",
            failure_count=2,
        )

    assert forced_idle.runtime_status == TargetRuntimeStatus.IDLE
    assert forced_idle.active_worker_id == ""
    assert forced_reload.runtime_status == TargetRuntimeStatus.RUNNING
    assert forced_reload.active_worker_id == "worker-forced"
    assert forced_reload.active_page_id == "page-reloaded"
    assert forced_reload.last_page_reloaded_at is not None
    assert forced_skip.runtime_status == TargetRuntimeStatus.IDLE
    assert forced_skip.active_worker_id == ""
    assert forced_skip.last_skip_reason.startswith(SORT_ADJUST_UNCONFIRMED_REASON)
    assert forced_retriable.runtime_status == TargetRuntimeStatus.IDLE
    assert forced_retriable.active_worker_id == ""
    assert forced_retriable.consecutive_failure_reason == "page_load_timeout"
    assert forced_failure.runtime_status == TargetRuntimeStatus.IDLE
    assert forced_failure.active_worker_id == ""
    assert forced_failure.consecutive_failure_reason == "page_load_timeout"
    assert forced_error.runtime_status == TargetRuntimeStatus.ERROR
    assert forced_error.active_worker_id == ""
    assert forced_error.last_error == "terminal error"
    assert forced_error.consecutive_failure_reason == "manual"
    assert forced_error.consecutive_failure_count == 2


def test_runtime_restart_retry_recovery_clears_owner_and_requests_scan(
    tmp_path: Path,
) -> None:
    """runtime restart recovery 應集中由 service 清 owner 並留下補掃要求。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="runtime-restart-retry",
                canonical_url="https://www.facebook.com/groups/runtime-restart-retry",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        running = app.services.targets.try_claim_target_running(
            target.id,
            "worker-runtime-restart",
            page_id="page-runtime-restart",
        )
        assert running is not None

        recovered = app.services.targets.force_request_target_retry_after_runtime_restart(
            target.id,
        )
        loaded = app.repositories.runtime_states.get(target.id)

    assert recovered.runtime_status == TargetRuntimeStatus.IDLE
    assert recovered.scan_requested_at is not None
    assert recovered.active_worker_id == ""
    assert recovered.active_page_id == ""
    assert loaded == recovered


def test_sqlite_lock_retry_recovery_clears_owner_and_requests_scan(
    tmp_path: Path,
) -> None:
    """sqlite lock recovery 在 owner 相符時由 service 清 owner 並補掃。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="sqlite-lock-retry",
                canonical_url="https://www.facebook.com/groups/sqlite-lock-retry",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        running = app.services.targets.try_claim_target_running(
            target.id,
            "worker-sqlite-lock",
            page_id="page-sqlite-lock",
        )
        assert running is not None
        assert running.last_started_at is not None

        recovered = app.services.targets.record_guarded_target_retry_after_sqlite_lock(
            target.id,
            worker_id="worker-sqlite-lock",
            started_at=running.last_started_at,
            page_id="page-sqlite-lock",
        )
        loaded = app.repositories.runtime_states.get(target.id)

    assert recovered is not None
    assert recovered.runtime_status == TargetRuntimeStatus.IDLE
    assert recovered.scan_requested_at is not None
    assert recovered.active_worker_id == ""
    assert recovered.active_page_id == ""
    assert loaded == recovered


def test_sqlite_lock_retry_recovery_ignores_stale_owner(
    tmp_path: Path,
) -> None:
    """sqlite lock recovery 不可讓舊 owner 清掉較新的 running owner。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="sqlite-lock-stale",
                canonical_url="https://www.facebook.com/groups/sqlite-lock-stale",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        old_running = app.services.targets.try_claim_target_running(
            target.id,
            "worker-old",
            page_id="page-old",
        )
        assert old_running is not None
        assert old_running.last_started_at is not None
        app.services.targets.restart_target_monitoring(target.id)
        new_running = app.services.targets.try_claim_target_running(
            target.id,
            "worker-new",
            page_id="page-new",
        )
        assert new_running is not None

        stale_update = app.services.targets.record_guarded_target_retry_after_sqlite_lock(
            target.id,
            worker_id="worker-old",
            started_at=old_running.last_started_at,
            page_id="page-old",
        )
        loaded = app.repositories.runtime_states.get(target.id)

    assert stale_update is None
    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.RUNNING
    assert loaded.active_worker_id == "worker-new"
    assert loaded.active_page_id == "page-new"


def test_non_running_sqlite_lock_retry_recovery_does_not_override_running(
    tmp_path: Path,
) -> None:
    """claim 前 recovery 只能更新非 running row，不能清掉 active owner。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="sqlite-lock-non-running",
                canonical_url="https://www.facebook.com/groups/sqlite-lock-non-running",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        running = app.services.targets.try_claim_target_running(
            target.id,
            "worker-running",
            page_id="page-running",
        )
        assert running is not None

        blocked = app.services.targets.record_non_running_target_retry_after_sqlite_lock(
            target.id,
        )
        loaded = app.repositories.runtime_states.get(target.id)

    assert blocked is None
    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.RUNNING
    assert loaded.active_worker_id == "worker-running"
    assert loaded.active_page_id == "page-running"


def test_mark_target_idle_if_not_running_does_not_override_running_owner(
    tmp_path: Path,
) -> None:
    """pre-claim idle patch 不可清掉已被 worker claim 的 running owner。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="idle-if-not-running",
                canonical_url="https://www.facebook.com/groups/idle-if-not-running",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        running = app.services.targets.try_claim_target_running(
            target.id,
            "worker-running",
            page_id="page-running",
        )
        assert running is not None

        skipped = app.services.targets.mark_target_idle_if_not_running(target.id)
        loaded = app.repositories.runtime_states.get(target.id)

    assert skipped is None
    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.RUNNING
    assert loaded.active_worker_id == "worker-running"
    assert loaded.active_page_id == "page-running"


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


def test_runtime_skip_reason_patch_does_not_overwrite_running_owner(
    tmp_path: Path,
) -> None:
    """skip reason 診斷 patch 不可覆蓋較新的 running ownership。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="skip-owner",
                canonical_url="https://www.facebook.com/groups/skip-owner",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        running = app.services.targets.try_mark_target_running(
            target.id,
            "worker-new",
            page_id="page-new",
        )
        skipped = app.services.targets.record_scan_guard_skip(target.id, "manual_skip")
        loaded = app.repositories.runtime_states.get(target.id)

    assert running is not None
    assert skipped.runtime_status == TargetRuntimeStatus.RUNNING
    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.RUNNING
    assert loaded.active_worker_id == "worker-new"
    assert loaded.active_page_id == "page-new"
    assert loaded.last_skip_reason == "manual_skip"


def test_display_next_due_patch_does_not_overwrite_running_owner(
    tmp_path: Path,
) -> None:
    """display-only patch 不可覆蓋較新的 running ownership。"""

    db_path = tmp_path / "app.db"
    due_at = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="display-owner",
                canonical_url="https://www.facebook.com/groups/display-owner",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        running = app.services.targets.try_mark_target_running(
            target.id,
            "worker-new",
            page_id="page-new",
        )
        displayed = app.services.targets.set_target_display_next_due_at(target.id, due_at)
        loaded = app.repositories.runtime_states.get(target.id)

    assert running is not None
    assert displayed is not None
    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.RUNNING
    assert loaded.active_worker_id == "worker-new"
    assert loaded.active_page_id == "page-new"
    assert loaded.display_next_due_at == due_at


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
