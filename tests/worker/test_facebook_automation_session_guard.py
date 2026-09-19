"""Facebook automation durable session sentinel tests。"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import UTC
from datetime import datetime
from datetime import timedelta
import json
import os
from pathlib import Path
import stat
from typing import Any
import zipfile

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.facebook_access_circuit_service import (
    FacebookAccessCircuitService,
)
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.automation.profile_identity import (
    load_or_create_managed_profile_identity,
)
from facebook_monitor.core.facebook_access import (
    FACEBOOK_ACCESS_PERSISTENCE_UNCERTAIN_REASON,
)
from facebook_monitor.core.facebook_access import FacebookAccessBlockSignal
from facebook_monitor.core.facebook_access import FacebookAccessCircuitStatus
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookAdmissionToken
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookRecoveryRecipeKind
from facebook_monitor.core.facebook_access import FacebookSafetyHoldOutcome
from facebook_monitor.core.facebook_access import FacebookWorkSourceKind
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.diagnostics.support_bundle import create_support_bundle
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.runtime.paths import resolve_runtime_paths
from facebook_monitor.worker.facebook_automation_session_guard import (
    derive_facebook_automation_profile_alias,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationRestartGuardOutcome,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationRestartGuardResult,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardError,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardErrorCode,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardRuntime,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardState,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardStore,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    reconcile_facebook_automation_restart_guard,
)
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.facebook_access_incident import (
    record_facebook_access_incident_for_db_async,
)
from facebook_monitor.worker.facebook_access_manual_probe import (
    FacebookManualProbeExecutionResult,
)
from facebook_monitor.worker.facebook_access_manual_probe import (
    FacebookManualProbeOutcome,
)
from facebook_monitor.worker.resident_main import run_resident_main_loop
from facebook_monitor.worker.resident_shared import ResidentCycleSummary
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions

from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext
from tests.worker.resident_main_test_helpers import as_async_scan_callable


_SESSION_ID = "00000000-0000-4000-8000-000000000001"
_OTHER_SESSION_ID = "00000000-0000-4000-8000-000000000002"
_NOW = datetime(2026, 7, 22, 8, 30, tzinfo=UTC)


def test_normal_marker_is_private_atomic_and_privacy_safe(tmp_path: Path) -> None:
    """Marker只保存allowlist欄位，且POSIX權限為private。"""

    store = _store(tmp_path)
    marker = store.start_normal_session(started_at=_NOW, session_id=_SESSION_ID)

    payload = json.loads(store.marker_path.read_text(encoding="utf-8"))
    assert marker.state == FacebookAutomationSessionGuardState.NORMAL_SESSION
    assert payload == {
        "profile_alias": "profile-1",
        "session_id": _SESSION_ID,
        "started_at": "2026-07-22T08:30:00Z",
        "state": "normal_session",
        "version": 1,
    }
    serialized = store.marker_path.read_text(encoding="utf-8")
    for forbidden in ("target", "url", "cookie", "evidence", "scope_key", str(tmp_path)):
        assert forbidden not in serialized.casefold()
    assert list(store.directory.glob(".session-guard-*.tmp")) == []
    if os.name != "nt":
        assert stat.S_IMODE(store.marker_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700


def test_existing_or_invalid_marker_fails_closed_without_overwrite(tmp_path: Path) -> None:
    """既有marker與額外欄位都不能被新session默默覆蓋。"""

    store = _store(tmp_path)
    store.start_normal_session(started_at=_NOW, session_id=_SESSION_ID)
    original = store.marker_path.read_bytes()

    with pytest.raises(FacebookAutomationSessionGuardError) as duplicate:
        store.start_normal_session(started_at=_NOW, session_id=_OTHER_SESSION_ID)
    assert duplicate.value.code == (
        FacebookAutomationSessionGuardErrorCode.MARKER_ALREADY_PRESENT
    )
    assert store.marker_path.read_bytes() == original

    payload = json.loads(original)
    payload["url"] = "https://example.invalid/private"
    store.marker_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FacebookAutomationSessionGuardError) as invalid:
        store.read()
    assert invalid.value.code == FacebookAutomationSessionGuardErrorCode.INVALID_MARKER
    assert store.marker_path.exists()


def test_trip_pending_replace_and_owner_specific_cleanup(tmp_path: Path) -> None:
    """Trip marker保留session owner且只有durable-trip cleanup可移除。"""

    store = _store(tmp_path)
    store.start_normal_session(started_at=_NOW, session_id=_SESSION_ID)
    marker = store.mark_trip_pending(
        session_id=_SESSION_ID,
        operation_kind=FacebookProductOperationKind.COMMENTS_ACCESS,
        trigger_action_kind=FacebookActionKind.DIRECT_DOCUMENT,
    )

    assert marker.state == FacebookAutomationSessionGuardState.TRIP_PENDING
    payload = json.loads(store.marker_path.read_text(encoding="utf-8"))
    assert payload["operation_kind"] == "comments_access"
    assert payload["trigger_action_kind"] == "direct_document"
    assert payload["reason_code"] == "facebook_temporary_block"
    assert "target" not in payload
    assert "evidence" not in payload
    with pytest.raises(FacebookAutomationSessionGuardError) as wrong_cleanup:
        store.clear_clean_session(session_id=_SESSION_ID)
    assert wrong_cleanup.value.code == FacebookAutomationSessionGuardErrorCode.STATE_MISMATCH
    with pytest.raises(FacebookAutomationSessionGuardError) as wrong_owner:
        store.clear_persisted_trip(session_id=_OTHER_SESSION_ID)
    assert wrong_owner.value.code == FacebookAutomationSessionGuardErrorCode.OWNER_MISMATCH

    store.clear_persisted_trip(session_id=_SESSION_ID)
    assert not store.marker_path.exists()


def test_failed_atomic_replace_keeps_normal_marker(monkeypatch, tmp_path: Path) -> None:
    """Replace失敗不得留下半份trip marker或刪除normal crash truth。"""

    store = _store(tmp_path)
    store.start_normal_session(started_at=_NOW, session_id=_SESSION_ID)
    before = store.marker_path.read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_automation_session_guard.os.replace",
        fail_replace,
    )
    with pytest.raises(FacebookAutomationSessionGuardError) as failed:
        store.mark_trip_pending(
            session_id=_SESSION_ID,
            operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
            trigger_action_kind=FacebookActionKind.DIRECT_DOCUMENT,
        )
    assert failed.value.code == FacebookAutomationSessionGuardErrorCode.STORAGE_UNAVAILABLE
    assert store.marker_path.read_bytes() == before
    current = store.read()
    assert current is not None
    assert current.state == FacebookAutomationSessionGuardState.NORMAL_SESSION
    assert list(store.directory.glob(".session-guard-*.tmp")) == []


def test_stale_normal_session_is_separate_hold_and_does_not_open_circuit(
    tmp_path: Path,
) -> None:
    """Unclean normal session保持0 browser I/O且不能冒充Facebook block。"""

    db_path = _initialize_db(tmp_path)
    store = _store(tmp_path)
    store.start_normal_session(started_at=_NOW, session_id=_SESSION_ID)

    result = reconcile_facebook_automation_restart_guard(
        db_path=db_path,
        store=store,
        profile_scope_key="opaque-scope-a",
        reconciled_at=_NOW,
    )

    assert result.outcome == FacebookAutomationRestartGuardOutcome.UNCLEAN_SESSION_HOLD
    assert not result.browser_io_allowed
    assert result.reason_code == "facebook_automation_unclean_session"
    assert store.marker_path.exists()
    with SqliteApplicationContext(db_path, initialize_schema_on_enter=False) as app:
        assert app.services.facebook_access_circuit.get("opaque-scope-a") is None


def test_trip_pending_restart_commits_uncertain_hold_then_removes_marker(
    tmp_path: Path,
) -> None:
    """Trip marker只在uncertain circuit transaction commit後移除。"""

    db_path = _initialize_db(tmp_path)
    store = _trip_store(tmp_path, operation=FacebookProductOperationKind.COMMENTS_ACCESS)

    result = reconcile_facebook_automation_restart_guard(
        db_path=db_path,
        store=store,
        profile_scope_key="opaque-scope-a",
        reconciled_at=_NOW,
    )

    assert result.outcome == (
        FacebookAutomationRestartGuardOutcome.PERSISTENCE_UNCERTAIN_RECONCILED
    )
    assert not result.browser_io_allowed
    assert result.safety_hold is not None
    assert result.safety_hold.outcome == FacebookSafetyHoldOutcome.OPENED
    assert not store.marker_path.exists()
    with SqliteApplicationContext(db_path, initialize_schema_on_enter=False) as app:
        state = app.services.facebook_access_circuit.get("opaque-scope-a")
        assert state is not None
        assert state.status == FacebookAccessCircuitStatus.OPEN
        assert state.reason_code == FACEBOOK_ACCESS_PERSISTENCE_UNCERTAIN_REASON
        assert state.operation_kind == FacebookProductOperationKind.COMMENTS_ACCESS
        assert state.trigger_action_kind == FacebookActionKind.DIRECT_DOCUMENT
        assert state.source_kind is None
        assert state.trigger_target_id is None
        assert state.detection_count == 0
        assert state.recovery_recipe_kind == FacebookRecoveryRecipeKind.NONE


def test_reconcile_failure_rolls_back_database_and_keeps_marker(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """DB transaction任何例外都rollback，trip marker保留供下次重試。"""

    db_path = _initialize_db(tmp_path)
    store = _trip_store(tmp_path, operation=FacebookProductOperationKind.POSTS_ACCESS)
    original = FacebookAccessCircuitService.reconcile_persistence_uncertain

    def fail_after_write(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(
        FacebookAccessCircuitService,
        "reconcile_persistence_uncertain",
        fail_after_write,
    )
    result = reconcile_facebook_automation_restart_guard(
        db_path=db_path,
        store=store,
        profile_scope_key="opaque-scope-a",
        reconciled_at=_NOW,
    )

    assert result.outcome == FacebookAutomationRestartGuardOutcome.STORAGE_CRITICAL
    assert not result.browser_io_allowed
    current = store.read()
    assert current is not None
    assert current.state == FacebookAutomationSessionGuardState.TRIP_PENDING
    with SqliteApplicationContext(db_path, initialize_schema_on_enter=False) as app:
        assert app.services.facebook_access_circuit.get("opaque-scope-a") is None


def test_existing_durable_block_is_not_overwritten_by_stale_trip_marker(
    tmp_path: Path,
) -> None:
    """Restart reconcile遇到既有open truth只清marker，不降級原episode reason。"""

    db_path = _initialize_db(tmp_path)
    with SqliteApplicationContext(db_path, initialize_schema_on_enter=False) as app:
        admission = app.services.facebook_access_circuit.admit_normal(
            "opaque-scope-a",
            process_safety_epoch=1,
            operation_id="operation-a",
            admitted_at=_NOW,
        )
        assert admission.token is not None
        app.services.facebook_access_circuit.trip(
            FacebookAccessBlockSignal(
                admission_token=FacebookAdmissionToken(
                    profile_scope_key="opaque-scope-a",
                    db_generation=admission.token.db_generation,
                    process_safety_epoch=1,
                    operation_id="operation-a",
                ),
                source_kind=FacebookWorkSourceKind.METADATA,
                operation_kind=FacebookProductOperationKind.GROUP_METADATA_ACCESS,
                trigger_action_kind=FacebookActionKind.GROUP_DOCUMENT,
                source_owner_token="operation-a",
            ),
            source_owner_is_valid=True,
            detected_at=_NOW,
        )
    store = _trip_store(tmp_path, operation=FacebookProductOperationKind.COMMENTS_ACCESS)

    result = reconcile_facebook_automation_restart_guard(
        db_path=db_path,
        store=store,
        profile_scope_key="opaque-scope-a",
        reconciled_at=_NOW,
    )

    assert result.safety_hold is not None
    assert result.safety_hold.outcome == FacebookSafetyHoldOutcome.ALREADY_DURABLE
    assert not store.marker_path.exists()
    with SqliteApplicationContext(db_path, initialize_schema_on_enter=False) as app:
        state = app.services.facebook_access_circuit.get("opaque-scope-a")
        assert state is not None
        assert state.reason_code == "facebook_temporary_block"
        assert state.operation_kind == FacebookProductOperationKind.GROUP_METADATA_ACCESS


def test_runtime_path_owns_guard_directory_and_support_bundle_excludes_marker(
    tmp_path: Path,
) -> None:
    """Sentinel留在data-dir專用目錄，support bundle不收檔名或內容。"""

    paths = resolve_runtime_paths(
        data_dir=tmp_path / "data",
        app_base_dir=tmp_path / "app",
    )
    paths.ensure_writable_dirs()
    assert paths.facebook_automation_session_guards_dir == (
        paths.data_dir / "facebook-automation-session-guards"
    )
    store = FacebookAutomationSessionGuardStore(
        paths.facebook_automation_session_guards_dir,
        profile_alias="profile-1",
    )
    store.start_normal_session(started_at=_NOW, session_id=_SESSION_ID)
    with SqliteApplicationContext(paths.db_path):
        pass

    bundle = create_support_bundle(
        paths=paths,
        runtime_diagnostics_text="",
        app_metadata={},
    )
    with zipfile.ZipFile(bundle.path) as archive:
        names = archive.namelist()
        contents = b"\n".join(archive.read(name) for name in names)
    assert all("session-guard" not in name for name in names)
    assert _SESSION_ID.encode() not in contents
    assert store.marker_path.name.encode() not in contents


def test_runtime_marker_is_removed_only_after_committed_trip_context_cleanup(
    tmp_path: Path,
) -> None:
    """Durable incident前及context尚未關閉時都不得清trip marker。"""

    store = _store(tmp_path)
    runtime = FacebookAutomationSessionGuardRuntime(store)
    runtime.start_before_browser_io(started_at=_NOW)
    runtime.mark_trip_pending(
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
        trigger_action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
    )

    assert not runtime.finish_after_browser_context_closed()
    assert store.marker_path.exists()
    runtime.note_incident_committed()
    assert runtime.finish_after_browser_context_closed()
    assert not store.marker_path.exists()


def test_two_managed_profiles_use_distinct_non_name_guard_aliases(tmp_path: Path) -> None:
    """同data-dir的多profile不得碰撞，也不把實際folder name寫進sentinel。"""

    profiles_root = tmp_path / "data" / "profiles"
    first_profile = profiles_root / "private-first-profile"
    second_profile = profiles_root / "private-second-profile"
    first_profile.mkdir(parents=True)
    second_profile.mkdir(parents=True)
    first_identity = load_or_create_managed_profile_identity(
        profiles_root=profiles_root,
        profile_dir=first_profile,
    )
    second_identity = load_or_create_managed_profile_identity(
        profiles_root=profiles_root,
        profile_dir=second_profile,
    )
    first_alias = derive_facebook_automation_profile_alias(
        first_identity.profile_scope_key
    )
    second_alias = derive_facebook_automation_profile_alias(
        second_identity.profile_scope_key
    )
    guard_dir = tmp_path / "data" / "facebook-automation-session-guards"
    first_store = FacebookAutomationSessionGuardStore(
        guard_dir,
        profile_alias=first_alias,
    )
    second_store = FacebookAutomationSessionGuardStore(
        guard_dir,
        profile_alias=second_alias,
    )

    first_store.start_normal_session(started_at=_NOW, session_id=_SESSION_ID)
    second_store.start_normal_session(started_at=_NOW, session_id=_OTHER_SESSION_ID)

    assert first_alias != second_alias
    assert first_store.marker_path != second_store.marker_path
    combined = first_store.marker_path.read_text() + second_store.marker_path.read_text()
    assert "private-first-profile" not in combined
    assert "private-second-profile" not in combined


@pytest.mark.parametrize("marker_state", ["normal_session", "trip_pending"])
def test_resident_restart_guard_launches_zero_browsers(
    marker_state: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Stale normal/trip marker都必須在Playwright入口前形成0-launch hold。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.profile_dir.mkdir(parents=True)
    with SqliteApplicationContext(paths.db_path):
        pass
    identity = load_or_create_managed_profile_identity(
        profiles_root=paths.profiles_dir,
        profile_dir=paths.profile_dir,
    )
    store = FacebookAutomationSessionGuardStore(
        paths.facebook_automation_session_guards_dir,
        profile_alias=derive_facebook_automation_profile_alias(
            identity.profile_scope_key
        ),
    )
    store.start_normal_session(started_at=_NOW, session_id=_SESSION_ID)
    if marker_state == "trip_pending":
        store.mark_trip_pending(
            session_id=_SESSION_ID,
            operation_kind=FacebookProductOperationKind.COMMENTS_ACCESS,
            trigger_action_kind=FacebookActionKind.DIRECT_DOCUMENT,
        )
    playwright_calls = 0

    def forbidden_playwright() -> object:
        nonlocal playwright_calls
        playwright_calls += 1
        raise AssertionError("restart guard must stop before Playwright")

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        forbidden_playwright,
    )

    async def run_test() -> None:
        await run_resident_main_loop(
            ResidentRuntimeOptions(
                db_path=paths.db_path,
                profile_dir=paths.profile_dir,
                scheduler_tick_seconds=0,
                max_cycles=1,
            ),
            automation_clock=lambda: _NOW,
        )

    asyncio.run(run_test())

    assert playwright_calls == 0
    if marker_state == "normal_session":
        assert store.marker_path.exists()
        with SqliteApplicationContext(
            paths.db_path,
            initialize_schema_on_enter=False,
        ) as app:
            assert app.services.facebook_access_circuit.get(identity.profile_scope_key) is None
    else:
        assert not store.marker_path.exists()
        with SqliteApplicationContext(
            paths.db_path,
            initialize_schema_on_enter=False,
        ) as app:
            state = app.services.facebook_access_circuit.get(identity.profile_scope_key)
        assert state is not None
        assert state.status == FacebookAccessCircuitStatus.OPEN
        assert state.reason_code == FACEBOOK_ACCESS_PERSISTENCE_UNCERTAIN_REASON


@pytest.mark.parametrize(
    ("cleanup_completed", "max_cycles"),
    [(True, 1), (False, 2)],
)
def test_trip_pending_reconcile_allows_only_safe_same_process_probe(
    cleanup_completed: bool,
    max_cycles: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Trip request可消化一次；cleanup未完成就立即停止runtime。"""

    paths = resolve_runtime_paths(
        data_dir=tmp_path / "data",
        app_base_dir=tmp_path / "app",
    )
    paths.profile_dir.mkdir(parents=True)
    with SqliteApplicationContext(paths.db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="trip-recovery-group",
                canonical_url="https://www.facebook.com/groups/trip-recovery-group",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
    identity = load_or_create_managed_profile_identity(
        profiles_root=paths.profiles_dir,
        profile_dir=paths.profile_dir,
    )
    store = FacebookAutomationSessionGuardStore(
        paths.facebook_automation_session_guards_dir,
        profile_alias=derive_facebook_automation_profile_alias(
            identity.profile_scope_key
        ),
    )
    store.start_normal_session(started_at=_NOW, session_id=_SESSION_ID)
    store.mark_trip_pending(
        session_id=_SESSION_ID,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
        trigger_action_kind=FacebookActionKind.DIRECT_DOCUMENT,
    )
    original_reconcile = reconcile_facebook_automation_restart_guard
    request_queued = False

    def reconcile_and_queue_probe(
        **kwargs: Any,
    ) -> FacebookAutomationRestartGuardResult:
        """在首次 reconcile 完成後模擬 Web 已提交的 persistent request。"""

        nonlocal request_queued
        result = original_reconcile(**kwargs)
        if result.safety_hold is not None and not request_queued:
            with SqliteApplicationContext(paths.db_path) as app:
                app.repositories.facebook_access_circuit.connection.execute(
                    """
                    UPDATE facebook_access_circuit_state
                    SET cooldown_until = ?
                    WHERE profile_scope_key = ?
                    """,
                    (encode_datetime(_NOW), identity.profile_scope_key),
                )
                requested = app.services.facebook_access_circuit.request_probe(
                    identity.profile_scope_key,
                    target_id=target.id,
                    requested_at=_NOW,
                )
                assert requested.state.probe_request_id
            request_queued = True
        return result

    probe_calls = 0

    async def consume_probe(**_kwargs: Any) -> FacebookManualProbeExecutionResult:
        nonlocal probe_calls
        probe_calls += 1
        return FacebookManualProbeExecutionResult(
            outcome=FacebookManualProbeOutcome.INCONCLUSIVE,
            cleanup_completed=cleanup_completed,
        )

    def forbidden_playwright() -> object:
        raise AssertionError("controlled probe stub must run before resident browser")

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main."
        "reconcile_facebook_automation_restart_guard",
        reconcile_and_queue_probe,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.consume_pending_facebook_manual_probe",
        consume_probe,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        forbidden_playwright,
    )

    async def run_test() -> list[ResidentCycleSummary]:
        return await run_resident_main_loop(
            ResidentRuntimeOptions(
                db_path=paths.db_path,
                profile_dir=paths.profile_dir,
                scheduler_tick_seconds=0,
                max_cycles=max_cycles,
            ),
            automation_clock=lambda: _NOW,
        )

    summaries = asyncio.run(run_test())

    assert request_queued
    assert probe_calls == 1
    assert len(summaries) == 1
    assert not store.marker_path.exists()


