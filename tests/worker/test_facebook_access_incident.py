"""Facebook access 專用 incident transaction tests。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.scan_recording_service import RecordScanRequest
from facebook_monitor.application.scan_recording_service import ScanRecordingService
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.facebook_access import FacebookAccessBlockSignal
from facebook_monitor.core.facebook_access import FacebookAccessCircuitStatus
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookAdmissionToken
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookWorkSourceKind
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.worker.facebook_access_incident import (
    FacebookAccessIncidentOutcomeKind,
)
from facebook_monitor.worker.facebook_access_incident import (
    record_facebook_access_incident_for_db,
)
from facebook_monitor.worker.scan_commit_guard import ScanCommitGuard
from facebook_monitor.worker.scan_commit_guard import scan_commit_guard_from_runtime_state
from facebook_monitor.worker.scan_orchestration import FacebookPageGuardDiagnostics


_INCIDENT_AT = datetime(2026, 7, 1, 8, tzinfo=UTC)


def test_scan_incidents_share_episode_and_write_one_guarded_failure_each(
    tmp_path: Path,
) -> None:
    """同 generation scan incidents 共用 episode，且每個 valid owner 最多一筆 scan。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = _prepare_running_scan(app, "first", "worker-a", "page-a")
        second = _prepare_running_scan(app, "second", "worker-b", "page-b")
        first_token = _admit(app, "scope", "operation-a")
        second_token = _admit(app, "scope", "operation-b")

    first_signal = _signal(
        first_token,
        source_owner_token="lease-a",
        target_id=first[0].id,
    )
    second_signal = _signal(
        second_token,
        source_owner_token="lease-b",
        target_id=second[0].id,
    )
    diagnostics = _diagnostics()

    opened = record_facebook_access_incident_for_db(
        db_path=db_path,
        signal=first_signal,
        source_owner_token="lease-a",
        scan_commit_guard=first[1],
        diagnostics=diagnostics,
        occurred_at=_INCIDENT_AT,
    )
    repeated = record_facebook_access_incident_for_db(
        db_path=db_path,
        signal=second_signal,
        source_owner_token="lease-b",
        scan_commit_guard=second[1],
        diagnostics=diagnostics,
        occurred_at=_INCIDENT_AT,
    )
    duplicate = record_facebook_access_incident_for_db(
        db_path=db_path,
        signal=second_signal,
        source_owner_token="lease-b",
        scan_commit_guard=second[1],
        diagnostics=diagnostics,
        occurred_at=_INCIDENT_AT,
    )

    assert opened.kind == FacebookAccessIncidentOutcomeKind.OPENED
    assert opened.scan_run_id > 0
    assert opened.runtime_released
    assert repeated.kind == FacebookAccessIncidentOutcomeKind.REPEATED
    assert repeated.scan_run_id > 0
    assert repeated.runtime_released
    assert duplicate.kind == FacebookAccessIncidentOutcomeKind.REJECTED_SCAN_OWNER
    assert duplicate.scan_run_id == 0

    with SqliteApplicationContext(db_path) as app:
        connection = app.repositories.targets.connection
        circuit = app.services.facebook_access_circuit.get("scope")
        events = app.repositories.facebook_access_circuit.list_recent_events("scope")
        first_run = app.repositories.scan_runs.latest_by_target(first[0].id)
        second_run = app.repositories.scan_runs.latest_by_target(second[0].id)
        first_runtime = app.repositories.runtime_states.get(first[0].id)
        second_runtime = app.repositories.runtime_states.get(second[0].id)
        side_effect_counts = _product_side_effect_counts(connection)
        scan_counts = {
            row["target_id"]: int(row["count"])
            for row in connection.execute(
                """
                SELECT target_id, COUNT(*) AS count
                FROM scan_runs
                GROUP BY target_id
                """
            ).fetchall()
        }

    assert circuit is not None
    assert circuit.status == FacebookAccessCircuitStatus.OPEN
    assert circuit.generation == 1
    assert circuit.detection_count == 2
    assert len(events) == 2
    assert scan_counts == {first[0].id: 1, second[0].id: 1}
    for scan_run in (first_run, second_run):
        assert scan_run is not None
        assert scan_run.error_message.startswith("Facebook 暫時限制存取")
        assert scan_run.metadata["reason"] == FACEBOOK_TEMPORARY_BLOCK_REASON
        assert scan_run.metadata["runtime_action"] == "facebook_access_pause"
        assert scan_run.metadata["failure_diagnostics"] == diagnostics.to_safe_mapping()
        assert "https://" not in str(scan_run.metadata)
    for runtime in (first_runtime, second_runtime):
        assert runtime is not None
        assert runtime.runtime_status == TargetRuntimeStatus.IDLE
        assert runtime.active_worker_id == ""
        assert runtime.active_page_id == ""
        assert runtime.last_skip_reason == FACEBOOK_TEMPORARY_BLOCK_REASON
        assert runtime.consecutive_failure_reason == "previous_failure"
        assert runtime.consecutive_failure_count == 2
    assert side_effect_counts == {
        "latest_scan_items": 0,
        "seen_items": 0,
        "match_history": 0,
        "notification_events": 0,
        "notification_outbox": 0,
    }


