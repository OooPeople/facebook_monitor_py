"""Stale normal-session recovery schema/repository/application tests。"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
import sqlite3

import pytest

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.facebook_session_recovery_service import (
    FacebookSessionRecoveryService,
)
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.defaults import FacebookAccessDefaults
from facebook_monitor.core.defaults import FacebookAutomationDefaults
from facebook_monitor.core.facebook_access import FacebookProbeResult
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingAcquireOutcome
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryClaimOutcome,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryFinishOutcome,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryLeaseOutcome,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryReconcileOutcome,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryRequestOutcome,
)
from facebook_monitor.core.facebook_session_recovery import FacebookSessionRecoveryStatus
from facebook_monitor.persistence.current_schema import create_current_schema
from facebook_monitor.persistence.migrations import migrate_42_to_43
from facebook_monitor.persistence.sqlite_connection import SqliteConnection
from tests.persistence.sqlite_test_helpers import table_sql


_NOW = datetime(2026, 9, 19, 1, 2, 3, tzinfo=UTC)
_MARKER_OWNER = "11111111-1111-4111-8111-111111111111"
_OTHER_OWNER = "22222222-2222-4222-8222-222222222222"
_REQUEST_1 = "33333333-3333-4333-8333-333333333333"
_TOKEN_1 = "44444444-4444-4444-8444-444444444444"
_REQUEST_2 = "55555555-5555-4555-8555-555555555555"
_TOKEN_2 = "66666666-6666-4666-8666-666666666666"


def test_v42_to_v43_migration_is_idempotent_and_matches_current_schema(
    tmp_path: Path,
) -> None:
    """v43 recovery DDL 可重跑，且 migration/current schema 不漂移。"""

    db_path = tmp_path / "migration.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.execute("CREATE TABLE targets (id TEXT PRIMARY KEY)")
        migrate_42_to_43(connection)
        migrate_42_to_43(connection)
        migrated = _normalized_sql(
            table_sql(connection, "facebook_session_recovery_state")
        )
        indexes = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }

    expected = sqlite3.connect(":memory:")
    expected.row_factory = sqlite3.Row
    try:
        create_current_schema(expected)
        current = _normalized_sql(
            table_sql(expected, "facebook_session_recovery_state")
        )
    finally:
        expected.close()

    assert migrated == current
    assert "idx_facebook_session_recovery_status_lease" in indexes


def test_recovery_schema_rejects_incomplete_probe_owner(tmp_path: Path) -> None:
    """Probing state 缺 request/token/lease 任一欄位都不得寫入。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        connection = app.repositories.targets.connection
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO facebook_session_recovery_state (
                    profile_scope_key, status, marker_session_id,
                    stale_detected_at, earliest_probe_at, updated_at
                ) VALUES (?, 'probing', ?, ?, ?, ?)
                """,
                (
                    "scope",
                    _MARKER_OWNER,
                    _NOW.isoformat(),
                    _NOW.isoformat(),
                    _NOW.isoformat(),
                ),
            )


def test_reconcile_stale_owner_recovers_pacing_and_applies_full_quiet_gap(
    tmp_path: Path,
) -> None:
    """即使 pacing lease 未到期，相符 stale owner 仍只在 DB 清理並套完整 gap。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        acquired = app.repositories.facebook_automation_pacing.try_acquire(
            "scope",
            operation_id="old-operation",
            work_kind="target_scan",
            owner_session_id=_MARKER_OWNER,
            started_at=_NOW - timedelta(minutes=1),
            lease_expires_at=_NOW + timedelta(minutes=4),
        )
        assert acquired.outcome == FacebookPacingAcquireOutcome.ACQUIRED
        service = _service(app, ids=())

        result = service.reconcile_stale_session(
            "scope",
            marker_session_id=_MARKER_OWNER,
            reconciled_at=_NOW,
        )
        repeated = service.reconcile_stale_session(
            "scope",
            marker_session_id=_MARKER_OWNER,
            reconciled_at=_NOW + timedelta(seconds=1),
        )
        pacing = app.repositories.facebook_automation_pacing.get("scope")

        assert result.outcome == FacebookSessionRecoveryReconcileOutcome.RECORDED
        assert result.state.status == FacebookSessionRecoveryStatus.HOLD
        assert result.state.earliest_probe_at == _NOW + timedelta(seconds=30)
        assert repeated.outcome == (
            FacebookSessionRecoveryReconcileOutcome.ALREADY_RECORDED
        )
        assert repeated.state.generation == result.state.generation
        assert repeated.state.earliest_probe_at == _NOW + timedelta(seconds=31)
        assert pacing is not None
        assert pacing.active_operation_id == ""
        assert pacing.owner_session_id == ""
        assert pacing.next_automation_not_before == _NOW + timedelta(seconds=31)
        assert pacing.last_outcome == "recovered_stale_session"


