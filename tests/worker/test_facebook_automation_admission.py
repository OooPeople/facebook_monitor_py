from __future__ import annotations

import asyncio
from datetime import UTC
from datetime import datetime
from pathlib import Path
from uuid import UUID

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
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationWorkKind,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationRestartGuardOutcome,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardRuntime,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardStore,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    reconcile_facebook_automation_restart_guard,
)


_NOW = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)


def _controller(
    db_path: Path,
    gate: FacebookAccessRuntimeGate,
) -> FacebookAutomationAdmissionController:
    """建立零等待測試用 admission controller。"""

    return FacebookAutomationAdmissionController(
        db_path=db_path,
        profile_scope_key="profile-scope-test",
        runtime_gate=gate,
        coordinator=FacebookAutomationCoordinator(
            quiet_gap_min_seconds=0,
            quiet_gap_max_seconds=0,
        ),
        persistent_quiet_gap_seconds=0,
    )


def test_admission_rechecks_process_epoch_after_coordinator() -> None:
    """trip latch 推進 epoch 後，既有 token 不得通過 visible-write fence。"""

    gate = FacebookAccessRuntimeGate()
    with SqliteApplicationContext(Path(":memory:")) as app:
        decision = app.services.facebook_access_circuit.admit_normal(
            "profile-scope-test",
            process_safety_epoch=gate.current_safety_epoch(),
            operation_id="operation-1",
        )
        assert decision.token is not None
        assert gate.admission_is_process_current(decision.token)
        assert gate.request_trip(decision.token)
        assert not gate.admission_is_process_current(decision.token)
        snapshot = gate.snapshot()
        assert snapshot.trip_requested
        assert snapshot.writes_closed
        assert snapshot.safety_epoch == 1


def test_browser_io_poison_cannot_be_reset_by_closed_circuit() -> None:
    """Cleanup不確定是same-process fatal，普通circuit reset不得重開。"""

    gate = FacebookAccessRuntimeGate()
    gate.poison_browser_io()
    poisoned_epoch = gate.current_safety_epoch()

    gate.reset_after_verified_closed_circuit()

    snapshot = gate.snapshot()
    assert snapshot.browser_io_poisoned
    assert snapshot.writes_closed
    assert snapshot.safety_epoch == poisoned_epoch + 1


def test_closed_circuit_admits_and_transfers_process_lease(tmp_path: Path) -> None:
    """closed circuit 應在 coordinator 後核發含 DB token 的 governed lease。"""

    db_path = tmp_path / "app.db"
    gate = FacebookAccessRuntimeGate()

    async def scenario() -> None:
        result = await _controller(db_path, gate).acquire(
            work_kind=FacebookAutomationWorkKind.TARGET_SCAN,
            operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
            owner_alias="target-scan",
        )
        assert result.admitted
        assert result.lease is not None
        assert result.lease.operation_kind == FacebookProductOperationKind.POSTS_ACCESS
        assert gate.admission_is_process_current(result.lease.admission_token)
        await result.lease.release()

    asyncio.run(scenario())


def test_crash_mid_lease_reconciles_marker_and_pacing_with_same_owner(
    tmp_path: Path,
) -> None:
    """Browser session崩潰時marker/pacing共用UUID，restart不得誤判owner mismatch。"""

    db_path = tmp_path / "app.db"
    store = FacebookAutomationSessionGuardStore(
        tmp_path / "guards",
        profile_alias="profile-test",
    )
    controller = FacebookAutomationAdmissionController(
        db_path=db_path,
        profile_scope_key="profile-scope-test",
        runtime_gate=FacebookAccessRuntimeGate(),
        coordinator=FacebookAutomationCoordinator(
            quiet_gap_min_seconds=0,
            quiet_gap_max_seconds=0,
        ),
        persistent_quiet_gap_seconds=0,
        clock=lambda: _NOW,
        session_guard_runtime=FacebookAutomationSessionGuardRuntime(store),
    )

    async def scenario() -> None:
        owner_session_id = controller.rotate_owner_session_id()
        assert str(UUID(owner_session_id)) == owner_session_id
        assert controller.start_session_guard_before_browser_io() == owner_session_id
        admitted = await controller.acquire(
            work_kind=FacebookAutomationWorkKind.TARGET_SCAN,
            operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
        )
        assert admitted.lease is not None
        marker = store.read()
        assert marker is not None
        assert marker.session_id == owner_session_id
        assert admitted.lease.pacing_token.owner_session_id == owner_session_id

        restarted = reconcile_facebook_automation_restart_guard(
            db_path=db_path,
            store=store,
            profile_scope_key="profile-scope-test",
            reconciled_at=_NOW,
        )
        assert restarted.outcome == (
            FacebookAutomationRestartGuardOutcome.UNCLEAN_SESSION_HOLD
        )
        assert restarted.session_recovery is not None
        with SqliteApplicationContext(db_path) as app:
            pacing = app.repositories.facebook_automation_pacing.get(
                "profile-scope-test"
            )
        assert pacing is not None
        assert pacing.active_operation_id == ""
        await admitted.lease.release()

    asyncio.run(scenario())


