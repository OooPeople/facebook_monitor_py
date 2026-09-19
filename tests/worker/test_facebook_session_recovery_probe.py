"""Stale normal-session recovery outer-supervisor tests。"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

import facebook_monitor.worker.facebook_session_recovery_probe as recovery_probe_module
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.facebook_access import FacebookAccessCircuitStatus
from facebook_monitor.core.facebook_access import FacebookProbeFailureStage
from facebook_monitor.core.facebook_access import FacebookProbeResult
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookRecoveryRecipeKind
from facebook_monitor.core.facebook_session_recovery import FacebookSessionRecoveryStatus
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.core.scan_failures import SCAN_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import SESSION_INVALID_REASON
from facebook_monitor.persistence.schema import CURRENT_SCHEMA_TABLES
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.facebook_access_runtime_gate import FacebookAccessRuntimeGate
from facebook_monitor.worker.facebook_automation_admission import (
    FacebookAutomationAdmissionController,
)
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationCoordinator,
)
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationCoordinatorSnapshot,
)
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationWorkKind,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationRestartGuardOutcome,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardStore,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    derive_facebook_automation_profile_alias,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    reconcile_facebook_automation_restart_guard,
)
from facebook_monitor.worker.facebook_session_recovery_probe import (
    FacebookSessionRecoveryProbeExecutionResult,
)
from facebook_monitor.worker.facebook_session_recovery_probe import (
    FacebookSessionRecoveryProbeOutcome,
)
from facebook_monitor.worker.facebook_session_recovery_probe import (
    consume_pending_facebook_session_recovery_probe,
)
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions

from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext


_NOW = datetime(2026, 9, 19, 3, 0, tzinfo=UTC)
_SCOPE = "opaque-session-recovery-scope"
_MARKER_ID = "11111111-1111-4111-8111-111111111111"
_SAFETY_MUTATION_TABLES = frozenset(
    {
        "dashboard_revision",
        "facebook_access_circuit_events",
        "facebook_access_circuit_state",
        "facebook_automation_pacing_state",
        "facebook_session_recovery_state",
    }
)
_PRODUCT_TABLES = tuple(
    table for table in CURRENT_SCHEMA_TABLES if table not in _SAFETY_MUTATION_TABLES
)


@pytest.mark.parametrize(
    ("failure_reason", "expected_outcome", "expected_recovery", "needs_login"),
    [
        ("", FacebookSessionRecoveryProbeOutcome.SUCCEEDED, "success", False),
        (
            FACEBOOK_TEMPORARY_BLOCK_REASON,
            FacebookSessionRecoveryProbeOutcome.BLOCKED,
            "blocked",
            False,
        ),
        (
            SESSION_INVALID_REASON,
            FacebookSessionRecoveryProbeOutcome.INCONCLUSIVE,
            "inconclusive",
            True,
        ),
    ],
)
def test_stale_session_recovery_probe_preserves_product_state_and_durable_outcome(
    failure_reason: str,
    expected_outcome: FacebookSessionRecoveryProbeOutcome,
    expected_recovery: str,
    needs_login: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Crash hold只能由單一bounded probe收斂，且不得寫產品掃描狀態。"""

    setup = _seed_pending_recovery(tmp_path)
    context = FakeAsyncBrowserContext()
    events: list[str] = []

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    @contextmanager
    def checked_profile_lease(*_args: object, **_kwargs: object):
        events.append("profile")
        yield object()

    async def fake_launch(*_args: object, **_kwargs: object) -> FakeAsyncBrowserContext:
        events.append("browser")
        with SqliteApplicationContext(setup.options.db_path) as app:
            pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        marker = setup.store.read()
        assert pacing is not None
        assert marker is not None
        assert pacing.owner_session_id == marker.session_id == _MARKER_ID
        return context

    async def probe_guard(_page: object) -> None:
        if failure_reason:
            raise WorkerFailure(failure_reason, "bounded test failure")

    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_session_recovery_probe.acquire_profile_lease",
        checked_profile_lease,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_session_recovery_probe.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_session_recovery_probe."
        "launch_persistent_context_async",
        fake_launch,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_session_recovery_probe."
        "ensure_async_page_scannable",
        probe_guard,
    )

    gate = FacebookAccessRuntimeGate()
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller = FacebookAutomationAdmissionController(
        db_path=setup.options.db_path,
        profile_scope_key=_SCOPE,
        runtime_gate=gate,
        coordinator=coordinator,
        persistent_quiet_gap_seconds=30,
        clock=lambda: _NOW + timedelta(seconds=31),
    )
    result = asyncio.run(
        consume_pending_facebook_session_recovery_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,
            admission_controller=controller,
            runtime_gate=gate,
            session_guard_store=setup.store,
            should_stop=lambda: False,
            clock=lambda: _NOW + timedelta(seconds=31),
        )
    )

    assert result.outcome == expected_outcome
    assert result.claimed and result.browser_launched
    assert result.page_count == 1
    assert result.document_navigation_count == 1
    assert result.context_closed and context.closed
    assert events == ["profile", "browser"]
    with SqliteApplicationContext(setup.options.db_path) as app:
        recovery = app.services.facebook_session_recovery.get(_SCOPE)
        circuit = app.services.facebook_access_circuit.get(_SCOPE)
        session = app.repositories.app_settings.get_profile_session_status()
        target = app.repositories.targets.get(setup.target_id)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert recovery is not None
    assert recovery.last_probe_result.value == expected_recovery
    assert target == setup.target_before
    assert product_state == setup.product_state_before
    assert session.needs_login is needs_login

    if expected_outcome == FacebookSessionRecoveryProbeOutcome.SUCCEEDED:
        assert recovery.status == FacebookSessionRecoveryStatus.RECOVERED
        assert setup.store.read() is None
        assert result.marker_cleared
        assert circuit is None
    elif expected_outcome == FacebookSessionRecoveryProbeOutcome.BLOCKED:
        assert recovery.status == FacebookSessionRecoveryStatus.HOLD
        assert setup.store.read() is None
        assert result.marker_cleared
        assert circuit is not None
        assert circuit.status == FacebookAccessCircuitStatus.OPEN
        assert gate.snapshot().writes_closed
    else:
        assert recovery.status == FacebookSessionRecoveryStatus.HOLD
        assert setup.store.read() is not None
        assert not result.marker_cleared
        assert circuit is None
        assert session.reason == SESSION_INVALID_REASON
        assert session.source == "facebook_session_recovery_probe"