def test_resident_reconciles_closed_session_probe_when_block_finalize_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Blocked finalize例外後，已關context的trip marker要在同process重讀收旂。"""

    paths = resolve_runtime_paths(
        data_dir=tmp_path / "data",
        app_base_dir=tmp_path / "app",
    )
    paths.profile_dir.mkdir(parents=True)
    with SqliteApplicationContext(paths.db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="blocked-finalize-group",
                canonical_url=(
                    "https://www.facebook.com/groups/blocked-finalize-group"
                ),
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
    identity = load_or_create_managed_profile_identity(
        profiles_root=paths.profiles_dir,
        profile_dir=paths.profile_dir,
    )
    store = FacebookAutomationSessionGuardStore(
        paths.facebook_automation_session_guards_dir,
        profile_alias=derive_facebook_automation_profile_alias(
            identity.profile_scope_key
        ),
    )
    store.start_normal_session(started_at=_NOW, session_id=_SESSION_ID)
    initial = reconcile_facebook_automation_restart_guard(
        db_path=paths.db_path,
        store=store,
        profile_scope_key=identity.profile_scope_key,
        reconciled_at=_NOW,
    )
    assert initial.outcome == FacebookAutomationRestartGuardOutcome.UNCLEAN_SESSION_HOLD
    with SqliteApplicationContext(paths.db_path) as app:
        requested = app.services.facebook_session_recovery.request_probe(
            identity.profile_scope_key,
            target_id=target.id,
            operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
            requested_at=_NOW + timedelta(seconds=30),
        )
    assert requested.state.request_id

    context = FakeAsyncBrowserContext()

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def fake_launch(*_args: object, **_kwargs: object) -> FakeAsyncBrowserContext:
        return context

    async def blocked_guard(_page: object) -> None:
        raise WorkerFailure(FACEBOOK_TEMPORARY_BLOCK_REASON, "blocked")

    def fail_blocked_finalize(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("injected blocked finalize failure")

    current_time = [_NOW + timedelta(seconds=31)]

    async def advance_automation_clock(seconds: float) -> None:
        current_time[0] += timedelta(seconds=seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_session_recovery_probe.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
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
        blocked_guard,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_session_recovery_probe._finish_blocked_probe",
        fail_blocked_finalize,
    )

    asyncio.run(
        run_resident_main_loop(
            ResidentRuntimeOptions(
                db_path=paths.db_path,
                profile_dir=paths.profile_dir,
                scheduler_tick_seconds=0,
                max_cycles=1,
            ),
            automation_sleep_fn=advance_automation_clock,
            automation_clock=lambda: current_time[0],
        )
    )

    assert context.closed
    assert store.read() is None
    with SqliteApplicationContext(paths.db_path) as app:
        circuit = app.services.facebook_access_circuit.get(identity.profile_scope_key)
        recovery = app.services.facebook_session_recovery.get(identity.profile_scope_key)
    assert circuit is not None
    assert circuit.status == FacebookAccessCircuitStatus.OPEN
    assert circuit.reason_code == FACEBOOK_ACCESS_PERSISTENCE_UNCERTAIN_REASON
    assert recovery is not None
    assert recovery.last_probe_result.value == "inconclusive"


def test_resident_recovers_expired_half_open_before_playwright(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Expired half-open lease以DB CAS回open，且整個restart tick為0 browser I/O。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.profile_dir.mkdir(parents=True)
    identity = load_or_create_managed_profile_identity(
        profiles_root=paths.profiles_dir,
        profile_dir=paths.profile_dir,
    )
    started = _NOW - timedelta(minutes=10)
    expired = _NOW - timedelta(minutes=5)
    with SqliteApplicationContext(paths.db_path) as app:
        app.repositories.facebook_access_circuit.ensure_closed(
            identity.profile_scope_key,
            updated_at=started,
        )
        app.repositories.facebook_access_circuit.connection.execute(
            """
            UPDATE facebook_access_circuit_state
            SET state = 'half_open', episode_id = 'episode-a', generation = 2,
                reason_code = 'facebook_temporary_block', source_kind = 'scan',
                operation_kind = 'posts_access',
                trigger_action_kind = 'group_feed_document',
                recovery_recipe_kind = 'group_feed_document_guard_v1',
                opened_at = ?, last_detected_at = ?, cooldown_until = ?,
                detection_count = 1, half_open_token = 'probe-owner',
                half_open_started_at = ?, half_open_lease_expires_at = ?,
                updated_at = ?
            WHERE profile_scope_key = ?
            """,
            (
                encode_datetime(started),
                encode_datetime(started),
                encode_datetime(started),
                encode_datetime(started),
                encode_datetime(expired),
                encode_datetime(started),
                identity.profile_scope_key,
            ),
        )
    playwright_calls = 0

    def forbidden_playwright() -> object:
        nonlocal playwright_calls
        playwright_calls += 1
        raise AssertionError("expired half-open recovery must be browser-free")

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        forbidden_playwright,
    )

    asyncio.run(
        run_resident_main_loop(
            ResidentRuntimeOptions(
                db_path=paths.db_path,
                profile_dir=paths.profile_dir,
                max_cycles=1,
            ),
            automation_clock=lambda: _NOW,
        )
    )

    assert playwright_calls == 0
    with SqliteApplicationContext(paths.db_path) as app:
        state = app.services.facebook_access_circuit.get(identity.profile_scope_key)
        events = app.repositories.facebook_access_circuit.list_recent_events(
            identity.profile_scope_key
        )
    assert state is not None
    assert state.status == FacebookAccessCircuitStatus.OPEN
    assert state.half_open_token == ""
    assert events[0].event_kind.value == "lease_recovered"


def test_resident_creates_marker_before_launch_and_cleans_after_context_close(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """正常resident session在launch時marker已durable，context關閉後才清除。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.profile_dir.mkdir(parents=True)
    with SqliteApplicationContext(paths.db_path):
        pass
    identity = load_or_create_managed_profile_identity(
        profiles_root=paths.profiles_dir,
        profile_dir=paths.profile_dir,
    )
    store = FacebookAutomationSessionGuardStore(
        paths.facebook_automation_session_guards_dir,
        profile_alias=derive_facebook_automation_profile_alias(
            identity.profile_scope_key
        ),
    )
    contexts: list[FakeAsyncBrowserContext] = []

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    async def fake_launch(
        _playwright: object,
        _options: object,
    ) -> FakeAsyncBrowserContext:
        marker = store.read()
        assert marker is not None
        assert marker.state == FacebookAutomationSessionGuardState.NORMAL_SESSION
        context = FakeAsyncBrowserContext()
        contexts.append(context)
        return context

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch,
    )

    async def run_test() -> None:
        await run_resident_main_loop(
            ResidentRuntimeOptions(
                db_path=paths.db_path,
                profile_dir=paths.profile_dir,
                scheduler_tick_seconds=0,
                max_cycles=1,
            ),
            automation_sleep_fn=lambda _seconds: asyncio.sleep(0),
            automation_clock=lambda: _NOW,
        )

    asyncio.run(run_test())

    assert len(contexts) == 1
    assert contexts[0].closed
    assert not store.marker_path.exists()


