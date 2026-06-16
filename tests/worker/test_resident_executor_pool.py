"""Resident main worker tests。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any


from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
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


def test_resident_main_executor_keeps_third_target_queued(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """queue-based executor 會讓兩個 target running，第三個保持 queued。"""

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

    async def blocking_scan_page(**kwargs: Any) -> PostsScanSummary:
        """讓前兩個 worker 保持 running，方便檢查第三個 target queued。"""

        nonlocal active_count
        active_count += 1
        if active_count == 2:
            started.set()
        await release.wait()
        active_count -= 1
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
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
            scan_page=blocking_scan_page,
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
            assert summary.selected_count == 3
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
                == 1
            )
            release.set()
            await target_queue.join()
        finally:
            await executor.stop()

    asyncio.run(run_test())
    log_text = caplog.text
    assert "resident_executor_start max_concurrent_scans=2" in log_text
    assert "resident_target_enqueued target_id=" in log_text
    assert "resident_target_running target_id=" in log_text
    assert "resident_scheduler_tick cycle=1 selected=3" in log_text


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

        async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
            scan_started.set()
            return PostsScanSummary(
                target_id=kwargs["target"].id,
                url=kwargs["page"].url,
                item_count=0,
                new_count=0,
                matched_count=0,
                scan_run_id=1,
                round_stats=(),
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
            scan_page=fake_scan_page,
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
            scan_page=fake_scan_page,
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
            target_id="target-1",
            in_use_by_worker="worker-current",
            current_url="https://current.example",
        )
        pool.pages["target-1"] = ownership

        released = await pool.release_if_page_id(
            "target-1",
            "stale-page",
            current_url="https://stale-release.example",
        )
        reloaded_at = await pool.mark_reloaded_if_page_id(
            "target-1",
            "stale-page",
            current_url="https://stale-reload.example",
        )
        discarded = await pool.discard_if_page_id("target-1", "stale-page")

        assert released is False
        assert reloaded_at is None
        assert discarded is False
        assert pool.pages["target-1"] is ownership
        assert ownership.in_use_by_worker == "worker-current"
        assert ownership.current_url == "https://current.example"
        assert ownership.last_reloaded_at is None
        assert not page.closed

    asyncio.run(run_test())


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
            scan_page=unused_scan_page,
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

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
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
            scan_page=fake_scan_page,
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
