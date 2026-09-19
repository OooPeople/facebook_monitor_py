from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.facebook_access import FacebookAccessCircuitStatus
from facebook_monitor.core.facebook_access import FacebookAccessBlockSignal
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookAdmissionToken
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookWorkSourceKind
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.facebook_access_runtime_gate import FacebookAccessRuntimeGate
from facebook_monitor.worker.facebook_automation_admission import (
    FacebookAutomationAdmissionController,
)
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationCoordinator,
)
from facebook_monitor.worker.resident_main import run_resident_main_scheduler_tick
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.scan_orchestration import FacebookPageGuardDiagnostics

from tests.worker.resident_main_test_helpers import as_async_scan_callable
from tests.worker.resident_main_test_helpers import build_success_scan_result_for_test
from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext


def test_resident_temporary_block_trips_global_circuit_without_normal_finalize(
    tmp_path: Path,
) -> None:
    """正式 resident 命中 block 後只走 incident transaction 並要求關閉 runtime。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
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

    diagnostics = FacebookPageGuardDiagnostics(
        classification=FACEBOOK_TEMPORARY_BLOCK_REASON,
        facebook_host=True,
        matched_heading=True,
        matched_detail=True,
        article_count=0,
        stable_observation_count=2,
        body_text_length=64,
        url_kind="group_feed",
    )

    async def blocked_scan(**_kwargs: Any) -> object:
        raise WorkerFailure(
            FACEBOOK_TEMPORARY_BLOCK_REASON,
            "Facebook access blocked",
            diagnostics=diagnostics,
        )

    async def scenario() -> None:
        gate = FacebookAccessRuntimeGate()
        coordinator = FacebookAutomationCoordinator(
            quiet_gap_min_seconds=0,
            quiet_gap_max_seconds=0,
        )
        controller = FacebookAutomationAdmissionController(
            db_path=db_path,
            profile_scope_key="profile-scope",
            runtime_gate=gate,
            coordinator=coordinator,
            persistent_quiet_gap_seconds=0,
        )
        queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(
            FakeAsyncBrowserContext(),
            max_open_pages=1,
            retain_idle_pages=False,
        )
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=page_pool,
            target_queue=queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(blocked_scan),
            automation_coordinator=coordinator,
            automation_admission_controller=controller,
        )
        await executor.start()
        try:
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                page_pool=page_pool,
                target_queue=queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
                automation_coordinator=coordinator,
                automation_admission_controller=controller,
            )
            assert summary.selected_count == 1
            await queue.join()
        finally:
            await executor.stop(cancel_running=True, runtime_restart=True)

        assert gate.snapshot().writes_closed
        assert executor.runtime_restart_requested()

    asyncio.run(scenario())

    with SqliteApplicationContext(db_path) as app:
        circuit = app.services.facebook_access_circuit.get("profile-scope")
        runtime = app.repositories.runtime_states.get(target.id)
        latest = app.repositories.scan_runs.latest_by_target(target.id)
        outbox = app.repositories.notification_outbox.list_pending()
    assert circuit is not None
    assert circuit.status == FacebookAccessCircuitStatus.OPEN
    assert runtime is not None
    assert runtime.runtime_status == TargetRuntimeStatus.IDLE
    assert runtime.consecutive_failure_reason == "previous_failure"
    assert runtime.consecutive_failure_count == 2
    assert latest is not None
    assert latest.metadata["worker"] == "facebook_access_incident"
    assert outbox == []


def test_resident_stale_success_cannot_commit_after_external_circuit_trip(
    tmp_path: Path,
) -> None:
    """掃描完成前若 DB generation 已被其他來源推進，成功結果必須整批丟棄。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def stale_success_scan(**kwargs: Any) -> object:
        app = kwargs["app"]
        circuit = app.services.facebook_access_circuit.get("profile-scope")
        assert circuit is not None
        trip = app.services.facebook_access_circuit.trip(
            FacebookAccessBlockSignal(
                admission_token=FacebookAdmissionToken(
                    profile_scope_key="profile-scope",
                    db_generation=circuit.generation,
                    process_safety_epoch=0,
                    operation_id="external-operation",
                ),
                source_kind=FacebookWorkSourceKind.METADATA,
                operation_kind=FacebookProductOperationKind.GROUP_METADATA_ACCESS,
                trigger_action_kind=FacebookActionKind.GROUP_DOCUMENT,
                source_owner_token="external-owner",
            ),
            source_owner_is_valid=True,
        )
        assert trip.state.status == FacebookAccessCircuitStatus.OPEN
        return build_success_scan_result_for_test(
            target=kwargs["target"],
            page_url=kwargs["page"].url,
            item_key="must-not-commit",
        )

    async def scenario() -> None:
        gate = FacebookAccessRuntimeGate()
        coordinator = FacebookAutomationCoordinator(
            quiet_gap_min_seconds=0,
            quiet_gap_max_seconds=0,
        )
        controller = FacebookAutomationAdmissionController(
            db_path=db_path,
            profile_scope_key="profile-scope",
            runtime_gate=gate,
            coordinator=coordinator,
            persistent_quiet_gap_seconds=0,
        )
        queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(
            FakeAsyncBrowserContext(),
            max_open_pages=1,
            retain_idle_pages=False,
        )
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=page_pool,
            target_queue=queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(stale_success_scan),
            automation_coordinator=coordinator,
            automation_admission_controller=controller,
        )
        await executor.start()
        try:
            await run_resident_main_scheduler_tick(
                options=executor.options,
                page_pool=page_pool,
                target_queue=queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
                automation_coordinator=coordinator,
                automation_admission_controller=controller,
            )
            await queue.join()
            assert executor.runtime_restart_requested()
        finally:
            await executor.stop(cancel_running=True, runtime_restart=True)

    asyncio.run(scenario())

    with SqliteApplicationContext(db_path) as app:
        assert app.repositories.scan_runs.latest_by_target(target.id) is None
        assert app.repositories.latest_scan_items.list_by_target(target.id) == []
        assert app.repositories.notification_outbox.list_pending() == []
