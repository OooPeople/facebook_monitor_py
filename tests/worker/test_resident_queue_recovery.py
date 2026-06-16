"""Resident main worker tests。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any


from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.scheduler.planner import DueTarget
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.resident_main import run_resident_main_scheduler_tick
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker import resident_main_executor_attempt as attempt_module
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import QueueItem
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.scan_finalize import record_skipped_scan


from tests.worker.resident_main_test_helpers import FakeAsyncPage
from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext
from tests.worker.resident_main_test_helpers import RecordingSchedulePlanner
from tests.worker.resident_main_cycle_harness import (
    run_resident_main_cycle_harness as run_resident_main_cycle,
)


def test_target_queue_snapshot_keeps_enqueue_order() -> None:
    """TargetQueue diagnostics 應保留排隊順序，供 runtime 診斷使用。"""

    async def run_test() -> None:
        """建立三筆 queue item 並檢查 snapshot 順序。"""

        target_queue = TargetQueue()
        for target_id in ("target-a", "target-b", "target-c"):
            accepted = await target_queue.enqueue(
                QueueItem(
                    due_target=DueTarget(
                        target_id=target_id,
                        interval_seconds=60,
                        due_at=utc_now(),
                        scan_requested=False,
                    ),
                    enqueue_reason="due",
                    enqueued_at=utc_now(),
                )
            )
            assert accepted
        queued_count, running_count, queued_ids = await target_queue.snapshot()
        assert queued_count == 3
        assert running_count == 0
        assert queued_ids == ("target-a", "target-b", "target-c")

    asyncio.run(run_test())


def test_target_queue_old_owner_complete_does_not_clear_new_attempt() -> None:
    """舊 attempt complete 不可移除新 attempt 尚未 bind 的 running guard。"""

    async def run_test() -> None:
        target_queue = TargetQueue()
        first_item = QueueItem(
            due_target=DueTarget(
                target_id="target-a",
                interval_seconds=60,
                due_at=utc_now(),
            ),
            enqueue_reason="due",
            enqueued_at=utc_now(),
        )
        assert await target_queue.enqueue(first_item)
        assert await target_queue.get() is not None
        await target_queue.bind_running_owner("target-a", "old-owner")
        assert await target_queue.release_running_if_owner("target-a", "old-owner")

        second_item = QueueItem(
            due_target=DueTarget(
                target_id="target-a",
                interval_seconds=60,
                due_at=utc_now(),
            ),
            enqueue_reason="retry",
            enqueued_at=utc_now(),
        )
        assert await target_queue.enqueue(second_item)
        assert await target_queue.get() is not None

        await target_queue.complete("target-a", owner_key="old-owner")
        _queued_count, running_count, _queued_ids = await target_queue.snapshot()
        assert running_count == 1

        await target_queue.bind_running_owner("target-a", "new-owner")
        await target_queue.complete("target-a", owner_key="new-owner")
        _queued_count, running_count, _queued_ids = await target_queue.snapshot()
        assert running_count == 0

    asyncio.run(run_test())


def test_async_resident_dispatches_schedule_after_running_lock(tmp_path: Path) -> None:
    """async resident 進 queue 時不推進 next_due_at，取得 running lock 後才推進。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

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
        target_queue = TargetQueue()
        planner = RecordingSchedulePlanner()
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=60,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=fake_scan_page,
        )
        due_target = DueTarget(
            target_id=target.id,
            interval_seconds=60,
            due_at=utc_now(),
        )
        enqueued_count = await executor.enqueue_due_targets((due_target,))
        assert enqueued_count == 1
        assert planner.dispatched_target_ids == []
        with SqliteApplicationContext(db_path) as app:
            queued_state = app.repositories.runtime_states.get(target.id)
        assert queued_state is not None
        assert queued_state.runtime_status == TargetRuntimeStatus.QUEUED

        item = await target_queue.get()
        assert item is not None
        result = await executor._run_queue_item("worker-1", item)  # noqa: SLF001
        assert result.success
        assert planner.dispatched_target_ids == [target.id]

    asyncio.run(run_test())