def test_reconcile_does_not_clear_different_active_pacing_owner(tmp_path: Path) -> None:
    """Marker owner 與目前 pacing owner 不符時保留 owner並回傳 bounded mismatch。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        app.repositories.facebook_automation_pacing.try_acquire(
            "scope",
            operation_id="current-operation",
            work_kind="target_scan",
            owner_session_id=_OTHER_OWNER,
            started_at=_NOW,
            lease_expires_at=_NOW + timedelta(minutes=5),
        )
        result = _service(app, ids=()).reconcile_stale_session(
            "scope",
            marker_session_id=_MARKER_OWNER,
            reconciled_at=_NOW,
        )
        pacing = app.repositories.facebook_automation_pacing.get("scope")

        assert result.outcome == (
            FacebookSessionRecoveryReconcileOutcome.PACING_OWNER_MISMATCH
        )
        assert result.state.status == FacebookSessionRecoveryStatus.HOLD
        assert pacing is not None
        assert pacing.active_operation_id == "current-operation"
        assert pacing.owner_session_id == _OTHER_OWNER


def test_request_claim_finish_are_generation_and_token_fenced(tmp_path: Path) -> None:
    """Recovery request/claim/finish 需保持 target eligibility 與雙重 CAS fence。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="recovery",
                canonical_url="https://www.facebook.com/groups/recovery",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        service = _service(
            app,
            ids=(_REQUEST_1, _TOKEN_1, _REQUEST_2, _TOKEN_2),
        )
        service.reconcile_stale_session(
            "scope",
            marker_session_id=_MARKER_OWNER,
            reconciled_at=_NOW,
        )

        too_early = service.request_probe(
            "scope",
            target_id=target.id,
            operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
            requested_at=_NOW + timedelta(seconds=29),
        )
        requested = service.request_probe(
            "scope",
            target_id=target.id,
            operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
            requested_at=_NOW + timedelta(seconds=30),
        )
        duplicate = service.request_probe(
            "scope",
            target_id=target.id,
            operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
            requested_at=_NOW + timedelta(seconds=30),
        )

        assert too_early.outcome == (
            FacebookSessionRecoveryRequestOutcome.QUIET_PERIOD_ACTIVE
        )
        assert requested.outcome == FacebookSessionRecoveryRequestOutcome.REQUESTED
        assert duplicate.outcome == (
            FacebookSessionRecoveryRequestOutcome.ALREADY_PENDING
        )
        assert requested.state.requested_target_id == target.id

        wrong_request = service.claim_probe(
            "scope",
            request_id="77777777-7777-4777-8777-777777777777",
            started_at=_NOW + timedelta(seconds=31),
        )
        claimed = service.claim_probe(
            "scope",
            request_id=requested.state.request_id,
            started_at=_NOW + timedelta(seconds=31),
        )

        assert wrong_request.outcome == (
            FacebookSessionRecoveryClaimOutcome.REQUEST_MISMATCH
        )
        assert claimed.outcome == FacebookSessionRecoveryClaimOutcome.CLAIMED
        assert claimed.state is not None
        assert claimed.target_id == target.id
        probing_generation = claimed.state.generation

        stale_finish = service.finish_probe(
            "scope",
            generation=probing_generation,
            probe_token="88888888-8888-4888-8888-888888888888",
            result=FacebookProbeResult.BLOCKED,
            finished_at=_NOW + timedelta(seconds=32),
        )
        blocked = service.finish_probe(
            "scope",
            generation=probing_generation,
            probe_token=claimed.state.probe_token,
            result=FacebookProbeResult.BLOCKED,
            finished_at=_NOW + timedelta(seconds=32),
        )

        assert stale_finish.outcome == FacebookSessionRecoveryFinishOutcome.STALE_OWNER
        assert blocked.outcome == FacebookSessionRecoveryFinishOutcome.UPDATED
        assert blocked.state is not None
        assert blocked.state.status == FacebookSessionRecoveryStatus.HOLD
        assert blocked.state.last_probe_result == FacebookProbeResult.BLOCKED
        assert blocked.state.earliest_probe_at == _NOW + timedelta(seconds=62)

        second_request = service.request_probe(
            "scope",
            target_id=target.id,
            operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
            requested_at=_NOW + timedelta(seconds=62),
        )
        second_claim = service.claim_probe(
            "scope",
            request_id=second_request.state.request_id,
            started_at=_NOW + timedelta(seconds=63),
        )
        assert second_claim.state is not None
        succeeded = service.finish_probe(
            "scope",
            generation=second_claim.state.generation,
            probe_token=second_claim.state.probe_token,
            result=FacebookProbeResult.SUCCESS,
            finished_at=_NOW + timedelta(seconds=64),
        )

        assert succeeded.outcome == FacebookSessionRecoveryFinishOutcome.UPDATED
        assert succeeded.state is not None
        assert succeeded.state.status == FacebookSessionRecoveryStatus.RECOVERED
        assert succeeded.state.recovered_at == _NOW + timedelta(seconds=64)
        assert succeeded.state.request_id == ""
        assert succeeded.state.probe_token == ""