def test_session_block_survives_cancellation_during_coordinator_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """BLOCKED線性化後的晚到cancel只重拋，不降級durable與pacing結果。"""

    setup = _seed_pending_recovery(tmp_path)
    coordinator, controller, gate = _runtime_dependencies(setup)
    release_observed = False

    async def observe_release_order(**kwargs: Any) -> None:
        nonlocal release_observed
        claimed = kwargs["claimed"]
        execution = kwargs["execution"]
        inner = await coordinator.acquire(FacebookAutomationWorkKind.HALF_OPEN_PROBE)
        pacing = controller.try_acquire_half_open_probe_pacing(
            operation_id=inner.operation_id,
            owner_session_id=_MARKER_ID,
            started_at=_NOW + timedelta(seconds=31),
        )
        assert pacing.token is not None
        execution.pacing_token = pacing.token

        class InspectingLease:
            operation_id = inner.operation_id

            async def release(self) -> None:
                nonlocal release_observed
                with SqliteApplicationContext(setup.options.db_path) as app:
                    recovery = app.services.facebook_session_recovery.get(_SCOPE)
                    circuit = app.services.facebook_access_circuit.get(_SCOPE)
                assert recovery is not None
                assert recovery.last_probe_result == FacebookProbeResult.BLOCKED
                assert circuit is not None
                assert circuit.status == FacebookAccessCircuitStatus.OPEN
                assert gate.snapshot().writes_closed
                release_observed = True
                owner = asyncio.current_task()
                assert owner is not None
                asyncio.get_running_loop().call_soon(owner.cancel)
                await inner.release()

        setup.store.mark_trip_pending(
            session_id=_MARKER_ID,
            operation_kind=claimed.recipe.operation_kind,
            trigger_action_kind=claimed.recipe.action_kind,
        )
        execution.process_lease = InspectingLease()
        execution.observed_result = FacebookProbeResult.BLOCKED
        execution.marker_trip_pending = True
        execution.context_closed = True

    monkeypatch.setattr(
        recovery_probe_module,
        "_run_claimed_probe_resources",
        observe_release_order,
    )

    async def run() -> FacebookAutomationCoordinatorSnapshot:
        with pytest.raises(asyncio.CancelledError):
            await consume_pending_facebook_session_recovery_probe(
                options=setup.options,
                profile_scope_key=_SCOPE,
                coordinator=coordinator,
                admission_controller=controller,
                runtime_gate=gate,
                session_guard_store=setup.store,
                should_stop=lambda: False,
                clock=lambda: _NOW + timedelta(seconds=31),
            )
        return await coordinator.snapshot()

    snapshot = asyncio.run(run())

    assert release_observed
    assert not snapshot.active and snapshot.waiter_count == 0
    assert setup.store.read() is None
    with SqliteApplicationContext(setup.options.db_path) as app:
        recovery = app.services.facebook_session_recovery.get(_SCOPE)
        circuit = app.services.facebook_access_circuit.get(_SCOPE)
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert recovery is not None
    assert recovery.status == FacebookSessionRecoveryStatus.HOLD
    assert recovery.last_probe_result == FacebookProbeResult.BLOCKED
    assert circuit is not None and circuit.status == FacebookAccessCircuitStatus.OPEN
    assert pacing is not None and pacing.active_operation_id == ""
    assert pacing.last_outcome == FacebookProbeResult.BLOCKED.value
    assert product_state == setup.product_state_before


