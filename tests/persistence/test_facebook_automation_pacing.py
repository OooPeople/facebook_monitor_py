from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingAcquireOutcome
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingFinishOutcome
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingRecoveryOutcome


def _utc(hour: int, minute: int = 0) -> datetime:
    """建立 deterministic UTC clock。"""

    return datetime(2026, 7, 22, hour, minute, tzinfo=timezone.utc)


def test_pacing_lease_finish_persists_restart_quiet_period(tmp_path: Path) -> None:
    """finish 後新 context 仍須遵守 next_not_before。"""

    db_path = tmp_path / "app.db"
    started_at = _utc(9)
    with SqliteApplicationContext(db_path) as app:
        acquired = app.repositories.facebook_automation_pacing.try_acquire(
            "profile-1",
            operation_id="operation-1",
            work_kind="target_scan",
            owner_session_id="session-1",
            started_at=started_at,
            lease_expires_at=started_at + timedelta(minutes=5),
        )
        assert acquired.outcome == FacebookPacingAcquireOutcome.ACQUIRED
        assert acquired.token is not None
        finished = app.repositories.facebook_automation_pacing.finish(
            acquired.token,
            finished_at=started_at + timedelta(minutes=1),
            next_not_before=started_at + timedelta(minutes=1, seconds=30),
            outcome="success",
        )
        assert finished.outcome == FacebookPacingFinishOutcome.UPDATED

    with SqliteApplicationContext(db_path) as app:
        too_early = app.repositories.facebook_automation_pacing.try_acquire(
            "profile-1",
            operation_id="operation-2",
            work_kind="metadata_refresh",
            owner_session_id="session-2",
            started_at=started_at + timedelta(minutes=1, seconds=20),
            lease_expires_at=started_at + timedelta(minutes=6),
        )
        assert too_early.outcome == FacebookPacingAcquireOutcome.QUIET_PERIOD
        admitted = app.repositories.facebook_automation_pacing.try_acquire(
            "profile-1",
            operation_id="operation-2",
            work_kind="metadata_refresh",
            owner_session_id="session-2",
            started_at=started_at + timedelta(minutes=1, seconds=30),
            lease_expires_at=started_at + timedelta(minutes=6),
        )
        assert admitted.outcome == FacebookPacingAcquireOutcome.ACQUIRED


def test_pacing_stale_finish_cannot_clear_new_owner(tmp_path: Path) -> None:
    """舊 generation/operation owner 的 finally 不得清掉新 lease。"""

    db_path = tmp_path / "app.db"
    now = _utc(9)
    with SqliteApplicationContext(db_path) as app:
        first = app.repositories.facebook_automation_pacing.try_acquire(
            "profile-1",
            operation_id="operation-1",
            work_kind="target_scan",
            owner_session_id="session-1",
            started_at=now,
            lease_expires_at=now + timedelta(seconds=1),
        )
        assert first.token is not None
        recovered = app.repositories.facebook_automation_pacing.recover_expired(
            "profile-1",
            recovered_at=now + timedelta(seconds=1),
            next_not_before=now + timedelta(seconds=1),
        )
        assert recovered.outcome == FacebookPacingRecoveryOutcome.RECOVERED
        second = app.repositories.facebook_automation_pacing.try_acquire(
            "profile-1",
            operation_id="operation-2",
            work_kind="cover_refresh",
            owner_session_id="session-2",
            started_at=now + timedelta(seconds=1),
            lease_expires_at=now + timedelta(minutes=5),
        )
        assert second.outcome == FacebookPacingAcquireOutcome.ACQUIRED
        stale = app.repositories.facebook_automation_pacing.finish(
            first.token,
            finished_at=now + timedelta(seconds=2),
            next_not_before=now + timedelta(seconds=30),
            outcome="stale-finally",
        )
        assert stale.outcome == FacebookPacingFinishOutcome.STALE_OWNER
        assert stale.state.active_operation_id == "operation-2"


def test_pacing_unexpired_owner_fails_closed_until_recovery(tmp_path: Path) -> None:
    """restart 不可搶未過期 owner；到期 recovery 後仍套完整 quiet gap。"""

    db_path = tmp_path / "app.db"
    now = _utc(9)
    with SqliteApplicationContext(db_path) as app:
        acquired = app.repositories.facebook_automation_pacing.try_acquire(
            "profile-1",
            operation_id="operation-1",
            work_kind="target_scan",
            owner_session_id="session-1",
            started_at=now,
            lease_expires_at=now + timedelta(minutes=5),
        )
        assert acquired.outcome == FacebookPacingAcquireOutcome.ACQUIRED
        early = app.repositories.facebook_automation_pacing.recover_expired(
            "profile-1",
            recovered_at=now + timedelta(minutes=4),
            next_not_before=now + timedelta(minutes=4, seconds=30),
        )
        assert early.outcome == FacebookPacingRecoveryOutcome.NOT_EXPIRED
        blocked = app.repositories.facebook_automation_pacing.try_acquire(
            "profile-1",
            operation_id="operation-2",
            work_kind="target_scan",
            owner_session_id="session-2",
            started_at=now + timedelta(minutes=4),
            lease_expires_at=now + timedelta(minutes=9),
        )
        assert blocked.outcome == FacebookPacingAcquireOutcome.ACTIVE_LEASE