def test_open_circuit_defers_without_acquiring_process_work(tmp_path: Path) -> None:
    """persistent open circuit 必須在任何 process work lease 前 fail closed。"""

    db_path = tmp_path / "app.db"
    gate = FacebookAccessRuntimeGate()
    controller = _controller(db_path, gate)

    async def scenario() -> None:
        admitted = await controller.acquire(
            work_kind=FacebookAutomationWorkKind.TARGET_SCAN,
            operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
        )
        assert admitted.lease is not None
        token = admitted.lease.admission_token
        with SqliteApplicationContext(db_path) as app:
            trip_result = app.services.facebook_access_circuit.trip(
                FacebookAccessBlockSignal(
                    admission_token=token,
                    source_kind=FacebookWorkSourceKind.SCAN,
                    operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
                    trigger_action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
                    source_owner_token="owner-1",
                ),
                source_owner_is_valid=True,
            )
            assert trip_result.state.status.value == "open"
        await admitted.lease.release()

        denied = await controller.acquire(
            work_kind=FacebookAutomationWorkKind.METADATA_REFRESH,
            operation_kind=FacebookProductOperationKind.GROUP_METADATA_ACCESS,
        )
        assert not denied.admitted
        assert denied.reason == "deferred_breaker"
        coordinator_snapshot = await controller.coordinator.snapshot()
        assert coordinator_snapshot.admitted_count == 1

    asyncio.run(scenario())


def test_second_work_waits_on_process_coordinator_before_persistent_lease(
    tmp_path: Path,
) -> None:
    """同 process work 不可因先看見 active pacing lease 而睡到 lease timeout。"""

    db_path = tmp_path / "app.db"
    gate = FacebookAccessRuntimeGate()
    controller = _controller(db_path, gate)

    async def scenario() -> None:
        first = await controller.acquire(
            work_kind=FacebookAutomationWorkKind.TARGET_SCAN,
            operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
        )
        assert first.lease is not None
        second_task = asyncio.create_task(
            controller.acquire(
                work_kind=FacebookAutomationWorkKind.METADATA_REFRESH,
                operation_kind=FacebookProductOperationKind.GROUP_METADATA_ACCESS,
            )
        )
        await asyncio.sleep(0)
        assert not second_task.done()
        await first.lease.release()
        second = await asyncio.wait_for(second_task, timeout=1)
        assert second.lease is not None
        await second.lease.release()

    asyncio.run(scenario())


def test_runtime_cancellation_defers_waiting_admission_and_allows_next_generation(
    tmp_path: Path,
) -> None:
    """stop/restart 取消不得轉成 scan failure，也不可鎖死下一個 runtime。"""

    db_path = tmp_path / "app.db"
    controller = _controller(db_path, FacebookAccessRuntimeGate())

    async def scenario() -> None:
        first = await controller.acquire(
            work_kind=FacebookAutomationWorkKind.TARGET_SCAN,
            operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
        )
        assert first.lease is not None
        waiter = asyncio.create_task(
            controller.acquire(
                work_kind=FacebookAutomationWorkKind.METADATA_REFRESH,
                operation_kind=FacebookProductOperationKind.GROUP_METADATA_ACCESS,
            )
        )
        await asyncio.sleep(0)

        controller.coordinator.cancel_pending_waiters()
        cancelled = await waiter
        assert not cancelled.admitted
        assert cancelled.reason == "cancelled_runtime"
        await first.lease.release()

        next_result = await controller.acquire(
            work_kind=FacebookAutomationWorkKind.COVER_REFRESH,
            operation_kind=FacebookProductOperationKind.COVER_METADATA_ACCESS,
        )
        assert next_result.lease is not None
        await next_result.lease.release()

    asyncio.run(scenario())