def test_session_recovery_revalidates_target_after_leases_before_browser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Claim後target停用時在browser launch前owner-aware回到hold。"""

    setup = _seed_pending_recovery(tmp_path)
    launched = False

    async def fake_launch(*_args: object, **_kwargs: object) -> object:
        nonlocal launched
        launched = True
        raise AssertionError("invalid target must not launch browser")

    @contextmanager
    def pausing_profile_lease(*_args: object, **_kwargs: object):
        with SqliteApplicationContext(setup.options.db_path) as app:
            app.services.targets.pause_target_monitoring(setup.target_id)
        yield object()

    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_session_recovery_probe.acquire_profile_lease",
        pausing_profile_lease,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_session_recovery_probe."
        "launch_persistent_context_async",
        fake_launch,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    gate = FacebookAccessRuntimeGate()
    controller = FacebookAutomationAdmissionController(
        db_path=setup.options.db_path,
        profile_scope_key=_SCOPE,
        runtime_gate=gate,
        coordinator=coordinator,
        persistent_quiet_gap_seconds=30,
        clock=lambda: _NOW + timedelta(seconds=31),
    )

    result = asyncio.run(
        consume_pending_facebook_session_recovery_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,
            admission_controller=controller,
            runtime_gate=gate,
            session_guard_store=setup.store,
            should_stop=lambda: False,
            clock=lambda: _NOW + timedelta(seconds=31),
        )
    )

    assert result.outcome == FacebookSessionRecoveryProbeOutcome.INCONCLUSIVE
    assert result.claimed and not result.browser_launched
    assert not launched
    assert setup.store.read() is not None
    with SqliteApplicationContext(setup.options.db_path) as app:
        recovery = app.services.facebook_session_recovery.get(_SCOPE)
        target = app.repositories.targets.get(setup.target_id)
    assert recovery is not None
    assert recovery.status == FacebookSessionRecoveryStatus.HOLD
    assert recovery.last_probe_result == FacebookProbeResult.INCONCLUSIVE
    assert target is not None and target.enabled and target.paused


def test_session_recovery_context_close_failure_keeps_hold_and_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Probe內容成功但context close失敗時不得清marker或解除hold。"""

    setup = _seed_pending_recovery(tmp_path)

    class CloseFailsContext(FakeAsyncBrowserContext):
        async def close(self) -> None:
            raise RuntimeError("context close failed")

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    context = CloseFailsContext()

    @contextmanager
    def profile_lease(*_args: object, **_kwargs: object):
        yield object()

    async def probe_guard(_page: object) -> None:
        return None

    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_session_recovery_probe.acquire_profile_lease",
        profile_lease,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_session_recovery_probe.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_session_recovery_probe."
        "launch_persistent_context_async",
        lambda *_args, **_kwargs: _async_value(context),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_session_recovery_probe."
        "ensure_async_page_scannable",
        probe_guard,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    gate = FacebookAccessRuntimeGate()
    controller = FacebookAutomationAdmissionController(
        db_path=setup.options.db_path,
        profile_scope_key=_SCOPE,
        runtime_gate=gate,
        coordinator=coordinator,
        persistent_quiet_gap_seconds=30,
        clock=lambda: _NOW + timedelta(seconds=31),
    )

    result = asyncio.run(
        consume_pending_facebook_session_recovery_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,
            admission_controller=controller,
            runtime_gate=gate,
            session_guard_store=setup.store,
            should_stop=lambda: False,
            clock=lambda: _NOW + timedelta(seconds=31),
        )
    )

    assert result.outcome == FacebookSessionRecoveryProbeOutcome.INCONCLUSIVE
    assert result.claimed and result.browser_launched
    assert not result.context_closed
    assert not result.marker_cleared
    assert result.failure_reason == SCHEDULER_RUNTIME_REASON
    assert result.failure_stage == FacebookProbeFailureStage.CONTEXT_CLOSE
    assert gate.snapshot().browser_io_poisoned
    assert setup.store.read() is not None
    with SqliteApplicationContext(setup.options.db_path) as app:
        recovery = app.services.facebook_session_recovery.get(_SCOPE)
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert recovery is not None
    assert recovery.status == FacebookSessionRecoveryStatus.HOLD
    assert recovery.last_probe_result == FacebookProbeResult.INCONCLUSIVE
    assert pacing is not None and pacing.active_operation_id == ""
    assert product_state == setup.product_state_before


