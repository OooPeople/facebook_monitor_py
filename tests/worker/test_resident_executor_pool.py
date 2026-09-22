"""Resident main worker tests。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.scheduler.planner import DueTarget
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.resident_main import run_resident_main_scheduler_tick
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_page_pool import PageOwnership
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions


from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext
from tests.worker.resident_main_test_helpers import FakeAsyncPage
from tests.worker.resident_main_test_helpers import as_async_scan_callable
from tests.worker.resident_main_test_helpers import build_success_scan_result_for_test


def _seed_target_durable_sentinel(
    app: ApplicationContext,
    target: TargetDescriptor,
) -> tuple[str, NotificationOutboxStatus]:
    """建立既有 scan/history/notification rows，供 no-write regression 比對。"""

    recorded_at = utc_now()
    item_key = "post:preexisting-sentinel"
    scan_run_id = app.repositories.scan_runs.add(
        ScanRun(
            target_id=target.id,
            status=ScanStatus.SUCCESS,
            started_at=recorded_at,
            finished_at=recorded_at,
            item_count=1,
            matched_count=1,
            metadata={"sentinel": True},
        )
    )
    app.repositories.latest_scan_items.replace_for_target(
        target.id,
        (
            LatestScanItem(
                target_id=target.id,
                scan_run_id=scan_run_id,
                item_kind=ItemKind.POST,
                item_key=item_key,
                item_index=0,
                text="preexisting latest item",
                scanned_at=recorded_at,
            ),
        ),
    )
    app.repositories.match_history.add(
        MatchHistoryEntry(
            target_id=target.id,
            group_id=target.group_id,
            item_kind=ItemKind.POST,
            item_key=item_key,
            text="preexisting history",
            include_rule="sentinel",
            recorded_at=recorded_at,
            created_at=recorded_at,
        )
    )
    notification_event_id = app.repositories.notification_events.add(
        NotificationEvent(
            target_id=target.id,
            item_key=item_key,
            channel=NotificationChannel.NTFY,
            status=NotificationStatus.FAILED,
            message="preexisting notification event",
            source_scan_run_id=scan_run_id,
            created_at=recorded_at,
        )
    )
    outbox_key = f"{target.id}:preexisting-sentinel:ntfy"
    outbox_status = NotificationOutboxStatus.FAILED
    app.repositories.notification_outbox.enqueue(
        NotificationOutboxEntry(
            idempotency_key=outbox_key,
            target_id=target.id,
            item_key=item_key,
            item_kind=ItemKind.POST,
            channel=NotificationChannel.NTFY,
            title="preexisting outbox",
            message="preexisting outbox message",
            endpoint="preexisting-topic",
            source_scan_run_id=scan_run_id,
            status=outbox_status,
            attempts=2,
            last_error="preexisting failure",
            notification_event_id=notification_event_id,
            created_at=recorded_at,
            updated_at=recorded_at,
        )
    )
    return outbox_key, outbox_status


def _snapshot_target_durable_rows(
    app: ApplicationContext,
    target_id: str,
) -> dict[str, tuple[tuple[object, ...], ...]]:
    """逐 row 保存 target visible scan/notification durable state。"""

    connection = app.repositories.scan_runs.connection
    queries = {
        "scan_runs": "SELECT * FROM scan_runs WHERE target_id = ? ORDER BY rowid",
        "latest_scan_items": (
            "SELECT * FROM latest_scan_items WHERE target_id = ? ORDER BY rowid"
        ),
        "match_history": (
            "SELECT * FROM match_history WHERE target_id = ? ORDER BY rowid"
        ),
        "notification_events": (
            "SELECT * FROM notification_events WHERE target_id = ? ORDER BY rowid"
        ),
        "notification_outbox": (
            "SELECT * FROM notification_outbox WHERE target_id = ? ORDER BY rowid"
        ),
    }
    return {
        table: tuple(
            tuple(row)
            for row in connection.execute(query, (target_id,)).fetchall()
        )
        for table, query in queries.items()
    }


def test_resident_main_scheduler_preserves_configured_worker_slots_before_pacing(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """executor 不得把設定的 worker slots 偷偷降為單一 page budget。"""

    caplog.set_level(logging.INFO, logger="facebook_monitor.worker")
    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        targets = [
            app.services.targets.upsert_group_posts_target(
                UpsertGroupPostsTargetRequest(
                    group_id=str(index),
                    canonical_url=f"https://www.facebook.com/groups/{index}",
                )
            )
            for index in (111, 222, 333)
        ]
        for target in targets:
            app.services.targets.restart_target_monitoring(target.id)

    started = asyncio.Event()
    release = asyncio.Event()
    active_count = 0

    async def blocking_scan_page(**kwargs: Any) -> object:
        """讓設定數量的 targets 保持 running，方便檢查 DB owner 數。"""

        nonlocal active_count
        active_count += 1
        if active_count == 2:
            started.set()
        await release.wait()
        active_count -= 1
        return build_success_scan_result_for_test(
            target=kwargs["target"],
            page_url=kwargs["page"].url,
        )

    async def run_test() -> None:
        target_queue = TargetQueue()
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
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(blocking_scan_page),
        )
        await executor.start()
        try:
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            with SqliteApplicationContext(db_path) as app:
                states = [app.repositories.runtime_states.get(target.id) for target in targets]
            assert summary.selected_count == 2
            assert (
                sum(
                    1
                    for state in states
                    if state is not None and state.runtime_status == TargetRuntimeStatus.RUNNING
                )
                == 2
            )
            assert (
                sum(
                    1
                    for state in states
                    if state is not None and state.runtime_status == TargetRuntimeStatus.QUEUED
                )
                == 0
            )
            release.set()
            await target_queue.join()
        finally:
            await executor.stop()

    asyncio.run(run_test())
    log_text = caplog.text
    assert (
        "resident_executor_start configured_max_concurrent_scans=2 "
        "effective_max_concurrent_scans=2"
    ) in log_text
    assert "resident_target_enqueued target_id=" in log_text
    assert "resident_target_running target_id=" in log_text
    assert "resident_scheduler_tick cycle=1 selected=2" in log_text


def test_resident_enqueue_publishes_item_after_runtime_queued(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """worker 不可早於 runtime queued 寫入前取得 queue item。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def run_test() -> None:
        target_queue = TargetQueue()
        db_mark_started = asyncio.Event()
        release_db_mark = asyncio.Event()
        scan_started = asyncio.Event()

        async def fake_scan_page(**kwargs: Any) -> object:
            scan_started.set()
            return build_success_scan_result_for_test(
                target=kwargs["target"],
                page_url=kwargs["page"].url,
            )

        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=60,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            target_queue=target_queue,
            schedule_planner=TargetSchedulePlanner(),
            scan_page=as_async_scan_callable(fake_scan_page),
        )
        original_run_db_operation = executor._run_db_operation_with_retry  # noqa: SLF001
        async def delayed_db_operation(operation_name: str, operation: Any) -> Any:
            if operation_name == "mark_target_queued":
                db_mark_started.set()
                await release_db_mark.wait()
            return await original_run_db_operation(operation_name, operation)

        monkeypatch.setattr(
            executor,
            "_run_db_operation_with_retry",
            delayed_db_operation,
        )

        await executor.start()
        enqueue_task: asyncio.Task[int] | None = None
        try:
            due_target = DueTarget(
                target_id=target.id,
                interval_seconds=60,
                due_at=utc_now(),
            )
            enqueue_task = asyncio.create_task(executor.enqueue_due_targets((due_target,)))
            await asyncio.wait_for(db_mark_started.wait(), timeout=1)
            await asyncio.sleep(0)
            assert not scan_started.is_set()

            release_db_mark.set()
            assert await asyncio.wait_for(enqueue_task, timeout=1) == 1
            await asyncio.wait_for(scan_started.wait(), timeout=1)
            await target_queue.join()
        finally:
            release_db_mark.set()
            if enqueue_task is not None and not enqueue_task.done():
                enqueue_task.cancel()
                await asyncio.gather(enqueue_task, return_exceptions=True)
            await executor.stop(cancel_running=True)

    asyncio.run(run_test())