def test_resident_scan_marks_trip_before_incident_and_cleans_after_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scan block必須先持久化trip marker，DB commit後仍等context關閉才清。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.profile_dir.mkdir(parents=True)
    with SqliteApplicationContext(paths.db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
    identity = load_or_create_managed_profile_identity(
        profiles_root=paths.profiles_dir,
        profile_dir=paths.profile_dir,
    )
    store = FacebookAutomationSessionGuardStore(
        paths.facebook_automation_session_guards_dir,
        profile_alias=derive_facebook_automation_profile_alias(
            identity.profile_scope_key
        ),
    )
    context = FakeAsyncBrowserContext()
    observations: list[str] = []

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    async def fake_launch(
        _playwright: object,
        _options: object,
    ) -> FakeAsyncBrowserContext:
        return context

    async def blocked_scan(**_kwargs: object) -> object:
        raise WorkerFailure(
            FACEBOOK_TEMPORARY_BLOCK_REASON,
            "blocked",
        )

    async def checking_incident(**kwargs: Any):
        before = store.read()
        assert before is not None
        assert before.state == FacebookAutomationSessionGuardState.TRIP_PENDING
        observations.append("before_commit")
        outcome = await record_facebook_access_incident_for_db_async(**kwargs)
        after = store.read()
        assert after is not None
        assert after.state == FacebookAutomationSessionGuardState.TRIP_PENDING
        observations.append("after_commit_before_context_close")
        return outcome

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main_executor_attempt."
        "record_facebook_access_incident_for_db_async",
        checking_incident,
    )

    async def run_test() -> None:
        await run_resident_main_loop(
            ResidentRuntimeOptions(
                db_path=paths.db_path,
                profile_dir=paths.profile_dir,
                interval_seconds=0,
                scheduler_tick_seconds=0,
                max_cycles=1,
            ),
            scan_page=as_async_scan_callable(blocked_scan),
            automation_sleep_fn=lambda _seconds: asyncio.sleep(0),
            automation_clock=lambda: _NOW,
        )

    asyncio.run(run_test())

    assert observations == ["before_commit", "after_commit_before_context_close"]
    assert context.closed
    assert not store.marker_path.exists()


