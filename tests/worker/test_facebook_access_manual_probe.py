"""Manual half-open probe supervisor tests。"""

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

import facebook_monitor.worker.facebook_access_manual_probe as manual_probe_module
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.core.facebook_access import FacebookAccessBlockSignal
from facebook_monitor.core.facebook_access import FacebookAccessCircuitStatus
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookProbeFinishOutcome
from facebook_monitor.core.facebook_access import FacebookProbeFailureStage
from facebook_monitor.core.facebook_access import FacebookProbeResult
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookRecoveryRecipeKind
from facebook_monitor.core.facebook_access import FacebookWorkSourceKind
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingAcquireOutcome
from facebook_monitor.core.scan_failures import CHECKPOINT_REQUIRED_REASON
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.core.scan_failures import LOGIN_REQUIRED_REASON
from facebook_monitor.core.scan_failures import PROFILE_LOCKED_REASON
from facebook_monitor.core.scan_failures import SCAN_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import SESSION_INVALID_REASON
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.persistence.schema import CURRENT_SCHEMA_TABLES
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.facebook_access_manual_probe import (
    FacebookManualProbeExecutionResult,
)
from facebook_monitor.worker.facebook_access_manual_probe import (
    FacebookManualProbeOutcome,
)
from facebook_monitor.worker.facebook_access_manual_probe import (
    consume_pending_facebook_manual_probe,
)
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
    FacebookAutomationSessionGuardRuntime,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardStore,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    derive_facebook_automation_profile_alias,
)
from facebook_monitor.automation.profile_identity import (
    load_or_create_managed_profile_identity,
)
from facebook_monitor.worker.resident_main import run_resident_main_loop
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions

from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext
from tests.worker.resident_main_test_helpers import as_async_scan_callable
from tests.worker.resident_main_test_helpers import build_success_scan_result_for_test


_NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
_SCOPE = "opaque-probe-scope"
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
    ("operation_kind", "recipe_kind"),
    [
        (
            FacebookProductOperationKind.POSTS_ACCESS,
            FacebookRecoveryRecipeKind.GROUP_FEED_DOCUMENT_GUARD_V1,
        ),
        (
            FacebookProductOperationKind.GROUP_METADATA_ACCESS,
            FacebookRecoveryRecipeKind.GROUP_DOCUMENT_GUARD_V1,
        ),
        (
            FacebookProductOperationKind.COVER_METADATA_ACCESS,
            FacebookRecoveryRecipeKind.GROUP_COVER_GUARD_V1,
        ),
    ],
)
def test_manual_probe_claims_before_resources_and_writes_no_product_state(
    operation_kind: FacebookProductOperationKind,
    recipe_kind: FacebookRecoveryRecipeKind,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Approved recipe最多一頁/一次group document，claim先於所有browser資源。"""

    setup = _seed_pending_probe(tmp_path, operation_kind=operation_kind)
    events: list[str] = []
    coordinator = _RecordingCoordinator(
        db_path=setup.options.db_path,
        events=events,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)
    context = FakeAsyncBrowserContext()

    @contextmanager
    def checked_profile_lease(*_args: object, **_kwargs: object):
        _assert_half_open(setup.options.db_path)
        events.append("profile")
        yield object()

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def checked_launch(
        _playwright: object,
        _options: object,
    ) -> FakeAsyncBrowserContext:
        _assert_half_open(setup.options.db_path)
        events.append("browser")
        with SqliteApplicationContext(setup.options.db_path) as app:
            pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        marker = setup.guard_store.read()
        assert pacing is not None
        assert marker is not None
        assert pacing.owner_session_id == marker.session_id
        assert pacing.active_work_kind == FacebookAutomationWorkKind.HALF_OPEN_PROBE.value
        assert pacing.active_operation_id
        competing = controller.try_acquire_half_open_probe_pacing(
            operation_id="competing-probe",
            started_at=_NOW,
        )
        assert competing.outcome == FacebookPacingAcquireOutcome.ACTIVE_LEASE
        return context

    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.acquire_profile_lease",
        checked_profile_lease,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe."
        "launch_persistent_context_async",
        checked_launch,
    )

    result = asyncio.run(
        consume_pending_facebook_manual_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,
            admission_controller=controller,
            session_guard_runtime=guard_runtime,
            should_stop=lambda: False,
            clock=lambda: _NOW,
        )
    )

    assert result.outcome == FacebookManualProbeOutcome.SUCCEEDED
    assert result.claimed and result.browser_launched
    assert result.page_count == 1
    assert result.document_navigation_count == 1
    assert events == ["coordinator", "profile", "browser"]
    assert context.closed
    assert len(context.pages) == 1
    assert context.pages[0].goto_count == 1
    assert context.pages[0].reload_count == 0
    assert context.pages[0].url == "https://www.facebook.com/groups/111"
    assert setup.guard_store.read() is None
    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
        target = app.repositories.targets.get(setup.target_id)
        latest = app.repositories.scan_runs.latest_by_target(setup.target_id)
        outbox = app.repositories.notification_outbox.list_pending()
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert state is not None and state.status == FacebookAccessCircuitStatus.CLOSED
    assert state.last_probe_result == FacebookProbeResult.SUCCESS
    assert target == setup.target_before
    assert product_state == setup.product_state_before
    assert latest is None
    assert outbox == []
    assert pacing is not None
    assert pacing.active_operation_id == ""
    assert pacing.next_automation_not_before == _NOW + timedelta(seconds=30)
    assert pacing.last_outcome == FacebookProbeResult.SUCCESS.value
    assert recipe_kind == setup.recipe_kind


@pytest.mark.parametrize(
    (
        "failure_reason",
        "expected_outcome",
        "expected_probe_result",
        "expected_needs_login",
    ),
    [
        (
            FACEBOOK_TEMPORARY_BLOCK_REASON,
            FacebookManualProbeOutcome.BLOCKED,
            FacebookProbeResult.BLOCKED,
            False,
        ),
        (
            LOGIN_REQUIRED_REASON,
            FacebookManualProbeOutcome.INCONCLUSIVE,
            FacebookProbeResult.INCONCLUSIVE,
            True,
        ),
        (
            CHECKPOINT_REQUIRED_REASON,
            FacebookManualProbeOutcome.INCONCLUSIVE,
            FacebookProbeResult.INCONCLUSIVE,
            True,
        ),
        (
            SESSION_INVALID_REASON,
            FacebookManualProbeOutcome.INCONCLUSIVE,
            FacebookProbeResult.INCONCLUSIVE,
            True,
        ),
        (
            "unknown",
            FacebookManualProbeOutcome.INCONCLUSIVE,
            FacebookProbeResult.INCONCLUSIVE,
            False,
        ),
    ],
)
def test_manual_probe_failure_reopens_owner_aware_and_preserves_session_semantics(
    failure_reason: str,
    expected_outcome: FacebookManualProbeOutcome,
    expected_probe_result: FacebookProbeResult,
    expected_needs_login: bool,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Probe失敗只在owner更新成功後同步既有session失效語義。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)
    context = FakeAsyncBrowserContext()

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def fail_guard(_page: object) -> None:
        raise WorkerFailure(failure_reason, "probe guard failed")

    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.acquire_profile_lease",
        lambda *_args, **_kwargs: _null_context(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe."
        "launch_persistent_context_async",
        lambda *_args, **_kwargs: _async_value(context),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe."
        "ensure_async_page_scannable",
        fail_guard,
    )

    result = asyncio.run(
        consume_pending_facebook_manual_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,
            admission_controller=controller,
            session_guard_runtime=guard_runtime,
            should_stop=lambda: False,
            clock=lambda: _NOW,
        )
    )

    assert result.outcome == expected_outcome
    assert result.failure_reason == failure_reason
    assert result.page_count == 1
    assert result.document_navigation_count == 1
    assert context.closed
    assert setup.guard_store.read() is None
    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
        session_status = app.repositories.app_settings.get_profile_session_status()
    assert state is not None and state.status == FacebookAccessCircuitStatus.OPEN
    assert state.last_probe_result == expected_probe_result
    assert state.half_open_token == ""
    assert state.reopen_count == int(failure_reason == FACEBOOK_TEMPORARY_BLOCK_REASON)
    assert (session_status.state.value == "needs_login") is expected_needs_login
    if expected_needs_login:
        assert session_status.reason == failure_reason
        assert session_status.source == "facebook_access_manual_probe"


def test_manual_probe_cancel_after_claim_launches_zero_browser(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Stop在claim後收斂為cancelled open，不取得coordinator/profile/browser。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = _ForbiddenCoordinator()
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.acquire_profile_lease",
        lambda *_args, **_kwargs: pytest.fail("profile lease must not be acquired"),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.async_playwright",
        lambda: pytest.fail("Playwright must not start"),
    )

    result = asyncio.run(
        consume_pending_facebook_manual_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,  # type: ignore[arg-type]
            admission_controller=controller,
            session_guard_runtime=guard_runtime,
            should_stop=lambda: True,
            clock=lambda: _NOW,
        )
    )

    assert result.outcome == FacebookManualProbeOutcome.CANCELLED
    assert result.claimed
    assert not result.browser_launched
    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
    assert state is not None and state.status == FacebookAccessCircuitStatus.OPEN
    assert state.last_probe_result == FacebookProbeResult.CANCELLED


@pytest.mark.parametrize("wait_kind", ["coordinator", "pacing"])
def test_manual_probe_stop_cancels_wait_and_finishes_owner_without_browser(
    wait_kind: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Stop在coordinator或persistent pacing等待中仍owner-aware取消且零browser。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.acquire_profile_lease",
        lambda *_args, **_kwargs: pytest.fail("profile lease must not be acquired"),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.async_playwright",
        lambda: pytest.fail("Playwright must not start"),
    )

    if wait_kind == "pacing":
        with SqliteApplicationContext(setup.options.db_path) as app:
            acquired = app.repositories.facebook_automation_pacing.try_acquire(
                _SCOPE,
                operation_id="seed-pacing",
                work_kind=FacebookAutomationWorkKind.TARGET_SCAN.value,
                owner_session_id="seed-owner",
                started_at=_NOW,
                lease_expires_at=_NOW + timedelta(seconds=1),
            )
            assert acquired.token is not None
            app.repositories.facebook_automation_pacing.finish(
                acquired.token,
                finished_at=_NOW,
                next_not_before=_NOW + timedelta(hours=1),
                outcome="finished",
            )

    async def run() -> FacebookManualProbeExecutionResult:
        blocker = None
        if wait_kind == "coordinator":
            blocker = await coordinator.acquire(FacebookAutomationWorkKind.TARGET_SCAN)
        stopping = False
        task = asyncio.create_task(
            consume_pending_facebook_manual_probe(
                options=setup.options,
                profile_scope_key=_SCOPE,
                coordinator=coordinator,
                admission_controller=controller,
                session_guard_runtime=guard_runtime,
                should_stop=lambda: stopping,
                clock=lambda: _NOW,
            )
        )
        for _ in range(200):
            snapshot = await coordinator.snapshot()
            with SqliteApplicationContext(setup.options.db_path) as app:
                circuit = app.services.facebook_access_circuit.get(_SCOPE)
            if (
                circuit is not None
                and circuit.status == FacebookAccessCircuitStatus.HALF_OPEN
                and (wait_kind == "coordinator" or snapshot.active)
            ):
                break
            await asyncio.sleep(0.001)
        else:
            raise AssertionError("probe did not enter expected wait")
        stopping = True
        result = await asyncio.wait_for(task, timeout=1)
        if blocker is not None:
            await blocker.release()
        return result

    result = asyncio.run(run())

    assert result.outcome == FacebookManualProbeOutcome.CANCELLED
    assert result.claimed and not result.browser_launched
    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
    assert state is not None and state.status == FacebookAccessCircuitStatus.OPEN
    assert state.last_probe_result == FacebookProbeResult.CANCELLED


def test_manual_probe_revalidates_target_after_profile_lease_before_browser(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Claim後target停用時在browser前owner-aware inconclusive且零launch。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)

    @contextmanager
    def pausing_profile_lease(*_args: object, **_kwargs: object):
        with SqliteApplicationContext(setup.options.db_path) as app:
            app.services.targets.pause_target_monitoring(setup.target_id)
        yield object()

    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.acquire_profile_lease",
        pausing_profile_lease,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.async_playwright",
        lambda: pytest.fail("Playwright must not start"),
    )

    result = asyncio.run(
        consume_pending_facebook_manual_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,
            admission_controller=controller,
            session_guard_runtime=guard_runtime,
            should_stop=lambda: False,
            clock=lambda: _NOW,
        )
    )

    assert result.outcome == FacebookManualProbeOutcome.INCONCLUSIVE
    assert result.claimed and not result.browser_launched
    assert setup.guard_store.read() is None
    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
    assert state is not None and state.status == FacebookAccessCircuitStatus.OPEN
    assert state.last_probe_result == FacebookProbeResult.INCONCLUSIVE


def test_manual_probe_launch_failure_reopens_but_keeps_unclean_sentinel(
    caplog: pytest.LogCaptureFixture,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Launch結果不確定時DB維持open，normal sentinel保留供restart fail closed。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def fail_launch(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("secret launch failure must not be logged")

    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.acquire_profile_lease",
        lambda *_args, **_kwargs: _null_context(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe."
        "launch_persistent_context_async",
        fail_launch,
    )

    result = asyncio.run(
        consume_pending_facebook_manual_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,
            admission_controller=controller,
            session_guard_runtime=guard_runtime,
            should_stop=lambda: False,
            clock=lambda: _NOW,
        )
    )

    assert result.outcome == FacebookManualProbeOutcome.INCONCLUSIVE
    assert not result.browser_launched
    assert result.failure_reason == SCHEDULER_RUNTIME_REASON
    assert result.failure_stage == FacebookProbeFailureStage.BROWSER_LAUNCH
    assert "secret launch failure" not in caplog.text
    assert (
        "facebook_probe_failure probe=manual stage=browser_launch "
        "reason=scheduler_runtime"
    ) in caplog.text
    marker = setup.guard_store.read()
    assert marker is not None
    assert marker.state.value == "normal_session"
    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
    assert state is not None and state.status == FacebookAccessCircuitStatus.OPEN
    assert pacing is not None and pacing.active_operation_id == ""
    assert pacing.next_automation_not_before == _NOW + timedelta(seconds=30)


def test_manual_probe_context_close_failure_is_classified_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Probe內容成功但context close失敗時保留sentinel並留下固定分類。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)

    class CloseFailsContext(FakeAsyncBrowserContext):
        async def close(self) -> None:
            raise RuntimeError("secret close failure")

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(
        manual_probe_module,
        "acquire_profile_lease",
        lambda *_args, **_kwargs: _null_context(),
    )
    monkeypatch.setattr(
        manual_probe_module,
        "async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        manual_probe_module,
        "launch_persistent_context_async",
        lambda *_args, **_kwargs: _async_value(CloseFailsContext()),
    )

    result = asyncio.run(
        consume_pending_facebook_manual_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,
            admission_controller=controller,
            session_guard_runtime=guard_runtime,
            should_stop=lambda: False,
            clock=lambda: _NOW,
        )
    )

    assert result.outcome == FacebookManualProbeOutcome.INCONCLUSIVE
    assert result.failure_reason == SCHEDULER_RUNTIME_REASON
    assert result.failure_stage == FacebookProbeFailureStage.CONTEXT_CLOSE
    assert controller.runtime_gate.snapshot().browser_io_poisoned
    assert setup.guard_store.read() is not None
    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
    assert state is not None and state.status == FacebookAccessCircuitStatus.OPEN
    assert state.last_probe_result == FacebookProbeResult.INCONCLUSIVE


def test_manual_probe_profile_lease_failure_releases_claimed_resources(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Profile lock失敗仍須釋放pacing/coordinator並owner-aware重開circuit。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)

    @contextmanager
    def fail_profile_lease(*_args: object, **_kwargs: object):
        raise ProfileLeaseError("injected profile lock failure")
        yield object()

    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.acquire_profile_lease",
        fail_profile_lease,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.async_playwright",
        lambda: pytest.fail("Playwright must not start"),
    )

    async def run_probe():
        result = await consume_pending_facebook_manual_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,
            admission_controller=controller,
            session_guard_runtime=guard_runtime,
            should_stop=lambda: False,
            clock=lambda: _NOW,
        )
        return result, await coordinator.snapshot()

    result, coordinator_state = asyncio.run(run_probe())

    assert result.outcome == FacebookManualProbeOutcome.INCONCLUSIVE
    assert result.claimed and not result.browser_launched
    assert result.finish_outcome == FacebookProbeFinishOutcome.UPDATED
    assert result.failure_reason == PROFILE_LOCKED_REASON
    assert result.failure_stage == FacebookProbeFailureStage.RESOURCE_ACQUIRE
    assert not coordinator_state.active
    assert coordinator_state.admitted_count == 1
    assert setup.guard_store.read() is None
    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
    assert state is not None and state.status == FacebookAccessCircuitStatus.OPEN
    assert state.last_probe_result == FacebookProbeResult.INCONCLUSIVE
    assert pacing is not None and pacing.active_operation_id == ""
    assert pacing.next_automation_not_before == _NOW + timedelta(seconds=30)


def test_manual_probe_absolute_deadline_is_inconclusive_and_closes_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """單一recipe deadline會收旂browser、owner與pacing，不誤記為cancel。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)
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
        manual_probe_module._APPROVED_RECIPES,
        recipe_kind,
        replace(
            manual_probe_module._APPROVED_RECIPES[recipe_kind],
            absolute_deadline_seconds=0.05,
            cleanup_grace_seconds=0.1,
        ),
    )
    monkeypatch.setattr(
        manual_probe_module,
        "acquire_profile_lease",
        lambda *_args, **_kwargs: _null_context(),
    )
    monkeypatch.setattr(
        manual_probe_module,
        "async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        manual_probe_module,
        "launch_persistent_context_async",
        lambda *_args, **_kwargs: _async_value(context),
    )
    monkeypatch.setattr(
        manual_probe_module,
        "ensure_async_page_scannable",
        hanging_guard,
    )

    result = asyncio.run(
        asyncio.wait_for(
            consume_pending_facebook_manual_probe(
                options=setup.options,
                profile_scope_key=_SCOPE,
                coordinator=coordinator,
                admission_controller=controller,
                session_guard_runtime=guard_runtime,
                should_stop=lambda: False,
                clock=lambda: _NOW,
            ),
            timeout=1,
        )
    )

    assert result.outcome == FacebookManualProbeOutcome.INCONCLUSIVE
    assert result.failure_reason == SCAN_TIMEOUT_REASON
    assert result.failure_stage == FacebookProbeFailureStage.DEADLINE
    assert result.browser_launched and context.closed
    assert setup.guard_store.read() is None
    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        target = app.repositories.targets.get(setup.target_id)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert state is not None and state.status == FacebookAccessCircuitStatus.OPEN
    assert state.last_probe_result == FacebookProbeResult.INCONCLUSIVE
    assert pacing is not None and pacing.active_operation_id == ""
    assert target == setup.target_before
    assert product_state == setup.product_state_before


