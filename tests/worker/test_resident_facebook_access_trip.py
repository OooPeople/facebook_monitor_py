from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from typing import NoReturn

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.facebook_temporary_block import FacebookActionKind
from facebook_monitor.core.facebook_temporary_block import FacebookProductOperationKind
from facebook_monitor.core.facebook_temporary_block import FacebookWorkSourceKind
from facebook_monitor.core.facebook_temporary_block import TemporaryBlockFinding
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.facebook_automation_runtime import FacebookAutomationRuntime
from facebook_monitor.worker.facebook_access_incident import FacebookAccessIncidentOutcome
from facebook_monitor.worker.facebook_access_incident import (
    FacebookAccessIncidentOutcomeKind,
)
from facebook_monitor.worker.resident_main import run_resident_main_scheduler_tick
from facebook_monitor.worker import resident_main_executor as resident_main_executor_module
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.scan_orchestration import FacebookPageGuardDiagnostics
from facebook_monitor.worker.scan_commit_guard import scan_commit_guard_from_runtime_state

from tests.worker.resident_main_test_helpers import as_async_scan_callable
from tests.worker.resident_main_test_helpers import build_success_scan_result_for_test
from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext


def _block_diagnostics() -> FacebookPageGuardDiagnostics:
    """建立不含頁面原文的 high-confidence detector diagnostics。"""

    return FacebookPageGuardDiagnostics(
        classification=FACEBOOK_TEMPORARY_BLOCK_REASON,
        facebook_host=True,
        matched_heading=True,
        matched_detail=True,
        article_count=0,
        stable_observation_count=2,
        body_text_length=64,
        url_kind="group_feed",
    )


def test_resident_temporary_block_pauses_all_and_cancels_peer_io(tmp_path: Path) -> None:
    """Leader writer 完成前先取消 peer，且 incident 原子停止全部 targets。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        targets = tuple(
            app.services.targets.upsert_group_posts_target(
                UpsertGroupPostsTargetRequest(
                    group_id=group_id,
                    canonical_url=f"https://www.facebook.com/groups/{group_id}",
                )
            )
            for group_id in ("111", "222")
        )
        for target in targets:
            app.services.targets.restart_target_monitoring(target.id)

    peer_started = asyncio.Event()
    peer_cancelled = asyncio.Event()
    blocked_target_id = targets[0].id

    async def scan_page(**kwargs: Any) -> NoReturn:
        target = kwargs["target"]
        if target.id == blocked_target_id:
            await peer_started.wait()
            raise WorkerFailure(
                FACEBOOK_TEMPORARY_BLOCK_REASON,
                "Facebook access blocked",
                diagnostics=_block_diagnostics(),
            )
        peer_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            peer_cancelled.set()
            raise
        raise AssertionError("peer wait completed unexpectedly")

    async def scenario() -> FacebookAutomationRuntime:
        runtime = FacebookAutomationRuntime()
        queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
                max_concurrent_scans=2,
            ),
            page_pool=page_pool,
            target_queue=queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(scan_page),
            facebook_runtime=runtime,
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
                facebook_runtime=runtime,
            )
            assert summary.selected_count == 2
            await asyncio.wait_for(queue.join(), timeout=2)
            assert executor.runtime_restart_requested()
        finally:
            await executor.stop(cancel_running=True, runtime_restart=True)
        return runtime

    runtime = asyncio.run(scenario())
    assert runtime.signal.is_tripped()
    assert peer_cancelled.is_set()

    with SqliteApplicationContext(db_path) as app:
        warning = app.services.facebook_temporary_block_warning.get()
        loaded_targets = tuple(app.repositories.targets.get(target.id) for target in targets)
        blocked_scan = app.repositories.scan_runs.latest_by_target(blocked_target_id)
        peer_scan = app.repositories.scan_runs.latest_by_target(targets[1].id)
        outbox = app.repositories.notification_outbox.list_pending()
    assert warning is not None
    assert all(target is not None and target.paused for target in loaded_targets)
    assert blocked_scan is not None
    assert blocked_scan.metadata["worker"] == "facebook_access_incident"
    assert peer_scan is None
    assert outbox == []


@pytest.mark.parametrize("trip_signal", (False, True))
def test_commit_ready_scan_result_respects_cancellation_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    trip_signal: bool,
) -> None:
    """普通 restart 保留 result；temporary-block trip 必須優先取消。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="peer-commit-ready",
                canonical_url="https://www.facebook.com/groups/peer-commit-ready",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        running = app.services.targets.mark_target_running(
            target.id,
            "peer-worker",
            page_id="peer-page",
        )
        config = app.services.targets.get_config_for_target(target)
    commit_guard = scan_commit_guard_from_runtime_state(running)

    async def scenario() -> None:
        runtime = FacebookAutomationRuntime()
        context = FakeAsyncBrowserContext()
        page = await context.new_page()
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=AsyncResidentPagePool(context),
            target_queue=TargetQueue(),
            schedule_planner=TargetSchedulePlanner(),
            scan_page=as_async_scan_callable(_unused_scan_page),
            facebook_runtime=runtime,
        )
        scan_result_ready = asyncio.Event()
        hold_wait_for = asyncio.Event()
        expected_result = build_success_scan_result_for_test(
            target=target,
            page_url=target.canonical_url,
        )

        async def peer_scan(**_kwargs: Any) -> object:
            return expected_result

        async def controlled_wait_for(awaitable: Any, *, timeout: float) -> object:
            assert timeout > 0
            result = await asyncio.shield(awaitable)
            scan_result_ready.set()
            await hold_wait_for.wait()
            return result

        monkeypatch.setattr(
            resident_main_executor_module.asyncio,
            "wait_for",
            controlled_wait_for,
        )
        with SqliteApplicationContext(db_path) as app:
            attempt = asyncio.create_task(
                executor._run_scan_with_heartbeat(
                    as_async_scan_callable(peer_scan),
                    page=page,
                    app=app,
                    target=target,
                    config=config,
                    scroll_rounds=0,
                    scroll_wait_ms=0,
                    worker_id=commit_guard.worker_id,
                    page_id=commit_guard.page_id,
                    commit_guard=commit_guard,
                )
            )
            await scan_result_ready.wait()
            if trip_signal:
                assert runtime.signal.try_trip()
            attempt.cancel()
            if trip_signal:
                with pytest.raises(asyncio.CancelledError):
                    await attempt
            else:
                assert await attempt is expected_result

    async def _unused_scan_page(**_kwargs: Any) -> NoReturn:
        raise AssertionError("executor default scanner must not run")

    asyncio.run(scenario())