def test_expired_probe_lease_returns_to_hold_with_full_quiet_gap(
    tmp_path: Path,
) -> None:
    """過期 probe lease 只能 DB-only CAS 回 hold，且保留 cancelled result。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="lease",
                canonical_url="https://www.facebook.com/groups/lease",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        service = _service(app, ids=(_REQUEST_1, _TOKEN_1))
        service.reconcile_stale_session(
            "scope",
            marker_session_id=_MARKER_OWNER,
            reconciled_at=_NOW,
        )
        requested = service.request_probe(
            "scope",
            target_id=target.id,
            operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
            requested_at=_NOW + timedelta(seconds=30),
        )
        service.claim_probe(
            "scope",
            request_id=requested.state.request_id,
            started_at=_NOW + timedelta(seconds=31),
        )

        early = service.recover_expired_probe(
            "scope",
            recovered_at=_NOW + timedelta(seconds=90),
        )
        expired = service.recover_expired_probe(
            "scope",
            recovered_at=_NOW + timedelta(seconds=92),
        )

        assert early.outcome == FacebookSessionRecoveryLeaseOutcome.NOT_EXPIRED
        assert expired.outcome == FacebookSessionRecoveryLeaseOutcome.RECOVERED
        assert expired.state is not None
        assert expired.state.status == FacebookSessionRecoveryStatus.HOLD
        assert expired.state.last_probe_result == FacebookProbeResult.CANCELLED
        assert expired.state.earliest_probe_at == _NOW + timedelta(seconds=122)


def test_repository_claim_atomically_rejects_target_paused_after_request(
    tmp_path: Path,
) -> None:
    """Request後target失效時，repository CAS本身也不得建立probing owner。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="claim-race",
                canonical_url="https://www.facebook.com/groups/claim-race",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        service = _service(app, ids=(_REQUEST_1,))
        service.reconcile_stale_session(
            "scope",
            marker_session_id=_MARKER_OWNER,
            reconciled_at=_NOW,
        )
        requested = service.request_probe(
            "scope",
            target_id=target.id,
            operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
            requested_at=_NOW + timedelta(seconds=30),
        )
        app.services.targets.pause_target_monitoring(target.id)

        claim = app.repositories.facebook_session_recovery.claim_probe(
            "scope",
            request_id=requested.state.request_id,
            probe_token=_TOKEN_1,
            started_at=_NOW + timedelta(seconds=31),
            lease_expires_at=_NOW + timedelta(seconds=91),
        )
        current = app.repositories.facebook_session_recovery.get("scope")

        assert claim.outcome == FacebookSessionRecoveryClaimOutcome.TARGET_UNAVAILABLE
        assert current is not None
        assert current.status == FacebookSessionRecoveryStatus.PROBE_PENDING
        assert current.probe_token == ""


def _service(
    app: ApplicationContext,
    *,
    ids: tuple[str, ...],
) -> FacebookSessionRecoveryService:
    """建立 deterministic recovery service。"""

    iterator = iter(ids)
    return FacebookSessionRecoveryService(
        app.repositories.facebook_session_recovery,
        app.repositories.facebook_automation_pacing,
        app.repositories.targets,
        automation_defaults=FacebookAutomationDefaults(
            persistent_quiet_gap_seconds=30,
        ),
        access_defaults=FacebookAccessDefaults(half_open_lease_seconds=60),
        id_factory=lambda: next(iterator),
    )


def _normalized_sql(value: str) -> str:
    """忽略 migration/current schema 排版差異。"""

    return "".join(value.split()).lower()
