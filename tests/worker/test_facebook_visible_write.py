from __future__ import annotations

from pathlib import Path
from threading import Event
from threading import Thread

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.facebook_access import FacebookAccessBlockSignal
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookWorkSourceKind
from facebook_monitor.worker.facebook_access_runtime_gate import FacebookAccessRuntimeGate
from facebook_monitor.worker.facebook_automation_admission import (
    FacebookAutomationAdmissionController,
)
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationCoordinator,
)
from facebook_monitor.worker.facebook_visible_write import FacebookVisibleWriteRejected
from facebook_monitor.worker.facebook_visible_write import fenced_facebook_application_context


def _controller(
    db_path: Path,
    gate: FacebookAccessRuntimeGate,
) -> FacebookAutomationAdmissionController:
    """建立不含 pacing delay 的 write-fence 測試 controller。"""

    return FacebookAutomationAdmissionController(
        db_path=db_path,
        profile_scope_key="profile-scope",
        runtime_gate=gate,
        coordinator=FacebookAutomationCoordinator(
            quiet_gap_min_seconds=0,
            quiet_gap_max_seconds=0,
        ),
        persistent_quiet_gap_seconds=0,
    )


def test_visible_write_rejects_stale_database_generation(tmp_path: Path) -> None:
    """其他 process 已 trip DB 後，舊 admission 不得開始 normal write。"""

    db_path = tmp_path / "app.db"
    gate = FacebookAccessRuntimeGate()
    controller = _controller(db_path, gate)
    with SqliteApplicationContext(db_path) as app:
        admitted = app.services.facebook_access_circuit.admit_normal(
            "profile-scope",
            process_safety_epoch=gate.current_safety_epoch(),
            operation_id="normal-work",
        )
        assert admitted.token is not None
        app.services.facebook_access_circuit.trip(
            FacebookAccessBlockSignal(
                admission_token=admitted.token,
                source_kind=FacebookWorkSourceKind.METADATA,
                operation_kind=FacebookProductOperationKind.GROUP_METADATA_ACCESS,
                trigger_action_kind=FacebookActionKind.GROUP_DOCUMENT,
                source_owner_token="metadata-owner",
            ),
            source_owner_is_valid=True,
        )

    with pytest.raises(FacebookVisibleWriteRejected):
        with fenced_facebook_application_context(
            db_path=db_path,
            controller=controller,
            token=admitted.token,
        ):
            raise AssertionError("stale write body must not execute")


def test_process_trip_waits_for_in_progress_visible_commit_fence(tmp_path: Path) -> None:
    """先取得 fence 的 writer 可完成；trip 必須在線性化點等待其退出。"""

    db_path = tmp_path / "app.db"
    gate = FacebookAccessRuntimeGate()
    controller = _controller(db_path, gate)
    with SqliteApplicationContext(db_path) as app:
        admitted = app.services.facebook_access_circuit.admit_normal(
            "profile-scope",
            process_safety_epoch=gate.current_safety_epoch(),
            operation_id="normal-work",
        )
    assert admitted.token is not None
    token = admitted.token

    trip_started = Event()
    trip_finished = Event()

    def request_trip() -> None:
        trip_started.set()
        assert gate.request_trip(token)
        trip_finished.set()

    with fenced_facebook_application_context(
        db_path=db_path,
        controller=controller,
        token=token,
    ):
        thread = Thread(target=request_trip)
        thread.start()
        assert trip_started.wait(timeout=1)
        assert not trip_finished.wait(timeout=0.05)

    thread.join(timeout=1)
    assert trip_finished.is_set()
    assert gate.snapshot().writes_closed