def test_async_incident_db_failure_trips_runtime_without_fake_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writer failure 必須傳播，trip 不得被包裝成已保存 warning。"""

    runtime = FacebookAutomationRuntime()

    async def fail_writer(**_kwargs: Any) -> object:
        raise OSError("storage unavailable")

    monkeypatch.setattr(
        "facebook_monitor.worker.facebook_automation_runtime."
        "record_facebook_access_incident_for_db_async",
        fail_writer,
    )

    async def scenario() -> None:
        with pytest.raises(OSError, match="storage unavailable"):
            await runtime.record_temporary_block(
                db_path=tmp_path / "app.db",
                finding=TemporaryBlockFinding(
                    source_kind=FacebookWorkSourceKind.METADATA,
                    operation_kind=FacebookProductOperationKind.GROUP_METADATA_ACCESS,
                    action_kind=FacebookActionKind.GROUP_DOCUMENT,
                ),
            )

    asyncio.run(scenario())
    assert runtime.signal.is_tripped()
    assert not (tmp_path / "app.db").exists()


def test_incident_writer_is_shielded_while_peers_and_leader_are_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Peer 立即取消；leader 外部取消必須延後到 durable writer terminal。"""

    async def scenario() -> None:
        runtime = FacebookAutomationRuntime()
        writer_started = asyncio.Event()
        allow_writer = asyncio.Event()
        writer_completed = asyncio.Event()
        peer_cancelled = asyncio.Event()

        async def controlled_writer(**_kwargs: Any) -> FacebookAccessIncidentOutcome:
            writer_started.set()
            await allow_writer.wait()
            writer_completed.set()
            return FacebookAccessIncidentOutcome(
                kind=FacebookAccessIncidentOutcomeKind.RECORDED,
            )

        monkeypatch.setattr(
            "facebook_monitor.worker.facebook_automation_runtime."
            "record_facebook_access_incident_for_db_async",
            controlled_writer,
        )

        async def peer() -> None:
            with runtime.facebook_work():
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    peer_cancelled.set()
                    raise

        async def leader() -> None:
            with runtime.facebook_work():
                await runtime.record_temporary_block(
                    db_path=tmp_path / "app.db",
                    finding=TemporaryBlockFinding(
                        source_kind=FacebookWorkSourceKind.METADATA,
                        operation_kind=FacebookProductOperationKind.GROUP_METADATA_ACCESS,
                        action_kind=FacebookActionKind.GROUP_DOCUMENT,
                    ),
                )

        peer_task = asyncio.create_task(peer())
        await asyncio.sleep(0)
        leader_task = asyncio.create_task(leader())
        await writer_started.wait()
        await peer_cancelled.wait()
        leader_task.cancel()
        await asyncio.sleep(0)
        assert not leader_task.done()
        assert not writer_completed.is_set()
        allow_writer.set()
        with pytest.raises(asyncio.CancelledError):
            await leader_task
        await asyncio.gather(peer_task, return_exceptions=True)
        assert writer_completed.is_set()

    asyncio.run(scenario())