def test_resident_pre_admission_failure_does_not_force_runtime_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """claim running 前失敗不可用 commit_guard=None 覆寫 runtime。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="pre-admission-failure",
                canonical_url="https://www.facebook.com/groups/pre-admission-failure",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    def fail_before_running(*_args: object, **_kwargs: object) -> object:
        raise WorkerFailure("pre_admission_failure", "failed before running claim")

    monkeypatch.setattr(attempt_module, "load_resident_target", fail_before_running)

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        raise AssertionError("scan should not run before target admission")

    summary = asyncio.run(
        run_resident_main_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            schedule_planner=TargetSchedulePlanner(),
            cycle_index=1,
            scan_page=fake_scan_page,
        )
    )

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert summary.skipped_count == 1
    assert summary.failure_count == 0
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.last_error == ""
    assert latest_scan is None


def test_stale_recovery_cancels_attempt_stuck_in_page_prepare(tmp_path: Path) -> None:
    """target restart recovery 應取消卡在 goto/reload 的整個 attempt。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    goto_started = asyncio.Event()
    goto_cancelled = asyncio.Event()

    class BlockingPreparePage(FakeAsyncPage):
        """第一個 page 會卡在 goto，直到 attempt 被 recovery 取消。"""

        async def goto(self, url: str, wait_until: str, timeout: float) -> None:
            self.url = url.rstrip("/")
            self.goto_count += 1
            goto_started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                goto_cancelled.set()
                raise

    class FirstPageBlocksContext(FakeAsyncBrowserContext):
        """第一個 page 卡住，後續 page 正常完成。"""

        async def new_page(self) -> FakeAsyncPage:
            page: FakeAsyncPage
            if not self.pages:
                page = BlockingPreparePage()
            else:
                page = FakeAsyncPage()
            self.pages.append(page)
            return page

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        finalize_result = record_skipped_scan(
            app=kwargs["app"],
            target=kwargs["target"],
            metadata={
                "worker": "resident_main",
                "skip_reason": SORT_ADJUST_UNCONFIRMED_REASON,
            },
            commit_guard=kwargs["commit_guard"],
        )
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=finalize_result.scan_run_id,
            round_stats=(),
        )

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FirstPageBlocksContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
                max_concurrent_scans=1,
                stale_running_after_seconds=180,
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=fake_scan_page,
        )
        await executor.start()
        try:
            await run_resident_main_scheduler_tick(
                options=executor.options,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
            await asyncio.wait_for(goto_started.wait(), timeout=1)
            with SqliteApplicationContext(db_path) as app:
                state = app.repositories.runtime_states.get(target.id)
                assert state is not None
                app.repositories.runtime_states.save(
                    replace(
                        state,
                        last_heartbeat_at=now - timedelta(seconds=240),
                        updated_at=now - timedelta(seconds=240),
                    )
                )

            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=2,
            )
            await asyncio.wait_for(goto_cancelled.wait(), timeout=1)
            await asyncio.wait_for(target_queue.join(), timeout=1)
        finally:
            await executor.stop()

        assert summary.recovered_runtime_count == 1
        with SqliteApplicationContext(db_path) as app:
            recovered_state = app.repositories.runtime_states.get(target.id)
        assert recovered_state is not None
        assert recovered_state.runtime_status == TargetRuntimeStatus.IDLE

    asyncio.run(run_test())