def test_resident_enqueue_releases_reserved_item_when_db_admission_rejected(
    tmp_path: Path,
) -> None:
    """DB admission 未實際 queued 時，不可 publish reserved queue item。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="queue-guard-error",
                canonical_url="https://www.facebook.com/groups/queue-guard-error",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.mark_target_error(
            target.id,
            "terminal failure",
            failure_reason="target_invalid",
            failure_count=3,
        )
        requested = app.services.targets.request_target_scan(target.id)

    async def run_test() -> None:
        target_queue = TargetQueue()

        async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
            raise AssertionError("DB rejected queue admission should not publish to worker")

        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=60,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            target_queue=target_queue,
            schedule_planner=TargetSchedulePlanner(),
            scan_page=as_async_scan_callable(fake_scan_page),
        )
        due_target = DueTarget(
            target_id=target.id,
            interval_seconds=60,
            due_at=utc_now(),
            scan_requested=True,
            scan_requested_at=requested.scan_requested_at,
        )

        enqueued = await executor.enqueue_due_targets((due_target,))
        queued_count, running_count, queued_ids = await target_queue.snapshot()
        counters = await executor.take_counters()
        with SqliteApplicationContext(db_path) as app:
            loaded = app.repositories.runtime_states.get(target.id)

        assert enqueued == 0
        assert (queued_count, running_count, queued_ids) == (0, 0, ())
        assert counters.skipped_count == 1
        assert loaded is not None
        assert loaded.runtime_status == TargetRuntimeStatus.ERROR
        assert loaded.last_error == "terminal failure"
        assert loaded.scan_requested_at == requested.scan_requested_at
        assert "runtime_queue_guard_rejected" in loaded.last_skip_reason

    asyncio.run(run_test())


def test_resident_page_pool_page_id_guards_ignore_stale_attempt() -> None:
    """page pool stale page_id 不可釋放、覆寫或關閉目前 page ownership。"""

    async def run_test() -> None:
        page = FakeAsyncPage()
        pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        ownership = PageOwnership(
            page=page,
            page_id="current-page",
            in_use_by_worker="worker-current",
        )
        pool.pages["target-1"] = ownership

        released = await pool.release_if_page_id(
            "target-1",
            "stale-page",
        )
        reloaded_at = await pool.mark_reloaded_if_page_id(
            "target-1",
            "stale-page",
        )
        discarded = await pool.discard_if_page_id("target-1", "stale-page")
        matching_reloaded_at = await pool.mark_reloaded_if_page_id(
            "target-1",
            "current-page",
        )

        assert released is False
        assert reloaded_at is None
        assert discarded is False
        assert matching_reloaded_at is not None
        utc_offset = matching_reloaded_at.utcoffset()
        assert utc_offset is not None
        assert utc_offset.total_seconds() == 0
        assert pool.pages["target-1"] is ownership
        assert ownership.in_use_by_worker == "worker-current"
        assert not page.closed

    asyncio.run(run_test())


@pytest.mark.parametrize(
    "replace_runtime_owner",
    (False, True),
    ids=("matching-db-owner", "new-db-owner"),
)
def test_resident_page_reload_owner_change_stops_before_scan_and_cleans_up_safely(
    tmp_path: Path,
    monkeypatch: Any,
    caplog: Any,
    replace_runtime_owner: bool,
) -> None:
    """page pool owner 改變後中止掃描，且只釋放仍相符的 DB owner。"""

    caplog.set_level(
        logging.INFO,
        logger="facebook_monitor.worker.resident_main_executor_attempt",
    )
    db_path = tmp_path / "app.db"
    previous_finished_at = utc_now()
    previous_heartbeat_at = utc_now()
    previous_reloaded_at = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="page-owner-changed",
                canonical_url="https://www.facebook.com/groups/page-owner-changed",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        requested = app.repositories.runtime_states.get(target.id)
        assert requested is not None
        assert requested.scan_requested_at is not None
        outbox_key, outbox_status = _seed_target_durable_sentinel(app, target)
        durable_rows_before = _snapshot_target_durable_rows(app, target.id)
        assert all(durable_rows_before.values())

    replacement_page = FakeAsyncPage()
    replacement_ownership = PageOwnership(
        page=replacement_page,
        page_id="replacement-page",
        in_use_by_worker="replacement-worker",
    )
    new_runtime_states: list[TargetRuntimeState] = []
    newer_scan_requests: list[datetime] = []
    scan_started = asyncio.Event()
    operation_names: list[str] = []

    async def fake_scan_page(**kwargs: Any) -> object:
        scan_started.set()
        return build_success_scan_result_for_test(
            target=kwargs["target"],
            page_url=kwargs["page"].url,
        )

    async def run_test() -> None:
        target_queue = TargetQueue()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=60,
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=TargetSchedulePlanner(),
            scan_page=as_async_scan_callable(fake_scan_page),
        )
        original_run_db_operation = executor._run_db_operation_with_retry  # noqa: SLF001
        original_add_counters = executor._add_counters  # noqa: SLF001
        counters_recorded = asyncio.Event()

        async def recording_db_operation(operation_name: str, operation: Any) -> Any:
            operation_names.append(operation_name)
            return await original_run_db_operation(operation_name, operation)

        async def recording_add_counters(counters: Any) -> None:
            await original_add_counters(counters)
            counters_recorded.set()

        async def replace_page_owner(target_id: str, page_id: str) -> None:
            with SqliteApplicationContext(db_path) as app:
                running = app.repositories.runtime_states.get(target_id)
                assert running is not None
                assert running.last_started_at is not None
                assert running.active_page_id == page_id
                if replace_runtime_owner:
                    released = (
                        app.services.targets.guarded_mark_scheduler_cancellation_idle(
                            target_id,
                            worker_id=running.active_worker_id,
                            started_at=running.last_started_at,
                            page_id=page_id,
                        )
                    )
                    assert released is not None
                    app.services.targets.mark_target_queued(target_id, "manual")
                    new_runtime = app.services.targets.try_claim_target_running(
                        target_id,
                        "replacement-worker",
                        page_id="replacement-page",
                    )
                    assert new_runtime is not None
                    new_runtime_states.append(new_runtime)
                else:
                    running = app.services.targets.request_target_scan(target_id)
                    assert running.scan_requested_at is not None
                    newer_scan_requests.append(running.scan_requested_at)
                    app.repositories.runtime_states.save(
                        replace(
                            running,
                            last_finished_at=previous_finished_at,
                            last_heartbeat_at=previous_heartbeat_at,
                            last_page_reloaded_at=previous_reloaded_at,
                            last_error="prior error",
                            last_skip_reason="prior skip",
                            consecutive_failure_reason="page_load_timeout",
                            consecutive_failure_count=2,
                            consecutive_scan_skip_reason="prior scan skip",
                            consecutive_scan_skip_count=1,
                        )
                    )
            async with page_pool.lock:
                page_pool.pages[target_id] = replacement_ownership

        monkeypatch.setattr(
            executor,
            "_run_db_operation_with_retry",
            recording_db_operation,
        )
        monkeypatch.setattr(executor, "_add_counters", recording_add_counters)
        monkeypatch.setattr(
            page_pool,
            "mark_reloaded_if_page_id",
            replace_page_owner,
        )

        await executor.start()
        try:
            due_target = DueTarget(
                target_id=target.id,
                interval_seconds=60,
                due_at=utc_now(),
                scan_requested=True,
                scan_requested_at=requested.scan_requested_at,
            )
            assert await executor.enqueue_due_targets((due_target,)) == 1
            await target_queue.join()
            await asyncio.wait_for(counters_recorded.wait(), timeout=1)

            queued_count, running_count, queued_ids = await target_queue.snapshot()
            counters = await executor.take_counters()
            assert (queued_count, running_count, queued_ids) == (0, 0, ())
            assert counters.skipped_count == 1
            assert counters.success_count == 0
            assert counters.failure_count == 0
            assert executor._active_attempt_tasks == {}  # noqa: SLF001
            assert executor._active_scan_tasks == {}  # noqa: SLF001
            assert page_pool.pages[target.id] is replacement_ownership
            assert replacement_ownership.in_use_by_worker == "replacement-worker"
            assert not replacement_page.closed
        finally:
            await executor.stop()

    asyncio.run(run_test())

    assert not scan_started.is_set()
    assert "guarded_mark_target_page_reloaded" not in operation_names
    assert operation_names.count(
        "guarded_release_target_for_page_reload_owner_change"
    ) == 1
    assert "reason=page_reload_owner_changed" in caplog.text
    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        durable_rows_after = _snapshot_target_durable_rows(app, target.id)
        outbox_entry = app.repositories.notification_outbox.get_by_idempotency_key(
            outbox_key
        )

    assert state is not None
    if replace_runtime_owner:
        assert len(new_runtime_states) == 1
        assert state == new_runtime_states[0]
        assert state.runtime_status == TargetRuntimeStatus.RUNNING
        assert state.active_worker_id == "replacement-worker"
        assert state.active_page_id == "replacement-page"
    else:
        assert len(newer_scan_requests) == 1
        assert state.runtime_status == TargetRuntimeStatus.IDLE
        assert state.active_worker_id == ""
        assert state.active_page_id == ""
        assert state.enqueue_reason == ""
        assert state.scan_requested_at == newer_scan_requests[0]
        assert state.last_finished_at == previous_finished_at
        assert state.last_heartbeat_at == previous_heartbeat_at
        assert state.last_page_reloaded_at == previous_reloaded_at
        assert state.last_error == "prior error"
        assert state.last_skip_reason == "prior skip"
        assert state.consecutive_failure_reason == "page_load_timeout"
        assert state.consecutive_failure_count == 2
        assert state.consecutive_scan_skip_reason == "prior scan skip"
        assert state.consecutive_scan_skip_count == 1
    assert durable_rows_after == durable_rows_before
    assert outbox_entry is not None
    assert outbox_entry.idempotency_key == outbox_key
    assert outbox_entry.status == outbox_status


def test_resident_main_executor_requests_restart_when_worker_exits_unexpectedly(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """executor worker slot 非預期結束時會要求重建 runtime。"""

    caplog.set_level(
        logging.ERROR,
        logger="facebook_monitor.worker.resident_main_executor",
    )

    async def unused_scan_page(**_kwargs: Any) -> PostsScanSummary:
        """本測試不會實際掃描 target。"""

        raise AssertionError("scan should not run")

    async def run_test() -> None:
        target_queue = TargetQueue()
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=tmp_path / "app.db",
                profile_dir=tmp_path / "profile",
                max_concurrent_scans=1,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            target_queue=target_queue,
            schedule_planner=TargetSchedulePlanner(),
            scan_page=as_async_scan_callable(unused_scan_page),
        )
        await executor.start()
        try:
            await target_queue.stop_worker()
            await asyncio.wait_for(executor.worker_tasks[0], timeout=1)
            await asyncio.sleep(0)

            assert executor.runtime_restart_requested()
            assert not executor.worker_health_ok()
        finally:
            await executor.stop(runtime_restart=True)

    asyncio.run(run_test())
    assert (
        "resident_executor_worker_stopped worker_id=resident-slot-1 reason=returned_unexpectedly"
    ) in caplog.text


def test_resident_main_executor_requests_restart_when_worker_task_raises(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """worker task 若在清理階段噴例外，必須記錄並要求 runtime restart。"""

    db_path = tmp_path / "app.db"
    caplog.set_level(
        logging.ERROR,
        logger="facebook_monitor.worker.resident_main_executor",
    )
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    class CompleteFailsTargetQueue(TargetQueue):
        """測試用 queue：模擬 worker cleanup 發生非預期例外。"""

        async def complete(self, target_id: str, owner_key: str = "") -> None:
            await super().complete(target_id, owner_key=owner_key)
            raise RuntimeError("queue complete failed")

    async def fake_scan_page(**kwargs: Any) -> object:
        return build_success_scan_result_for_test(
            target=kwargs["target"],
            page_url=kwargs["page"].url,
        )

    async def run_test() -> None:
        target_queue = CompleteFailsTargetQueue()
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                max_concurrent_scans=1,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            target_queue=target_queue,
            schedule_planner=TargetSchedulePlanner(),
            scan_page=as_async_scan_callable(fake_scan_page),
        )
        await executor.start()
        try:
            due_target = DueTarget(
                target_id=target.id,
                interval_seconds=60,
                due_at=utc_now(),
            )
            assert await executor.enqueue_due_targets((due_target,)) == 1
            await asyncio.wait_for(executor.wait_runtime_restart_requested(), timeout=1)
            done, _pending = await asyncio.wait(executor.worker_tasks, timeout=1)
            assert executor.worker_tasks[0] in done
            assert not executor.worker_health_ok()
            assert executor.worker_statuses() == ("resident-slot-1:failed:RuntimeError",)
        finally:
            await executor.stop(runtime_restart=True)

    asyncio.run(run_test())
    assert (
        "resident_executor_worker_stopped worker_id=resident-slot-1 "
        "reason=exception exception_class=RuntimeError"
    ) in caplog.text