def test_resident_reconciles_trip_pending_after_runtime_when_incident_write_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Incident persistence例外留下trip marker時，同process在context收斂後開hold。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.profile_dir.mkdir(parents=True)
    with SqliteApplicationContext(paths.db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="incident-write-failure",
                canonical_url="https://www.facebook.com/groups/incident-write-failure",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
    identity = load_or_create_managed_profile_identity(
        profiles_root=paths.profiles_dir,
        profile_dir=paths.profile_dir,
    )
    store = FacebookAutomationSessionGuardStore(
        paths.facebook_automation_session_guards_dir,
        profile_alias=derive_facebook_automation_profile_alias(
            identity.profile_scope_key
        ),
    )
    context = FakeAsyncBrowserContext()

    class FakePlaywrightManager:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def blocked_scan(**_kwargs: object) -> object:
        raise WorkerFailure(FACEBOOK_TEMPORARY_BLOCK_REASON, "blocked")

    async def failed_incident_write(**_kwargs: object) -> object:
        marker = store.read()
        assert marker is not None
        assert marker.state == FacebookAutomationSessionGuardState.TRIP_PENDING
        raise RuntimeError("injected incident persistence failure")

    async def fake_launch(*_args: object, **_kwargs: object) -> FakeAsyncBrowserContext:
        return context

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main_executor_attempt."
        "record_facebook_access_incident_for_db_async",
        failed_incident_write,
    )

    asyncio.run(
        run_resident_main_loop(
            ResidentRuntimeOptions(
                db_path=paths.db_path,
                profile_dir=paths.profile_dir,
                interval_seconds=0,
                scheduler_tick_seconds=0,
                max_cycles=1,
            ),
            scan_page=as_async_scan_callable(blocked_scan),
            automation_sleep_fn=lambda _seconds: asyncio.sleep(0),
            automation_clock=lambda: _NOW,
        )
    )

    assert context.closed
    assert store.read() is None
    with SqliteApplicationContext(paths.db_path) as app:
        circuit = app.services.facebook_access_circuit.get(identity.profile_scope_key)
    assert circuit is not None
    assert circuit.status == FacebookAccessCircuitStatus.OPEN
    assert circuit.reason_code == FACEBOOK_ACCESS_PERSISTENCE_UNCERTAIN_REASON


def _store(tmp_path: Path) -> FacebookAutomationSessionGuardStore:
    return FacebookAutomationSessionGuardStore(
        tmp_path / "data" / "facebook-automation-session-guards",
        profile_alias="profile-1",
    )


def _trip_store(
    tmp_path: Path,
    *,
    operation: FacebookProductOperationKind,
) -> FacebookAutomationSessionGuardStore:
    store = _store(tmp_path)
    store.start_normal_session(started_at=_NOW, session_id=_SESSION_ID)
    store.mark_trip_pending(
        session_id=_SESSION_ID,
        operation_kind=operation,
        trigger_action_kind=FacebookActionKind.DIRECT_DOCUMENT,
    )
    return store


def _initialize_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path):
        pass
    return db_path