def test_manual_probe_task_cancel_closes_context_and_reopens_before_propagating(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Browser中cancel仍先owner-aware finish/cleanup，再把CancelledError交回supervisor。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)
    context = FakeAsyncBrowserContext()

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.acquire_profile_lease",
        lambda *_args, **_kwargs: _null_context(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe."
        "launch_persistent_context_async",
        lambda *_args, **_kwargs: _async_value(context),
    )
    async def run_cancel() -> None:
        entered = asyncio.Event()

        async def hanging_guard(_page: object) -> None:
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(
            "facebook_monitor.worker.facebook_access_manual_probe."
            "ensure_async_page_scannable",
            hanging_guard,
        )
        task = asyncio.create_task(
            consume_pending_facebook_manual_probe(
                options=setup.options,
                profile_scope_key=_SCOPE,
                coordinator=coordinator,
                admission_controller=controller,
                session_guard_runtime=guard_runtime,
                should_stop=lambda: False,
                clock=lambda: _NOW,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_cancel())

    assert context.closed
    assert setup.guard_store.read() is None
    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        target = app.repositories.targets.get(setup.target_id)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert state is not None and state.status == FacebookAccessCircuitStatus.OPEN
    assert state.last_probe_result == FacebookProbeResult.CANCELLED
    assert pacing is not None and pacing.active_operation_id == ""
    assert target == setup.target_before
    assert product_state == setup.product_state_before


def test_manual_probe_cancel_during_cleanup_finishes_grace_before_reraising(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """取消落在context close時仍先用獨立cleanup grace收旂。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)
    close_started: asyncio.Event | None = None
    allow_close: asyncio.Event | None = None

    class ControlledCloseContext(FakeAsyncBrowserContext):
        async def close(self) -> None:
            assert close_started is not None and allow_close is not None
            close_started.set()
            await allow_close.wait()
            self.closed = True

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    context = ControlledCloseContext()
    monkeypatch.setattr(
        manual_probe_module,
        "acquire_profile_lease",
        lambda *_args, **_kwargs: _null_context(),
    )
    monkeypatch.setattr(
        manual_probe_module,
        "async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        manual_probe_module,
        "launch_persistent_context_async",
        lambda *_args, **_kwargs: _async_value(context),
    )

    async def run_cancel() -> None:
        nonlocal close_started, allow_close
        close_started = asyncio.Event()
        allow_close = asyncio.Event()
        task = asyncio.create_task(
            consume_pending_facebook_manual_probe(
                options=setup.options,
                profile_scope_key=_SCOPE,
                coordinator=coordinator,
                admission_controller=controller,
                session_guard_runtime=guard_runtime,
                should_stop=lambda: False,
                clock=lambda: _NOW,
            )
        )
        await asyncio.wait_for(close_started.wait(), timeout=1)
        task.cancel()
        allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_cancel())

    assert context.closed
    assert setup.guard_store.read() is None
    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert state is not None and state.status == FacebookAccessCircuitStatus.OPEN
    assert state.last_probe_result == FacebookProbeResult.CANCELLED
    assert pacing is not None and pacing.active_operation_id == ""
    assert product_state == setup.product_state_before


def test_manual_probe_deadline_cleans_partially_entered_playwright_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Manager enter中timeout也會呼叫bounded exit並收掉背景driver task。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)

    class PartialManager:
        exit_called = False
        background: asyncio.Task[bool] | None = None

        async def __aenter__(self) -> object:
            self.background = asyncio.create_task(asyncio.Event().wait())
            await asyncio.Event().wait()
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            self.exit_called = True
            assert self.background is not None
            self.background.cancel()
            await asyncio.gather(self.background, return_exceptions=True)

    manager = PartialManager()
    recipe_kind = FacebookRecoveryRecipeKind.GROUP_FEED_DOCUMENT_GUARD_V1
    monkeypatch.setitem(
        manual_probe_module._APPROVED_RECIPES,
        recipe_kind,
        replace(
            manual_probe_module._APPROVED_RECIPES[recipe_kind],
            absolute_deadline_seconds=0.05,
            cleanup_grace_seconds=0.1,
        ),
    )
    monkeypatch.setattr(
        manual_probe_module,
        "acquire_profile_lease",
        lambda *_args, **_kwargs: _null_context(),
    )
    monkeypatch.setattr(
        manual_probe_module,
        "async_playwright",
        lambda: manager,
    )

    result = asyncio.run(
        asyncio.wait_for(
            consume_pending_facebook_manual_probe(
                options=setup.options,
                profile_scope_key=_SCOPE,
                coordinator=coordinator,
                admission_controller=controller,
                session_guard_runtime=guard_runtime,
                should_stop=lambda: False,
                clock=lambda: _NOW,
            ),
            timeout=1,
        )
    )

    assert result.outcome == FacebookManualProbeOutcome.INCONCLUSIVE
    assert result.failure_reason == SCAN_TIMEOUT_REASON
    assert result.failure_stage == FacebookProbeFailureStage.DEADLINE
    assert result.cleanup_completed and not result.context_closed
    assert manager.exit_called
    assert manager.background is not None and manager.background.done()


def test_manual_probe_driver_timeouts_use_remaining_absolute_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Launch/goto driver timeout會隨已耗用budget縮短，不重置完整scan timeout。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)
    launch_timeout = 0.0
    goto_timeout = 0.0

    class RecordingPage:
        url = "about:blank"

        async def goto(self, url: str, *, wait_until: str, timeout: float) -> None:
            nonlocal goto_timeout
            self.url = url
            goto_timeout = timeout

        def locator(self, _selector: str) -> object:
            return FakeAsyncBrowserContext().pages

    class RecordingContext(FakeAsyncBrowserContext):
        def __init__(self) -> None:
            super().__init__()
            self.pages = [RecordingPage()]  # type: ignore[list-item]

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    context = RecordingContext()

    async def delayed_launch(_playwright: object, runtime_options: object) -> object:
        nonlocal launch_timeout
        launch_timeout = float(runtime_options.timeout_seconds)  # type: ignore[attr-defined]
        await asyncio.sleep(0.02)
        return context

    async def no_guard(_page: object) -> None:
        return None

    recipe_kind = FacebookRecoveryRecipeKind.GROUP_FEED_DOCUMENT_GUARD_V1
    monkeypatch.setitem(
        manual_probe_module._APPROVED_RECIPES,
        recipe_kind,
        replace(
            manual_probe_module._APPROVED_RECIPES[recipe_kind],
            absolute_deadline_seconds=0.2,
        ),
    )
    monkeypatch.setattr(
        manual_probe_module,
        "acquire_profile_lease",
        lambda *_args, **_kwargs: _null_context(),
    )
    monkeypatch.setattr(
        manual_probe_module,
        "async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        manual_probe_module,
        "launch_persistent_context_async",
        delayed_launch,
    )
    monkeypatch.setattr(
        manual_probe_module,
        "ensure_async_page_scannable",
        no_guard,
    )

    result = asyncio.run(
        consume_pending_facebook_manual_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,
            admission_controller=controller,
            session_guard_runtime=guard_runtime,
            should_stop=lambda: False,
            clock=lambda: _NOW,
        )
    )

    assert result.outcome == FacebookManualProbeOutcome.SUCCEEDED
    assert 0 < launch_timeout <= 0.2
    assert 0 < goto_timeout < launch_timeout * 1000


def test_comments_pending_recipe_remains_unclaimed_and_launches_zero_browser(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Phase4前即使DB出現comments request，supervisor也固定不claim、不launch。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.COMMENTS_ACCESS,
        force_comments_request=True,
    )
    coordinator = _ForbiddenCoordinator()
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.acquire_profile_lease",
        lambda *_args, **_kwargs: pytest.fail("profile lease must not be acquired"),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.async_playwright",
        lambda: pytest.fail("Playwright must not start"),
    )

    result = asyncio.run(
        consume_pending_facebook_manual_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,  # type: ignore[arg-type]
            admission_controller=controller,
            session_guard_runtime=guard_runtime,
            should_stop=lambda: False,
            clock=lambda: _NOW,
        )
    )

    assert result.outcome == FacebookManualProbeOutcome.RECIPE_UNAVAILABLE
    assert not result.claimed
    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
    assert state is not None and state.status == FacebookAccessCircuitStatus.OPEN
    assert state.probe_request_id


def test_poisoned_runtime_keeps_next_manual_probe_pending_without_browser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cleanup poison後同process的下一筆manual request不claim也不開browser。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)
    controller.runtime_gate.poison_browser_io()
    monkeypatch.setattr(
        manual_probe_module,
        "async_playwright",
        lambda: pytest.fail("poisoned runtime must not start Playwright"),
    )

    result = asyncio.run(
        consume_pending_facebook_manual_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,
            admission_controller=controller,
            session_guard_runtime=guard_runtime,
            should_stop=lambda: False,
            clock=lambda: _NOW,
        )
    )

    assert result.outcome == FacebookManualProbeOutcome.INCONCLUSIVE
    assert not result.claimed and not result.browser_launched
    assert result.failure_reason == SCHEDULER_RUNTIME_REASON
    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
    assert state is not None and state.status == FacebookAccessCircuitStatus.OPEN
    assert state.probe_request_id


def test_manual_probe_cancel_during_process_release_finishes_durable_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cancel落在coordinator release時仍先解owner與DB half-open再重新拋出。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)

    async def acquire_then_cancel_on_release(**kwargs: Any) -> None:
        inner = await coordinator.acquire(FacebookAutomationWorkKind.HALF_OPEN_PROBE)
        pacing = controller.try_acquire_half_open_probe_pacing(
            operation_id=inner.operation_id,
            started_at=_NOW,
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
        manual_probe_module,
        "_execute_claimed_probe_resources",
        acquire_then_cancel_on_release,
    )

    async def run() -> FacebookAutomationCoordinatorSnapshot:
        task = asyncio.create_task(
            consume_pending_facebook_manual_probe(
                options=setup.options,
                profile_scope_key=_SCOPE,
                coordinator=coordinator,
                admission_controller=controller,
                session_guard_runtime=guard_runtime,
                should_stop=lambda: False,
                clock=lambda: _NOW,
            )
        )
        with pytest.raises(asyncio.CancelledError):
            await task
        return await coordinator.snapshot()

    snapshot = asyncio.run(run())

    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert state is not None and state.status == FacebookAccessCircuitStatus.OPEN
    assert state.last_probe_result == FacebookProbeResult.CANCELLED
    assert pacing is not None
    assert pacing.last_outcome == FacebookProbeResult.CANCELLED.value
    assert product_state == setup.product_state_before
    assert not snapshot.active and snapshot.waiter_count == 0


def test_manual_probe_cancel_during_stop_monitor_join_finishes_durable_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Stop-monitor join中被cancel仍要先完成manual durable finish。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)
    owner_task: asyncio.Task[None] | None = None

    async def no_resources(**kwargs: Any) -> None:
        pacing = controller.try_acquire_half_open_probe_pacing(
            operation_id="manual-stop-monitor-cancel",
            started_at=_NOW,
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
        manual_probe_module,
        "_execute_claimed_probe_resources",
        no_resources,
    )
    monkeypatch.setattr(
        manual_probe_module,
        "_cancel_coordinator_waiters_when_stopping",
        cancel_owner_when_monitor_stops,
    )

    async def run() -> None:
        nonlocal owner_task
        owner_task = asyncio.current_task()
        with pytest.raises(asyncio.CancelledError):
            await consume_pending_facebook_manual_probe(
                options=setup.options,
                profile_scope_key=_SCOPE,
                coordinator=coordinator,
                admission_controller=controller,
                session_guard_runtime=guard_runtime,
                should_stop=lambda: False,
                clock=lambda: _NOW,
            )

    asyncio.run(run())

    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
        pacing = app.repositories.facebook_automation_pacing.get(_SCOPE)
        product_state = _product_state_snapshot(app.repositories.targets.connection)
    assert state is not None and state.status == FacebookAccessCircuitStatus.OPEN
    assert state.last_probe_result == FacebookProbeResult.CANCELLED
    assert pacing is not None
    assert pacing.last_outcome == FacebookProbeResult.CANCELLED.value
    assert product_state == setup.product_state_before


def test_manual_probe_discards_finish_after_owner_becomes_stale(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """另一transition先完成時，原probe result不得覆寫新generation。"""

    setup = _seed_pending_probe(
        tmp_path,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
    )
    coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=0,
        quiet_gap_max_seconds=0,
    )
    controller, guard_runtime = _runtime_dependencies(setup, coordinator)
    context = FakeAsyncBrowserContext()
    from facebook_monitor.worker import facebook_access_manual_probe as probe_module

    original_finish = probe_module._finish_probe

    def finish_after_other_owner(*args: Any, **kwargs: Any):
        with SqliteApplicationContext(setup.options.db_path) as app:
            app.services.facebook_access_circuit.finish_probe(
                _SCOPE,
                half_open_token=str(kwargs["half_open_token"]),
                generation=int(kwargs["generation"]),
                result=FacebookProbeResult.SUCCESS,
                finished_at=_NOW,
            )
        return original_finish(*args, **kwargs)

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.acquire_profile_lease",
        lambda *_args, **_kwargs: _null_context(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe."
        "launch_persistent_context_async",
        lambda *_args, **_kwargs: _async_value(context),
    )
    monkeypatch.setattr(probe_module, "_finish_probe", finish_after_other_owner)

    result = asyncio.run(
        consume_pending_facebook_manual_probe(
            options=setup.options,
            profile_scope_key=_SCOPE,
            coordinator=coordinator,
            admission_controller=controller,
            session_guard_runtime=guard_runtime,
            should_stop=lambda: False,
            clock=lambda: _NOW,
        )
    )

    assert result.outcome == FacebookManualProbeOutcome.STALE_OWNER
    with SqliteApplicationContext(setup.options.db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
    assert state is not None and state.status == FacebookAccessCircuitStatus.CLOSED
    assert state.last_probe_result == FacebookProbeResult.SUCCESS


@pytest.mark.parametrize("probe_result", ["success", "blocked"])
def test_same_process_trip_probe_resets_gate_only_after_verified_success(
    probe_result: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """同process open latch可執行唯一probe；僅DB verified closed後恢復normal。"""

    db_path = tmp_path / "data" / "app.db"
    profile_dir = tmp_path / "data" / "profiles" / "automation_default"
    profile_dir.mkdir(parents=True)
    identity = load_or_create_managed_profile_identity(
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    opened_at = _NOW - timedelta(hours=13)
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        admission = app.services.facebook_access_circuit.admit_normal(
            identity.profile_scope_key,
            process_safety_epoch=0,
            operation_id="same-process-trip",
            admitted_at=opened_at,
        )
        assert admission.token is not None
        app.services.facebook_access_circuit.trip(
            FacebookAccessBlockSignal(
                admission_token=admission.token,
                source_kind=FacebookWorkSourceKind.SCAN,
                operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
                trigger_action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
                source_owner_token="same-process-trip",
                target_id=target.id,
            ),
            source_owner_is_valid=True,
            detected_at=opened_at,
        )
        requested = app.services.facebook_access_circuit.request_probe(
            identity.profile_scope_key,
            target_id=target.id,
            requested_at=_NOW,
        )
        assert requested.state.probe_request_id
    contexts: list[FakeAsyncBrowserContext] = []
    scan_calls = 0
    clock_value = [_NOW]
    gate = FacebookAccessRuntimeGate()
    assert admission.token is not None
    assert gate.request_trip(admission.token)

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def fake_launch(*_args: object, **_kwargs: object) -> FakeAsyncBrowserContext:
        context = FakeAsyncBrowserContext()
        contexts.append(context)
        return context

    async def scan_page(**kwargs: Any) -> object:
        nonlocal scan_calls
        scan_calls += 1
        return build_success_scan_result_for_test(
            target=kwargs["target"],
            page_url=kwargs["page"].url,
        )

    async def probe_guard(_page: object) -> None:
        if probe_result == "blocked":
            raise WorkerFailure(FACEBOOK_TEMPORARY_BLOCK_REASON, "still blocked")

    async def advance_clock(seconds: float) -> None:
        clock_value[0] += timedelta(seconds=max(seconds, 0))
        await asyncio.sleep(0)

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.FacebookAccessRuntimeGate",
        lambda: gate,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: _null_context(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.acquire_profile_lease",
        lambda *_args, **_kwargs: _null_context(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe."
        "launch_persistent_context_async",
        fake_launch,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_access_manual_probe."
        "ensure_async_page_scannable",
        probe_guard,
    )

    asyncio.run(
        run_resident_main_loop(
            ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=profile_dir,
                interval_seconds=0,
                scheduler_tick_seconds=0,
                max_cycles=2,
            ),
            scan_page=as_async_scan_callable(scan_page),
            sleep_fn=advance_clock,
            automation_sleep_fn=advance_clock,
            automation_clock=lambda: clock_value[0],
        )
    )

    with SqliteApplicationContext(db_path) as app:
        circuit = app.repositories.facebook_access_circuit.get(
            identity.profile_scope_key
        )
    assert circuit is not None
    if probe_result == "success":
        assert circuit.status == FacebookAccessCircuitStatus.CLOSED
        assert len(contexts) == 2
        assert scan_calls == 1
        assert not gate.snapshot().writes_closed
    else:
        assert circuit.status == FacebookAccessCircuitStatus.OPEN
        assert len(contexts) == 1
        assert scan_calls == 0
        assert gate.snapshot().writes_closed


class _ProbeSetup:
    def __init__(
        self,
        *,
        options: ResidentRuntimeOptions,
        target_id: str,
        target_before: object,
        product_state_before: dict[str, tuple[tuple[Any, ...], ...]],
        recipe_kind: FacebookRecoveryRecipeKind,
        guard_store: FacebookAutomationSessionGuardStore,
    ) -> None:
        self.options = options
        self.target_id = target_id
        self.target_before = target_before
        self.product_state_before = product_state_before
        self.recipe_kind = recipe_kind
        self.guard_store = guard_store


def _seed_pending_probe(
    tmp_path: Path,
    *,
    operation_kind: FacebookProductOperationKind,
    force_comments_request: bool = False,
) -> _ProbeSetup:
    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation_default"
    profile_dir.mkdir(parents=True)
    opened_at = _NOW - timedelta(hours=13)
    with SqliteApplicationContext(db_path) as app:
        if operation_kind == FacebookProductOperationKind.COMMENTS_ACCESS:
            target = app.services.targets.upsert_comments_target(
                UpsertCommentsTargetRequest(
                    group_id="111",
                    parent_post_id="222",
                    canonical_url="https://www.facebook.com/groups/111/posts/222",
                )
            )
        else:
            target = app.services.targets.upsert_group_posts_target(
                UpsertGroupPostsTargetRequest(
                    group_id="111",
                    canonical_url="https://www.facebook.com/groups/111",
                )
            )
        app.services.targets.restart_target_monitoring(target.id)
        admission = app.services.facebook_access_circuit.admit_normal(
            _SCOPE,
            process_safety_epoch=0,
            operation_id="opening-operation",
            admitted_at=opened_at,
        )
        assert admission.token is not None
        action_kind = (
            FacebookActionKind.DIRECT_DOCUMENT
            if operation_kind == FacebookProductOperationKind.COMMENTS_ACCESS
            else FacebookActionKind.GROUP_DOCUMENT
        )
        opened = app.services.facebook_access_circuit.trip(
            FacebookAccessBlockSignal(
                admission_token=admission.token,
                source_kind=FacebookWorkSourceKind.SCAN,
                operation_kind=operation_kind,
                trigger_action_kind=action_kind,
                source_owner_token="opening-operation",
                target_id=target.id,
            ),
            source_owner_is_valid=True,
            detected_at=opened_at,
        )
        if force_comments_request:
            request_id = "comments-request"
            app.repositories.facebook_access_circuit.connection.execute(
                """
                UPDATE facebook_access_circuit_state
                SET recovery_recipe_kind = ?, probe_request_id = ?,
                    probe_requested_at = ?, requested_recipe_kind = ?,
                    requested_target_id = ?, cooldown_until = ?, updated_at = ?
                WHERE profile_scope_key = ?
                """,
                (
                    FacebookRecoveryRecipeKind.COMMENTS_GROUP_TRUSTED_CLICK_V1.value,
                    request_id,
                    encode_datetime(_NOW),
                    FacebookRecoveryRecipeKind.COMMENTS_GROUP_TRUSTED_CLICK_V1.value,
                    target.id,
                    encode_datetime(_NOW),
                    encode_datetime(_NOW),
                    _SCOPE,
                ),
            )
            recipe_kind = FacebookRecoveryRecipeKind.COMMENTS_GROUP_TRUSTED_CLICK_V1
        else:
            request = app.services.facebook_access_circuit.request_probe(
                _SCOPE,
                target_id=target.id,
                requested_at=_NOW,
            )
            assert request.state.probe_request_id
            recipe_kind = opened.state.recovery_recipe_kind
        target_before = app.repositories.targets.get(target.id)
        product_state_before = _product_state_snapshot(
            app.repositories.targets.connection
        )
    store = FacebookAutomationSessionGuardStore(
        tmp_path / "facebook-automation-session-guards",
        profile_alias=derive_facebook_automation_profile_alias(_SCOPE),
    )
    return _ProbeSetup(
        options=ResidentRuntimeOptions(db_path=db_path, profile_dir=profile_dir),
        target_id=target.id,
        target_before=target_before,
        product_state_before=product_state_before,
        recipe_kind=recipe_kind,
        guard_store=store,
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


def _runtime_dependencies(
    setup: _ProbeSetup,
    coordinator: Any,
) -> tuple[FacebookAutomationAdmissionController, FacebookAutomationSessionGuardRuntime]:
    runtime = FacebookAutomationSessionGuardRuntime(setup.guard_store)
    controller = FacebookAutomationAdmissionController(
        db_path=setup.options.db_path,
        profile_scope_key=_SCOPE,
        runtime_gate=FacebookAccessRuntimeGate(),
        coordinator=coordinator,
        persistent_quiet_gap_seconds=30,
        clock=lambda: _NOW,
        session_guard_runtime=runtime,
    )
    return controller, runtime


class _RecordingCoordinator(FacebookAutomationCoordinator):
    def __init__(self, *, db_path: Path, events: list[str]) -> None:
        super().__init__(quiet_gap_min_seconds=0, quiet_gap_max_seconds=0)
        self.db_path = db_path
        self.events = events

    async def acquire(
        self,
        work_kind: FacebookAutomationWorkKind,
        *,
        owner_alias: str = "",
    ):
        _assert_half_open(self.db_path)
        self.events.append("coordinator")
        return await super().acquire(work_kind, owner_alias=owner_alias)


class _ForbiddenCoordinator:
    async def acquire(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("coordinator must not be acquired")


def _assert_half_open(db_path: Path) -> None:
    with SqliteApplicationContext(db_path) as app:
        state = app.services.facebook_access_circuit.get(_SCOPE)
    assert state is not None and state.status == FacebookAccessCircuitStatus.HALF_OPEN


@contextmanager
def _null_context():
    yield object()


async def _async_value(value: Any) -> Any:
    return value