def test_inactive_stale_recovery_cancels_attempt_without_scan_failure(
    tmp_path: Path,
) -> None:
    """已停用 target 的 stale running cleanup 仍需取消 resident in-memory attempt。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    goto_started = asyncio.Event()
    goto_cancelled = asyncio.Event()

    class BlockingPreparePage(FakeAsyncPage):
        """page prepare 卡住，讓 recovery 有機會取消 attempt。"""

        async def goto(self, url: str, wait_until: str, timeout: float) -> None:
            self.url = url.rstrip("/")
            self.goto_count += 1
            goto_started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                goto_cancelled.set()
                raise

    class BlockingContext(FakeAsyncBrowserContext):
        """所有新 page 都使用可取消的 blocking page。"""

        async def new_page(self) -> FakeAsyncPage:
            page = BlockingPreparePage()
            self.pages.append(page)
            return page

    async def fake_scan_page(**_kwargs: Any) -> PostsScanSummary:
        raise AssertionError("inactive stale running cleanup must not scan")

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(BlockingContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
                max_concurrent_scans=1,
                stale_running_after_seconds=180,
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=fake_scan_page,
        )
        await executor.start()
        try:
            await run_resident_main_scheduler_tick(
                options=executor.options,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
            await asyncio.wait_for(goto_started.wait(), timeout=1)
            with SqliteApplicationContext(db_path) as app:
                state = app.repositories.runtime_states.get(target.id)
                assert state is not None
                assert state.runtime_status == TargetRuntimeStatus.RUNNING
                app.repositories.runtime_states.save(
                    replace(
                        state,
                        desired_state=TargetDesiredState.STOPPED,
                        last_heartbeat_at=now - timedelta(seconds=240),
                        updated_at=now - timedelta(seconds=240),
                    )
                )

            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=2,
            )
            await asyncio.wait_for(goto_cancelled.wait(), timeout=1)
            await asyncio.wait_for(target_queue.join(), timeout=1)
        finally:
            await executor.stop()

        assert summary.recovered_runtime_count == 1
        _queued_count, running_count, _queued_ids = await target_queue.snapshot()
        assert running_count == 0
        with SqliteApplicationContext(db_path) as app:
            recovered_state = app.repositories.runtime_states.get(target.id)
            latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        assert recovered_state is not None
        assert recovered_state.desired_state == TargetDesiredState.STOPPED
        assert recovered_state.runtime_status == TargetRuntimeStatus.IDLE
        assert latest_scan is None

    asyncio.run(run_test())


def test_stale_recovery_cancels_attempt_stuck_in_new_page(tmp_path: Path) -> None:
    """page 建立階段卡住時，也要能靠 running owner recovery 取消。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    new_page_started = asyncio.Event()
    new_page_cancelled = asyncio.Event()

    class FirstNewPageBlocksContext(FakeAsyncBrowserContext):
        """第一次建立 page 卡住，後續 page 正常。"""

        def __init__(self) -> None:
            super().__init__()
            self.blocked_once = False

        async def new_page(self) -> FakeAsyncPage:
            if not self.blocked_once:
                self.blocked_once = True
                new_page_started.set()
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    new_page_cancelled.set()
                    raise
            return await super().new_page()

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
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FirstNewPageBlocksContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
                max_concurrent_scans=1,
                stale_running_after_seconds=180,
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=fake_scan_page,
        )
        await executor.start()
        try:
            await run_resident_main_scheduler_tick(
                options=executor.options,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
            await asyncio.wait_for(new_page_started.wait(), timeout=1)
            with SqliteApplicationContext(db_path) as app:
                state = app.repositories.runtime_states.get(target.id)
                assert state is not None
                assert state.runtime_status == TargetRuntimeStatus.RUNNING
                app.repositories.runtime_states.save(
                    replace(
                        state,
                        last_heartbeat_at=now - timedelta(seconds=240),
                        updated_at=now - timedelta(seconds=240),
                    )
                )

            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=2,
            )
            await asyncio.wait_for(new_page_cancelled.wait(), timeout=1)
            await asyncio.wait_for(target_queue.join(), timeout=1)
        finally:
            await executor.stop()

        assert summary.recovered_runtime_count == 1
        with SqliteApplicationContext(db_path) as app:
            recovered_state = app.repositories.runtime_states.get(target.id)
        assert recovered_state is not None
        assert recovered_state.runtime_status == TargetRuntimeStatus.IDLE

    asyncio.run(run_test())


def test_async_resident_consumes_manual_scan_request_when_enqueued(
    tmp_path: Path,
) -> None:
    """manual scan request 進入 executor queue 時先清除，避免目前掃描完成後重跑。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

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
        target_queue = TargetQueue()
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
        enqueued_count = await executor.enqueue_due_targets(
            (
                DueTarget(
                    target_id=target.id,
                    interval_seconds=60,
                    due_at=utc_now(),
                    scan_requested=True,
                ),
            )
        )

        assert enqueued_count == 1
        with SqliteApplicationContext(db_path) as app:
            queued_state = app.repositories.runtime_states.get(target.id)
        assert queued_state is not None
        assert queued_state.runtime_status == TargetRuntimeStatus.QUEUED
        assert queued_state.scan_requested_at is None

    asyncio.run(run_test())