def test_poisoned_runtime_keeps_next_session_probe_pending_without_browser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """前一次cleanup未確認收旂後，同process不得claim或重開browser。"""

    setup = _seed_pending_recovery(tmp_path)
    coordinator, controller, gate = _runtime_dependencies(setup)
    gate.poison_browser_io()
    monkeypatch.setattr(
        recovery_probe_module,
        "async_playwright",
        lambda: pytest.fail("poisoned runtime must not start Playwright"),
    )

    result = asyncio.run(
        consume_pending_facebook_session_recovery_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,
            admission_controller=controller,
            runtime_gate=gate,
            session_guard_store=setup.store,
            should_stop=lambda: False,
            clock=lambda: _NOW + timedelta(seconds=31),
        )
    )

    assert result.outcome == FacebookSessionRecoveryProbeOutcome.INCONCLUSIVE
    assert not result.claimed and not result.browser_launched
    assert result.failure_reason == SCHEDULER_RUNTIME_REASON
    with SqliteApplicationContext(setup.options.db_path) as app:
        recovery = app.services.facebook_session_recovery.get(_SCOPE)
    assert recovery is not None
    assert recovery.status == FacebookSessionRecoveryStatus.PROBE_PENDING


def test_session_probe_cancel_during_process_release_does_not_strand_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Release階段cancel仍完成coordinator與durable recovery owner。"""

    setup = _seed_pending_recovery(tmp_path)
    coordinator, controller, gate = _runtime_dependencies(setup)

    async def acquire_then_cancel_on_release(**kwargs: Any) -> None:
        inner = await coordinator.acquire(FacebookAutomationWorkKind.HALF_OPEN_PROBE)
        pacing = controller.try_acquire_half_open_probe_pacing(
            operation_id=inner.operation_id,
            owner_session_id=_MARKER_ID,
            started_at=_NOW + timedelta(seconds=31),
        )
        assert pacing.token is not None
        kwargs["execution"].pacing_token = pacing.token

        class CancelOnReleaseLease:
            async def release(self) -> None:
                owner = asyncio.current_task()
                assert owner is not None
                asyncio.get_running_loop().call_soon(owner.cancel)
                await inner.release()

        kwargs["execution"].process_lease = CancelOnReleaseLease()

    monkeypatch.setattr(
        recovery_probe_module,
        "_run_claimed_probe_resources",
        acquire_then_cancel_on_release,
    )

    async def run() -> FacebookAutomationCoordinatorSnapshot:
        task = asyncio.create_task(
            consume_pending_facebook_session_recovery_probe(
                options=setup.options,
                profile_scope_key=_SCOPE,
                coordinator=coordinator,
                admission_controller=controller,
                runtime_gate=gate,
                session_guard_store=setup.store,
                should_stop=lambda: False,
                clock=lambda: _NOW + timedelta(seconds=31),
            )
        )
        with pytest.raises(asyncio.CancelledError):
            await task
        return await coordinator.snapshot()

    snapshot = asyncio.run(run())

    with SqliteApplicationContext(setup.options.db_path) as app:
        recovery = app.services.facebook_session_recovery.get(_SCOPE)
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert recovery is not None
    assert recovery.status == FacebookSessionRecoveryStatus.HOLD
    assert recovery.last_probe_result == FacebookProbeResult.CANCELLED
    assert pacing is not None
    assert pacing.last_outcome == FacebookProbeResult.CANCELLED.value
    assert product_state == setup.product_state_before
    assert not snapshot.active and snapshot.waiter_count == 0


def test_session_probe_cancel_during_stop_monitor_join_finishes_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Stop-monitor join中被cancel仍不得留下PROBING owner。"""

    setup = _seed_pending_recovery(tmp_path)
    coordinator, controller, gate = _runtime_dependencies(setup)
    owner_task: asyncio.Task[None] | None = None

    async def no_resources(**kwargs: Any) -> None:
        pacing = controller.try_acquire_half_open_probe_pacing(
            operation_id="session-stop-monitor-cancel",
            owner_session_id=_MARKER_ID,
            started_at=_NOW + timedelta(seconds=31),
        )
        assert pacing.token is not None
        kwargs["execution"].pacing_token = pacing.token
        await asyncio.sleep(0)

    async def cancel_owner_when_monitor_stops(**_kwargs: Any) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            assert owner_task is not None
            asyncio.get_running_loop().call_soon(owner_task.cancel)
            raise

    monkeypatch.setattr(
        recovery_probe_module,
        "_run_claimed_probe_resources",
        no_resources,
    )
    monkeypatch.setattr(
        recovery_probe_module,
        "_cancel_coordinator_waiters_when_stopping",
        cancel_owner_when_monitor_stops,
    )

    async def run() -> None:
        nonlocal owner_task
        owner_task = asyncio.current_task()
        with pytest.raises(asyncio.CancelledError):
            await consume_pending_facebook_session_recovery_probe(
                options=setup.options,
                profile_scope_key=_SCOPE,
                coordinator=coordinator,
                admission_controller=controller,
                runtime_gate=gate,
                session_guard_store=setup.store,
                should_stop=lambda: False,
                clock=lambda: _NOW + timedelta(seconds=31),
            )

    asyncio.run(run())

    with SqliteApplicationContext(setup.options.db_path) as app:
        recovery = app.services.facebook_session_recovery.get(_SCOPE)
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert recovery is not None
    assert recovery.status == FacebookSessionRecoveryStatus.HOLD
    assert recovery.last_probe_result == FacebookProbeResult.CANCELLED
    assert pacing is not None
    assert pacing.last_outcome == FacebookProbeResult.CANCELLED.value
    assert product_state == setup.product_state_before