def test_metadata_and_cover_only_trip_circuit_without_target_side_effects(
    tmp_path: Path,
) -> None:
    """Metadata/cover source 不得偽造 scan 或改 target runtime。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="metadata",
                canonical_url="https://www.facebook.com/groups/metadata",
            )
        )
        token = _admit(app, "metadata-scope", "metadata-operation")
        runtime_before = app.repositories.runtime_states.get(target.id)

    metadata = _signal(
        token,
        source_owner_token="metadata-owner",
        target_id=target.id,
        source_kind=FacebookWorkSourceKind.METADATA,
        operation_kind=FacebookProductOperationKind.GROUP_METADATA_ACCESS,
        action_kind=FacebookActionKind.GROUP_DOCUMENT,
    )
    cover = replace(
        metadata,
        source_kind=FacebookWorkSourceKind.COVER,
        operation_kind=FacebookProductOperationKind.COVER_METADATA_ACCESS,
        source_owner_token="cover-owner",
    )
    unsupported = replace(
        metadata,
        source_kind=FacebookWorkSourceKind.SYNC_RESOLVER,
        source_owner_token="resolver-owner",
    )

    opened = record_facebook_access_incident_for_db(
        db_path=db_path,
        signal=metadata,
        source_owner_token="metadata-owner",
        occurred_at=_INCIDENT_AT,
    )
    repeated = record_facebook_access_incident_for_db(
        db_path=db_path,
        signal=cover,
        source_owner_token="cover-owner",
        occurred_at=_INCIDENT_AT,
    )
    rejected = record_facebook_access_incident_for_db(
        db_path=db_path,
        signal=unsupported,
        source_owner_token="resolver-owner",
        occurred_at=_INCIDENT_AT,
    )

    with SqliteApplicationContext(db_path) as app:
        connection = app.repositories.targets.connection
        circuit = app.services.facebook_access_circuit.get("metadata-scope")
        runtime_after = app.repositories.runtime_states.get(target.id)
        scan_count = _count(connection, "scan_runs")
        outbox_count = _count(connection, "notification_outbox")

    assert opened.kind == FacebookAccessIncidentOutcomeKind.OPENED
    assert repeated.kind == FacebookAccessIncidentOutcomeKind.REPEATED
    assert rejected.kind == FacebookAccessIncidentOutcomeKind.REJECTED_SOURCE
    assert circuit is not None
    assert circuit.detection_count == 2
    assert runtime_after == runtime_before
    assert scan_count == 0
    assert outbox_count == 0


def test_stale_owner_guard_and_generation_have_zero_visible_writes(tmp_path: Path) -> None:
    """Owner/guard/admission 任一 stale 時不得寫 circuit、scan 或 runtime。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _prepare_running_scan(app, "stale", "worker", "page")
        token = _admit(app, "stale-scope", "operation")
        revision_before = app.repositories.dashboard_revision.get_dashboard_revision()

    signal = _signal(
        token,
        source_owner_token="lease",
        target_id=fixture[0].id,
    )
    wrong_source_owner = record_facebook_access_incident_for_db(
        db_path=db_path,
        signal=signal,
        source_owner_token="another-lease",
        scan_commit_guard=fixture[1],
        occurred_at=_INCIDENT_AT,
    )
    wrong_guard = record_facebook_access_incident_for_db(
        db_path=db_path,
        signal=signal,
        source_owner_token="lease",
        scan_commit_guard=replace(fixture[1], worker_id="another-worker"),
        occurred_at=_INCIDENT_AT,
    )
    stale_signal = replace(
        signal,
        admission_token=replace(token, db_generation=99),
    )
    stale_admission = record_facebook_access_incident_for_db(
        db_path=db_path,
        signal=stale_signal,
        source_owner_token="lease",
        scan_commit_guard=fixture[1],
        occurred_at=_INCIDENT_AT,
    )

    with SqliteApplicationContext(db_path) as app:
        connection = app.repositories.targets.connection
        circuit = app.services.facebook_access_circuit.get("stale-scope")
        runtime = app.repositories.runtime_states.get(fixture[0].id)
        revision_after = app.repositories.dashboard_revision.get_dashboard_revision()

        assert _count(connection, "facebook_access_circuit_events") == 0
        assert _count(connection, "scan_runs") == 0
        assert _count(connection, "notification_outbox") == 0

    assert wrong_source_owner.kind == (
        FacebookAccessIncidentOutcomeKind.REJECTED_SOURCE_OWNER
    )
    assert wrong_guard.kind == FacebookAccessIncidentOutcomeKind.REJECTED_SCAN_OWNER
    assert stale_admission.kind == (
        FacebookAccessIncidentOutcomeKind.REJECTED_STALE_ADMISSION
    )
    assert circuit is not None
    assert circuit.status == FacebookAccessCircuitStatus.CLOSED
    assert circuit.generation == 0
    assert runtime is not None
    assert runtime.runtime_status == TargetRuntimeStatus.RUNNING
    assert revision_after == revision_before


