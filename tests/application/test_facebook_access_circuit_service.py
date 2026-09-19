"""Facebook access circuit application/repository CAS tests。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
import sqlite3
from threading import Barrier

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.facebook_access_circuit_service import (
    FacebookAccessCircuitService,
)
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.facebook_access import FacebookAccessBlockSignal
from facebook_monitor.core.facebook_access import FacebookAccessCircuitStatus
from facebook_monitor.core.facebook_access import FacebookAccessSignalConfidence
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookAdmissionOutcome
from facebook_monitor.core.facebook_access import FacebookAdmissionToken
from facebook_monitor.core.facebook_access import FacebookCircuitTripOutcome
from facebook_monitor.core.facebook_access import FacebookHalfOpenClaimOutcome
from facebook_monitor.core.facebook_access import FacebookLeaseRecoveryOutcome
from facebook_monitor.core.facebook_access import FacebookProbeFinishOutcome
from facebook_monitor.core.facebook_access import FacebookProbeRequestOutcome
from facebook_monitor.core.facebook_access import FacebookProbeResult
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookRecoveryRecipeKind
from facebook_monitor.core.facebook_access import FacebookWorkSourceKind
from facebook_monitor.persistence.repositories.facebook_access_circuit import (
    FacebookAccessCircuitRepository,
)
from facebook_monitor.persistence.repositories.targets import TargetRepository


def test_trip_manual_probe_and_finish_use_generation_cas(tmp_path: Path) -> None:
    """Trip/repeated/request/claim/reopen/close 全程保留 generation owner。"""

    started = datetime(2026, 7, 1, tzinfo=UTC)
    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="group-a",
                canonical_url="https://www.facebook.com/groups/group-a",
            )
        )
        comments_target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="group-a",
                parent_post_id="post-a",
                canonical_url="https://www.facebook.com/groups/group-a/posts/post-a",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.restart_target_monitoring(comments_target.id)
        service = _deterministic_service(app, started)
        admission = service.admit_normal(
            "scope-a",
            process_safety_epoch=7,
            operation_id="operation-a",
        )
        assert admission.outcome == FacebookAdmissionOutcome.ALLOWED
        assert admission.token is not None
        assert service.admission_is_current(
            admission.token,
            process_safety_epoch=7,
        )
        assert not service.admission_is_current(
            admission.token,
            process_safety_epoch=8,
        )

        signal = _block_signal(admission.token, target.id)
        opened = service.trip(signal, source_owner_is_valid=True)
        revision_after_open = (
            app.repositories.dashboard_revision.get_dashboard_revision()
        )
        repeated = service.trip(signal, source_owner_is_valid=True)
        revision_after_repeated = (
            app.repositories.dashboard_revision.get_dashboard_revision()
        )

        assert opened.outcome == FacebookCircuitTripOutcome.OPENED
        assert opened.state.generation == 1
        assert opened.state.recovery_recipe_kind == (
            FacebookRecoveryRecipeKind.GROUP_FEED_DOCUMENT_GUARD_V1
        )
        assert repeated.outcome == FacebookCircuitTripOutcome.REPEATED
        assert repeated.state.episode_id == opened.state.episode_id
        assert repeated.state.detection_count == 2
        assert revision_after_repeated == revision_after_open
        assert not service.admission_is_current(
            admission.token,
            process_safety_epoch=7,
        )
        assert (
            service.admit_normal(
                "scope-a",
                process_safety_epoch=8,
                operation_id="operation-b",
            ).outcome
            == FacebookAdmissionOutcome.DEFERRED_OPEN
        )

        too_early = service.request_probe(
            "scope-a",
            target_id=target.id,
            requested_at=started + timedelta(hours=11),
        )
        assert too_early.outcome == FacebookProbeRequestOutcome.COOLDOWN_ACTIVE
        wrong_canary = service.request_probe(
            "scope-a",
            target_id=comments_target.id,
            requested_at=started + timedelta(hours=12),
        )
        assert wrong_canary.outcome == FacebookProbeRequestOutcome.TARGET_UNAVAILABLE
        requested = service.request_probe(
            "scope-a",
            target_id=target.id,
            requested_at=started + timedelta(hours=12),
        )
        assert requested.outcome == FacebookProbeRequestOutcome.REQUESTED

        first_claim = service.claim_half_open(
            "scope-a",
            request_id=requested.state.probe_request_id,
            started_at=started + timedelta(hours=12),
        )
        second_claim = service.claim_half_open(
            "scope-a",
            request_id=requested.state.probe_request_id,
            started_at=started + timedelta(hours=12),
        )
        assert first_claim.outcome == FacebookHalfOpenClaimOutcome.CLAIMED
        assert first_claim.state is not None
        assert first_claim.recipe_kind == (FacebookRecoveryRecipeKind.GROUP_FEED_DOCUMENT_GUARD_V1)
        assert second_claim.outcome == FacebookHalfOpenClaimOutcome.REJECTED_STATE

        stale_finish = service.finish_probe(
            "scope-a",
            half_open_token="stale-owner",
            generation=first_claim.state.generation,
            result=FacebookProbeResult.SUCCESS,
            finished_at=started + timedelta(hours=12, minutes=1),
        )
        assert stale_finish.outcome == FacebookProbeFinishOutcome.STALE_OWNER
        reopened = service.finish_probe(
            "scope-a",
            half_open_token=first_claim.state.half_open_token,
            generation=first_claim.state.generation,
            result=FacebookProbeResult.BLOCKED,
            finished_at=started + timedelta(hours=12, minutes=2),
        )
        assert reopened.outcome == FacebookProbeFinishOutcome.UPDATED
        assert reopened.state is not None
        assert reopened.state.status == FacebookAccessCircuitStatus.OPEN
        assert reopened.state.reopen_count == 1
        assert reopened.state.cooldown_until == started + timedelta(
            hours=36,
            minutes=2,
        )

        second_request = service.request_probe(
            "scope-a",
            target_id=target.id,
            requested_at=reopened.state.cooldown_until,
        )
        second_owner = service.claim_half_open(
            "scope-a",
            request_id=second_request.state.probe_request_id,
            started_at=reopened.state.cooldown_until,
        )
        assert second_owner.state is not None
        closed = service.finish_probe(
            "scope-a",
            half_open_token=second_owner.state.half_open_token,
            generation=second_owner.state.generation,
            result=FacebookProbeResult.SUCCESS,
            finished_at=reopened.state.cooldown_until + timedelta(minutes=1),
        )

        assert closed.outcome == FacebookProbeFinishOutcome.UPDATED
        assert closed.state is not None
        assert closed.state.status == FacebookAccessCircuitStatus.CLOSED
        assert closed.state.generation == 5
        assert [
            event.event_kind.value for event in service.repository.list_recent_events("scope-a")
        ] == [
            "probe_succeeded",
            "half_open_acquired",
            "probe_requested",
            "probe_blocked",
            "half_open_acquired",
            "probe_requested",
            "repeated_detection",
            "opened",
        ]


def test_invalid_owner_and_comments_recipe_remain_fail_closed(tmp_path: Path) -> None:
    """Invalid owner 不得 trip；comments Phase 4 前不得建立 recovery request。"""

    started = datetime(2026, 7, 1, tzinfo=UTC)
    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="group-comments",
                canonical_url="https://www.facebook.com/groups/group-comments",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        service = _deterministic_service(app, started)
        assert service.request_probe(
            "closed-scope",
            target_id=target.id,
            requested_at=started,
        ).outcome == FacebookProbeRequestOutcome.REJECTED_STATE
        admission = service.admit_normal(
            "comments-scope",
            process_safety_epoch=0,
            operation_id="comments-operation",
        )
        assert admission.token is not None
        signal = _block_signal(
            admission.token,
            target.id,
            operation_kind=FacebookProductOperationKind.COMMENTS_ACCESS,
        )

        rejected = service.trip(signal, source_owner_is_valid=False)
        assert rejected.outcome == FacebookCircuitTripOutcome.REJECTED_OWNER
        opened = service.trip(signal, source_owner_is_valid=True)
        assert opened.outcome == FacebookCircuitTripOutcome.OPENED
        assert opened.state.recovery_recipe_kind == FacebookRecoveryRecipeKind.NONE
        request = service.request_probe(
            "comments-scope",
            target_id=target.id,
            requested_at=started + timedelta(hours=12),
        )
        assert request.outcome == FacebookProbeRequestOutcome.RECIPE_UNAVAILABLE


def test_probe_candidates_allow_same_operation_alternative_target(
    tmp_path: Path,
) -> None:
    """Trigger 失效時只列出同 operation active canary，並可指定替代目標。"""

    started = datetime(2026, 7, 1, tzinfo=UTC)
    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        trigger = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="group-trigger",
                canonical_url="https://www.facebook.com/groups/group-trigger",
            )
        )
        alternative = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="group-alternative",
                canonical_url="https://www.facebook.com/groups/group-alternative",
            )
        )
        wrong_operation = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="group-trigger",
                parent_post_id="post-a",
                canonical_url=(
                    "https://www.facebook.com/groups/group-trigger/posts/post-a"
                ),
            )
        )
        for target in (trigger, alternative, wrong_operation):
            app.services.targets.restart_target_monitoring(target.id)
        service = _deterministic_service(app, started)
        admission = service.admit_normal(
            "alternative-scope",
            process_safety_epoch=1,
            operation_id="operation",
        )
        assert admission.token is not None
        service.trip(
            _block_signal(admission.token, trigger.id),
            source_owner_is_valid=True,
        )
        app.services.targets.pause_target_monitoring(trigger.id)

        candidates = service.list_probe_target_candidates("alternative-scope")
        wrong_request = service.request_probe(
            "alternative-scope",
            target_id=wrong_operation.id,
            requested_at=started + timedelta(hours=12),
        )
        requested = service.request_probe(
            "alternative-scope",
            target_id=alternative.id,
            requested_at=started + timedelta(hours=12),
        )

        assert [candidate.id for candidate in candidates] == [alternative.id]
        assert wrong_request.outcome == FacebookProbeRequestOutcome.TARGET_UNAVAILABLE
        assert requested.outcome == FacebookProbeRequestOutcome.REQUESTED
        assert requested.state.requested_target_id == alternative.id


def test_claim_revalidates_target_and_cancels_unavailable_request(
    tmp_path: Path,
) -> None:
    """Canary 在 request 後失效時不得 claim，且釋放 pending 供重新選擇。"""

    started = datetime(2026, 7, 1, tzinfo=UTC)
    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="group-stale-canary",
                canonical_url="https://www.facebook.com/groups/group-stale-canary",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        service = _deterministic_service(app, started)
        admission = service.admit_normal(
            "stale-canary-scope",
            process_safety_epoch=1,
            operation_id="operation",
        )
        assert admission.token is not None
        service.trip(
            _block_signal(admission.token, target.id),
            source_owner_is_valid=True,
        )
        requested = service.request_probe(
            "stale-canary-scope",
            target_id=target.id,
            requested_at=started + timedelta(hours=12),
        )
        app.services.targets.pause_target_monitoring(target.id)

        claim = service.claim_half_open(
            "stale-canary-scope",
            request_id=requested.state.probe_request_id,
            started_at=started + timedelta(hours=12, minutes=1),
        )

        assert claim.outcome == FacebookHalfOpenClaimOutcome.TARGET_UNAVAILABLE
        assert claim.state is not None
        assert claim.state.status == FacebookAccessCircuitStatus.OPEN
        assert claim.state.probe_request_id == ""
        assert claim.state.last_probe_result == FacebookProbeResult.CANCELLED
        assert service.repository.list_recent_events("stale-canary-scope")[0].event_kind.value == (
            "probe_cancelled"
        )


def test_expired_half_open_lease_recovers_once(tmp_path: Path) -> None:
    """Restart recovery 只回收已過期 lease，重送不會再改 generation。"""

    started = datetime(2026, 7, 1, tzinfo=UTC)
    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="group-recovery",
                canonical_url="https://www.facebook.com/groups/group-recovery",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        service = _deterministic_service(app, started)
        admission = service.admit_normal(
            "recovery-scope",
            process_safety_epoch=1,
            operation_id="operation",
        )
        assert admission.token is not None
        service.trip(
            _block_signal(admission.token, target.id),
            source_owner_is_valid=True,
        )
        requested = service.request_probe(
            "recovery-scope",
            target_id=target.id,
            requested_at=started + timedelta(hours=12),
        )
        owner = service.claim_half_open(
            "recovery-scope",
            request_id=requested.state.probe_request_id,
            started_at=started + timedelta(hours=12),
        )
        assert owner.state is not None

        not_expired = service.recover_expired_half_open(
            "recovery-scope",
            recovered_at=started + timedelta(hours=12, minutes=4),
        )
        recovered = service.recover_expired_half_open(
            "recovery-scope",
            recovered_at=started + timedelta(hours=12, minutes=5),
        )
        repeated = service.recover_expired_half_open(
            "recovery-scope",
            recovered_at=started + timedelta(hours=12, minutes=6),
        )

        assert not_expired.outcome == FacebookLeaseRecoveryOutcome.NOT_EXPIRED
        assert recovered.outcome == FacebookLeaseRecoveryOutcome.RECOVERED
        assert recovered.state is not None
        assert recovered.state.generation == owner.state.generation + 1
        assert recovered.state.last_probe_result == FacebookProbeResult.CANCELLED
        assert repeated.outcome == FacebookLeaseRecoveryOutcome.NOT_HALF_OPEN


def test_target_delete_nulls_aliasable_references_but_keeps_circuit(
    tmp_path: Path,
) -> None:
    """Target delete 只 SET NULL trigger/event reference，不得清除 profile circuit。"""

    started = datetime(2026, 7, 1, tzinfo=UTC)
    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="group-delete",
                canonical_url="https://www.facebook.com/groups/group-delete",
            )
        )
        service = _deterministic_service(app, started)
        admission = service.admit_normal(
            "delete-scope",
            process_safety_epoch=1,
            operation_id="operation",
        )
        assert admission.token is not None
        service.trip(
            _block_signal(admission.token, target.id),
            source_owner_is_valid=True,
        )

        app.services.targets.delete_target(target.id)

        state = service.get("delete-scope")
        events = service.repository.list_recent_events("delete-scope")
        assert state is not None
        assert state.status == FacebookAccessCircuitStatus.OPEN
        assert state.trigger_target_id is None
        assert events[0].target_id is None


def test_concurrent_trip_and_half_open_claim_have_single_cas_winner(
    tmp_path: Path,
) -> None:
    """多 connection 競爭只能產生一個 open episode 與一個 half-open owner。"""

    started = datetime(2026, 7, 1, tzinfo=UTC)
    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="group-race",
                canonical_url="https://www.facebook.com/groups/group-race",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        admission = app.services.facebook_access_circuit.admit_normal(
            "race-scope",
            process_safety_epoch=1,
            operation_id="operation",
            admitted_at=started,
        )
        assert admission.token is not None
        signal = _block_signal(admission.token, target.id)

    trip_barrier = Barrier(2)

    def trip_from_connection(worker_id: int) -> FacebookCircuitTripOutcome:
        connection = _open_connection(db_path)
        try:
            service = FacebookAccessCircuitService(
                FacebookAccessCircuitRepository(connection),
                TargetRepository(connection),
                clock=lambda: started,
                id_factory=lambda: f"episode-{worker_id}",
            )
            trip_barrier.wait()
            with connection:
                return service.trip(
                    signal,
                    source_owner_is_valid=True,
                ).outcome
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        trip_outcomes = tuple(executor.map(trip_from_connection, (1, 2)))

    assert sorted(outcome.value for outcome in trip_outcomes) == ["opened", "repeated"]

    with SqliteApplicationContext(db_path) as app:
        service = _deterministic_service(app, started)
        opened = service.get("race-scope")
        assert opened is not None
        request = service.request_probe(
            "race-scope",
            target_id=target.id,
            requested_at=opened.cooldown_until,
        )
        request_id = request.state.probe_request_id

    claim_barrier = Barrier(2)

    def claim_from_connection(worker_id: int) -> FacebookHalfOpenClaimOutcome:
        connection = _open_connection(db_path)
        try:
            service = FacebookAccessCircuitService(
                FacebookAccessCircuitRepository(connection),
                TargetRepository(connection),
                clock=lambda: started + timedelta(hours=12),
                id_factory=lambda: f"half-open-{worker_id}",
            )
            claim_barrier.wait()
            with connection:
                return service.claim_half_open(
                    "race-scope",
                    request_id=request_id,
                    started_at=started + timedelta(hours=12),
                ).outcome
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        claim_outcomes = tuple(executor.map(claim_from_connection, (1, 2)))

    assert claim_outcomes.count(FacebookHalfOpenClaimOutcome.CLAIMED) == 1
    with SqliteApplicationContext(db_path) as app:
        state = app.services.facebook_access_circuit.get("race-scope")
        assert state is not None
        assert state.status == FacebookAccessCircuitStatus.HALF_OPEN
        assert state.generation == 2


def _deterministic_service(
    app: ApplicationContext,
    now: datetime,
) -> FacebookAccessCircuitService:
    """建立固定 clock 與可預測 UUID sequence 的 service。"""

    sequence = count(1)
    return FacebookAccessCircuitService(
        app.repositories.facebook_access_circuit,
        app.repositories.targets,
        clock=lambda: now,
        id_factory=lambda: f"id-{next(sequence)}",
    )


def _block_signal(
    token: FacebookAdmissionToken,
    target_id: str,
    *,
    operation_kind: FacebookProductOperationKind = (FacebookProductOperationKind.POSTS_ACCESS),
) -> FacebookAccessBlockSignal:
    """建立不含 URL/page body 的 high-confidence typed signal。"""

    return FacebookAccessBlockSignal(
        admission_token=token,
        source_kind=FacebookWorkSourceKind.SCAN,
        operation_kind=operation_kind,
        trigger_action_kind=FacebookActionKind.DIRECT_DOCUMENT,
        source_owner_token="scan-owner",
        target_id=target_id,
        evidence_code="facebook_page_guard_v1",
        confidence=FacebookAccessSignalConfidence.HIGH,
    )


def _open_connection(db_path: Path) -> sqlite3.Connection:
    """建立 concurrency test 使用的獨立 SQLite connection。"""

    connection = sqlite3.connect(db_path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