def test_session_recovery_launch_failure_is_safely_classified(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Browser generic exception只留下固定stage/reason，不記錄例外內容。"""

    setup = _seed_pending_recovery(tmp_path)

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def fail_launch(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("secret session recovery launch detail")

    monkeypatch.setattr(
        recovery_probe_module,
        "acquire_profile_lease",
        lambda *_args, **_kwargs: _null_context(),
    )
    monkeypatch.setattr(
        recovery_probe_module,
        "async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        recovery_probe_module,
        "launch_persistent_context_async",
        fail_launch,
    )
    coordinator, controller, gate = _runtime_dependencies(setup)

    result = asyncio.run(
        consume_pending_facebook_session_recovery_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,
            admission_controller=controller,
            runtime_gate=gate,
            session_guard_store=setup.store,
            should_stop=lambda: False,
            clock=lambda: _NOW + timedelta(seconds=31),
        )
    )

    assert result.outcome == FacebookSessionRecoveryProbeOutcome.INCONCLUSIVE
    assert result.failure_reason == SCHEDULER_RUNTIME_REASON
    assert result.failure_stage == FacebookProbeFailureStage.BROWSER_LAUNCH
    assert "secret session recovery launch detail" not in caplog.text
    assert (
        "facebook_probe_failure probe=session_recovery stage=browser_launch "
        "reason=scheduler_runtime"
    ) in caplog.text
    assert setup.store.read() is not None


def test_session_recovery_absolute_deadline_is_bounded_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Deadline耗盡會回HOLD/inconclusive並保留stale-session marker。"""

    setup = _seed_pending_recovery(tmp_path)
    context = FakeAsyncBrowserContext()

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def hanging_guard(_page: object) -> None:
        await asyncio.Event().wait()

    recipe_kind = FacebookRecoveryRecipeKind.GROUP_FEED_DOCUMENT_GUARD_V1
    monkeypatch.setitem(
        recovery_probe_module._APPROVED_RECIPES,
        recipe_kind,
        replace(
            recovery_probe_module._APPROVED_RECIPES[recipe_kind],
            absolute_deadline_seconds=0.01,
            cleanup_grace_seconds=0.1,
        ),
    )
    monkeypatch.setattr(
        recovery_probe_module,
        "acquire_profile_lease",
        lambda *_args, **_kwargs: _null_context(),
    )
    monkeypatch.setattr(
        recovery_probe_module,
        "async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        recovery_probe_module,
        "launch_persistent_context_async",
        lambda *_args, **_kwargs: _async_value(context),
    )
    monkeypatch.setattr(
        recovery_probe_module,
        "ensure_async_page_scannable",
        hanging_guard,
    )
    coordinator, controller, gate = _runtime_dependencies(setup)

    result = asyncio.run(
        asyncio.wait_for(
            consume_pending_facebook_session_recovery_probe(
                options=setup.options,
                profile_scope_key=_SCOPE,
                coordinator=coordinator,
                admission_controller=controller,
                runtime_gate=gate,
                session_guard_store=setup.store,
                should_stop=lambda: False,
                clock=lambda: _NOW + timedelta(seconds=31),
            ),
            timeout=1,
        )
    )

    assert result.outcome == FacebookSessionRecoveryProbeOutcome.INCONCLUSIVE
    assert result.failure_reason == SCAN_TIMEOUT_REASON
    assert result.failure_stage == FacebookProbeFailureStage.DEADLINE
    assert result.browser_launched and result.context_closed and context.closed
    assert setup.store.read() is not None
    with SqliteApplicationContext(setup.options.db_path) as app:
        recovery = app.services.facebook_session_recovery.get(_SCOPE)
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert recovery is not None
    assert recovery.status == FacebookSessionRecoveryStatus.HOLD
    assert recovery.last_probe_result == FacebookProbeResult.INCONCLUSIVE
    assert pacing is not None and pacing.active_operation_id == ""
    assert product_state == setup.product_state_before


def test_session_recovery_task_cancel_during_browser_cleans_up_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """真實task cancellation先完成context/owner cleanup再向supervisor傳遞。"""

    setup = _seed_pending_recovery(tmp_path)
    context = FakeAsyncBrowserContext()
    profile_exited = False
    coordinator, controller, gate = _runtime_dependencies(setup)

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    @contextmanager
    def profile_lease(*_args: object, **_kwargs: object):
        nonlocal profile_exited
        try:
            yield object()
        finally:
            profile_exited = True

    async def run_cancel() -> FacebookAutomationCoordinatorSnapshot:
        entered = asyncio.Event()

        async def hanging_guard(_page: object) -> None:
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(
            recovery_probe_module,
            "ensure_async_page_scannable",
            hanging_guard,
        )
        task = asyncio.create_task(
            consume_pending_facebook_session_recovery_probe(
                options=setup.options,
                profile_scope_key=_SCOPE,
                coordinator=coordinator,
                admission_controller=controller,
                runtime_gate=gate,
                session_guard_store=setup.store,
                should_stop=lambda: False,
                clock=lambda: _NOW + timedelta(seconds=31),
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return await coordinator.snapshot()

    monkeypatch.setattr(recovery_probe_module, "acquire_profile_lease", profile_lease)
    monkeypatch.setattr(
        recovery_probe_module,
        "async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        recovery_probe_module,
        "launch_persistent_context_async",
        lambda *_args, **_kwargs: _async_value(context),
    )

    coordinator_state = asyncio.run(run_cancel())

    assert context.closed and profile_exited
    assert not coordinator_state.active and coordinator_state.waiter_count == 0
    marker = setup.store.read()
    assert marker is not None and marker.session_id == _MARKER_ID
    with SqliteApplicationContext(setup.options.db_path) as app:
        recovery = app.services.facebook_session_recovery.get(_SCOPE)
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        circuit = app.services.facebook_access_circuit.get(_SCOPE)
        session = app.repositories.app_settings.get_profile_session_status()
        target = app.repositories.targets.get(setup.target_id)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert recovery is not None
    assert recovery.status == FacebookSessionRecoveryStatus.HOLD
    assert recovery.last_probe_result == FacebookProbeResult.CANCELLED
    assert pacing is not None and pacing.active_operation_id == ""
    assert pacing.last_outcome == FacebookProbeResult.CANCELLED.value
    assert circuit is None and not gate.snapshot().writes_closed
    assert not session.needs_login
    assert target == setup.target_before
    assert product_state == setup.product_state_before


def test_session_recovery_task_cancel_while_waiting_for_coordinator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Coordinator wait中的真實cancel也會清waiter並owner-aware回HOLD。"""

    setup = _seed_pending_recovery(tmp_path)
    coordinator, controller, gate = _runtime_dependencies(setup)
    monkeypatch.setattr(
        recovery_probe_module,
        "acquire_profile_lease",
        lambda *_args, **_kwargs: pytest.fail("profile lease must not be acquired"),
    )
    monkeypatch.setattr(
        recovery_probe_module,
        "async_playwright",
        lambda: pytest.fail("Playwright must not start"),
    )

    async def run_cancel() -> FacebookAutomationCoordinatorSnapshot:
        blocker = await coordinator.acquire(FacebookAutomationWorkKind.TARGET_SCAN)
        task = asyncio.create_task(
            consume_pending_facebook_session_recovery_probe(
                options=setup.options,
                profile_scope_key=_SCOPE,
                coordinator=coordinator,
                admission_controller=controller,
                runtime_gate=gate,
                session_guard_store=setup.store,
                should_stop=lambda: False,
                clock=lambda: _NOW + timedelta(seconds=31),
            )
        )
        for _ in range(200):
            snapshot = await coordinator.snapshot()
            if snapshot.waiter_count == 1:
                break
            await asyncio.sleep(0.001)
        else:
            raise AssertionError("session recovery did not enter coordinator wait")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await blocker.release()
        return await coordinator.snapshot()

    coordinator_state = asyncio.run(run_cancel())

    assert not coordinator_state.active and coordinator_state.waiter_count == 0
    assert setup.store.read() is not None
    with SqliteApplicationContext(setup.options.db_path) as app:
        recovery = app.services.facebook_session_recovery.get(_SCOPE)
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert recovery is not None
    assert recovery.status == FacebookSessionRecoveryStatus.HOLD
    assert recovery.last_probe_result == FacebookProbeResult.CANCELLED
    assert pacing is not None and pacing.active_operation_id == ""
    assert product_state == setup.product_state_before


def test_session_recovery_deadline_also_bounds_coordinator_wait(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Absolute deadline從claim後起算，等待process coordinator也不可無限期。"""

    setup = _seed_pending_recovery(tmp_path)
    coordinator, controller, gate = _runtime_dependencies(setup)
    recipe_kind = FacebookRecoveryRecipeKind.GROUP_FEED_DOCUMENT_GUARD_V1
    monkeypatch.setitem(
        recovery_probe_module._APPROVED_RECIPES,
        recipe_kind,
        replace(
            recovery_probe_module._APPROVED_RECIPES[recipe_kind],
            absolute_deadline_seconds=0.01,
        ),
    )
    monkeypatch.setattr(
        recovery_probe_module,
        "acquire_profile_lease",
        lambda *_args, **_kwargs: pytest.fail("profile lease must not be acquired"),
    )
    monkeypatch.setattr(
        recovery_probe_module,
        "async_playwright",
        lambda: pytest.fail("Playwright must not start"),
    )

    async def run_timeout() -> tuple[
        FacebookSessionRecoveryProbeExecutionResult,
        FacebookAutomationCoordinatorSnapshot,
    ]:
        blocker = await coordinator.acquire(FacebookAutomationWorkKind.TARGET_SCAN)
        try:
            result = await asyncio.wait_for(
                consume_pending_facebook_session_recovery_probe(
                    options=setup.options,
                    profile_scope_key=_SCOPE,
                    coordinator=coordinator,
                    admission_controller=controller,
                    runtime_gate=gate,
                    session_guard_store=setup.store,
                    should_stop=lambda: False,
                    clock=lambda: _NOW + timedelta(seconds=31),
                ),
                timeout=1,
            )
        finally:
            await blocker.release()
        return result, await coordinator.snapshot()

    result, coordinator_state = asyncio.run(run_timeout())

    assert result.outcome == FacebookSessionRecoveryProbeOutcome.INCONCLUSIVE
    assert result.failure_reason == SCAN_TIMEOUT_REASON
    assert result.failure_stage == FacebookProbeFailureStage.DEADLINE
    assert not result.browser_launched
    assert not coordinator_state.active and coordinator_state.waiter_count == 0
    assert setup.store.read() is not None
    with SqliteApplicationContext(setup.options.db_path) as app:
        recovery = app.services.facebook_session_recovery.get(_SCOPE)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert recovery is not None
    assert recovery.status == FacebookSessionRecoveryStatus.HOLD
    assert recovery.last_probe_result == FacebookProbeResult.INCONCLUSIVE
    assert product_state == setup.product_state_before


@pytest.mark.parametrize("wait_kind", ["coordinator", "pacing"])
def test_session_recovery_stop_cancels_wait_and_finishes_owner_without_browser(
    wait_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Stop在coordinator或pacing等待中會owner-aware取消且不開browser。"""

    setup = _seed_pending_recovery(tmp_path)
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    gate = FacebookAccessRuntimeGate()
    controller = FacebookAutomationAdmissionController(
        db_path=setup.options.db_path,
        profile_scope_key=_SCOPE,
        runtime_gate=gate,
        coordinator=coordinator,
        persistent_quiet_gap_seconds=30,
        clock=lambda: _NOW + timedelta(seconds=31),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_session_recovery_probe.acquire_profile_lease",
        lambda *_args, **_kwargs: pytest.fail("profile lease must not be acquired"),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_session_recovery_probe.async_playwright",
        lambda: pytest.fail("Playwright must not start"),
    )

    if wait_kind == "pacing":
        with SqliteApplicationContext(setup.options.db_path) as app:
            acquired = app.repositories.facebook_automation_pacing.try_acquire(
                _SCOPE,
                operation_id="seed-pacing",
                work_kind=FacebookAutomationWorkKind.TARGET_SCAN.value,
                owner_session_id="seed-owner",
                started_at=_NOW + timedelta(seconds=31),
                lease_expires_at=_NOW + timedelta(seconds=32),
            )
            assert acquired.token is not None
            app.repositories.facebook_automation_pacing.finish(
                acquired.token,
                finished_at=_NOW + timedelta(seconds=31),
                next_not_before=_NOW + timedelta(hours=1),
                outcome="finished",
            )

    async def run() -> FacebookSessionRecoveryProbeExecutionResult:
        blocker = None
        if wait_kind == "coordinator":
            blocker = await coordinator.acquire(FacebookAutomationWorkKind.TARGET_SCAN)
        stopping = False
        task = asyncio.create_task(
            consume_pending_facebook_session_recovery_probe(
                options=setup.options,
                profile_scope_key=_SCOPE,
                coordinator=coordinator,
                admission_controller=controller,
                runtime_gate=gate,
                session_guard_store=setup.store,
                should_stop=lambda: stopping,
                clock=lambda: _NOW + timedelta(seconds=31),
            )
        )
        for _ in range(200):
            snapshot = await coordinator.snapshot()
            with SqliteApplicationContext(setup.options.db_path) as app:
                recovery = app.services.facebook_session_recovery.get(_SCOPE)
            if (
                recovery is not None
                and recovery.status == FacebookSessionRecoveryStatus.PROBING
                and (wait_kind == "coordinator" or snapshot.active)
            ):
                break
            await asyncio.sleep(0.001)
        else:
            raise AssertionError("session recovery did not enter expected wait")
        stopping = True
        result = await asyncio.wait_for(task, timeout=1)
        if blocker is not None:
            await blocker.release()
        return result

    result = asyncio.run(run())

    assert result.outcome == FacebookSessionRecoveryProbeOutcome.CANCELLED
    assert result.claimed and not result.browser_launched
    assert setup.store.read() is not None
    with SqliteApplicationContext(setup.options.db_path) as app:
        recovery = app.services.facebook_session_recovery.get(_SCOPE)
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert recovery is not None
    assert recovery.status == FacebookSessionRecoveryStatus.HOLD
    assert recovery.last_probe_result == FacebookProbeResult.CANCELLED
    assert pacing is not None and pacing.active_operation_id == ""
    assert product_state == setup.product_state_before


class _RecoverySetup:
    def __init__(
        self,
        *,
        options: ResidentRuntimeOptions,
        store: FacebookAutomationSessionGuardStore,
        target_id: str,
        target_before: object,
        product_state_before: dict[str, tuple[tuple[Any, ...], ...]],
    ) -> None:
        self.options = options
        self.store = store
        self.target_id = target_id
        self.target_before = target_before
        self.product_state_before = product_state_before


def _seed_pending_recovery(tmp_path: Path) -> _RecoverySetup:
    """模擬上一個process在normal browser session中崩潰後由新processreconcile。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation_default"
    profile_dir.mkdir(parents=True)
    store = FacebookAutomationSessionGuardStore(
        tmp_path / "facebook-automation-session-guards",
        profile_alias=derive_facebook_automation_profile_alias(_SCOPE),
    )
    store.start_normal_session(started_at=_NOW - timedelta(minutes=1), session_id=_MARKER_ID)
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        target_before = app.repositories.targets.get(target.id)
        product_state_before = _product_state_snapshot(
            app.repositories.targets.connection
        )

    reconciled = reconcile_facebook_automation_restart_guard(
        db_path=db_path,
        store=store,
        profile_scope_key=_SCOPE,
        reconciled_at=_NOW,
    )
    assert reconciled.outcome == FacebookAutomationRestartGuardOutcome.UNCLEAN_SESSION_HOLD
    with SqliteApplicationContext(db_path) as app:
        requested = app.services.facebook_session_recovery.request_probe(
            _SCOPE,
            target_id=target.id,
            operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
            requested_at=_NOW + timedelta(seconds=30),
        )
    assert requested.state.status == FacebookSessionRecoveryStatus.PROBE_PENDING
    return _RecoverySetup(
        options=ResidentRuntimeOptions(db_path=db_path, profile_dir=profile_dir),
        store=store,
        target_id=target.id,
        target_before=target_before,
        product_state_before=product_state_before,
    )


def _product_state_snapshot(
    connection: Any,
) -> dict[str, tuple[tuple[Any, ...], ...]]:
    """快照所有非 safety-owner 資料表，同時偵測 INSERT/UPDATE/DELETE。"""

    snapshot: dict[str, tuple[tuple[Any, ...], ...]] = {}
    for table in _PRODUCT_TABLES:
        columns = tuple(
            str(row[1])
            for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        )
        order_by = ", ".join(f'"{column}"' for column in columns)
        where_clause = (
            " WHERE key <> 'profile_session_status'"
            if table == "app_settings"
            else ""
        )
        rows = connection.execute(
            f'SELECT * FROM "{table}"{where_clause} ORDER BY {order_by}'
        ).fetchall()
        snapshot[table] = tuple(tuple(row) for row in rows)
    return snapshot


@contextmanager
def _null_context():
    """提供不碰真實 profile lock 的測試 context。"""

    yield object()


def _runtime_dependencies(
    setup: _RecoverySetup,
) -> tuple[
    FacebookAutomationCoordinator,
    FacebookAutomationAdmissionController,
    FacebookAccessRuntimeGate,
]:
    """建立 session recovery probe 的共用 runtime dependencies。"""

    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    gate = FacebookAccessRuntimeGate()
    controller = FacebookAutomationAdmissionController(
        db_path=setup.options.db_path,
        profile_scope_key=_SCOPE,
        runtime_gate=gate,
        coordinator=coordinator,
        persistent_quiet_gap_seconds=30,
        clock=lambda: _NOW + timedelta(seconds=31),
    )
    return coordinator, controller, gate


async def _async_value(value: Any) -> Any:
    """回傳 async monkeypatch 需要的固定值。"""

    return value