def test_incident_rolls_back_circuit_scan_and_runtime_together(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Scan write 後的 injected failure 必須讓整個 immediate transaction rollback。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _prepare_running_scan(app, "rollback", "worker", "page")
        token = _admit(app, "rollback-scope", "operation")

    signal = _signal(
        token,
        source_owner_token="lease",
        target_id=fixture[0].id,
    )
    original_record_scan = ScanRecordingService.record_scan

    def fail_after_scan_insert(
        self: ScanRecordingService,
        request: RecordScanRequest,
    ) -> int:
        original_record_scan(self, request)
        raise RuntimeError("injected incident failure")

    monkeypatch.setattr(ScanRecordingService, "record_scan", fail_after_scan_insert)

    with pytest.raises(RuntimeError, match="injected incident failure"):
        record_facebook_access_incident_for_db(
            db_path=db_path,
            signal=signal,
            source_owner_token="lease",
            scan_commit_guard=fixture[1],
            diagnostics=_diagnostics(),
            occurred_at=_INCIDENT_AT,
        )

    with SqliteApplicationContext(db_path) as app:
        connection = app.repositories.targets.connection
        circuit = app.services.facebook_access_circuit.get("rollback-scope")
        runtime = app.repositories.runtime_states.get(fixture[0].id)

        assert _count(connection, "facebook_access_circuit_events") == 0
        assert _count(connection, "scan_runs") == 0
        assert _count(connection, "notification_outbox") == 0

    assert circuit is not None
    assert circuit.status == FacebookAccessCircuitStatus.CLOSED
    assert circuit.generation == 0
    assert runtime is not None
    assert runtime.runtime_status == TargetRuntimeStatus.RUNNING
    assert runtime.active_worker_id == "worker"
    assert runtime.active_page_id == "page"


def _prepare_running_scan(
    app: ApplicationContext,
    group_id: str,
    worker_id: str,
    page_id: str,
) -> tuple[TargetDescriptor, ScanCommitGuard]:
    target = app.services.targets.upsert_group_posts_target(
        UpsertGroupPostsTargetRequest(
            group_id=group_id,
            canonical_url=f"https://www.facebook.com/groups/{group_id}",
        )
    )
    app.services.targets.restart_target_monitoring(target.id)
    runtime = app.repositories.runtime_states.get(target.id)
    assert runtime is not None
    app.repositories.runtime_states.save(
        replace(
            runtime,
            consecutive_failure_reason="previous_failure",
            consecutive_failure_count=2,
        )
    )
    running = app.services.targets.mark_target_running(
        target.id,
        worker_id,
        page_id=page_id,
    )
    return target, scan_commit_guard_from_runtime_state(running)


def _admit(
    app: ApplicationContext,
    scope: str,
    operation_id: str,
) -> FacebookAdmissionToken:
    admission = app.services.facebook_access_circuit.admit_normal(
        scope,
        process_safety_epoch=0,
        operation_id=operation_id,
        admitted_at=_INCIDENT_AT,
    )
    assert admission.token is not None
    return admission.token


def _signal(
    token: FacebookAdmissionToken,
    *,
    source_owner_token: str,
    target_id: str,
    source_kind: FacebookWorkSourceKind = FacebookWorkSourceKind.SCAN,
    operation_kind: FacebookProductOperationKind = (
        FacebookProductOperationKind.POSTS_ACCESS
    ),
    action_kind: FacebookActionKind = FacebookActionKind.DIRECT_DOCUMENT,
) -> FacebookAccessBlockSignal:
    return FacebookAccessBlockSignal(
        admission_token=token,
        source_kind=source_kind,
        operation_kind=operation_kind,
        trigger_action_kind=action_kind,
        source_owner_token=source_owner_token,
        target_id=target_id,
        evidence_code="facebook_page_guard_v1",
    )


def _diagnostics() -> FacebookPageGuardDiagnostics:
    return FacebookPageGuardDiagnostics(
        classification=FACEBOOK_TEMPORARY_BLOCK_REASON,
        facebook_host=True,
        matched_heading=True,
        matched_detail=True,
        article_count=0,
        stable_observation_count=2,
        body_text_length=48,
        url_kind="group_post",
    )


def _product_side_effect_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table_name: _count(connection, table_name)
        for table_name in (
            "latest_scan_items",
            "seen_items",
            "match_history",
            "notification_events",
            "notification_outbox",
        )
    }


def _count(connection: sqlite3.Connection, table_name: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()[0])
