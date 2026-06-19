"""Resident main worker tests。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from typing import cast

import pytest
from playwright.async_api import Error as AsyncPlaywrightError

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import SCHEDULER_STOPPING_REASON
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.notifications.outbox_service import build_notification_idempotency_key
from facebook_monitor.scheduler.planner import DueTarget
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.resident_main import run_resident_main_scheduler_tick
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker import resident_main_executor_attempt as attempt_module
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_page_pool import PageOwnership
from facebook_monitor.worker.resident_main_queue import QueueItem
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.scan_finalize import NormalizedScanItem
from facebook_monitor.worker.scan_finalize import finalize_scan_items
from facebook_monitor.worker.scan_finalize import scan_commit_guard_from_runtime_state
from facebook_monitor.worker.scan_pipeline_results import ProtectiveSkipScanResult
from facebook_monitor.worker.scan_pipeline_results import SuccessScanResult


from tests.worker.resident_main_test_helpers import FakeAsyncPage
from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext
from tests.worker.resident_main_test_helpers import RecordingSchedulePlanner
from tests.worker.resident_main_test_helpers import _stub_runtime_outbox_dispatch
from tests.worker.resident_main_test_helpers import as_async_scan_callable
from tests.worker.resident_main_test_helpers import build_success_scan_result_for_test
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

    async def fake_scan_page(**kwargs: Any) -> object:
        return build_success_scan_result_for_test(
            target=kwargs["target"],
            page_url=kwargs["page"].url,
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
            scan_page=as_async_scan_callable(fake_scan_page),
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
            scan_page=as_async_scan_callable(fake_scan_page),
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


def test_resident_running_claim_rejected_does_not_release_reserved_page(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """只 reserve page id 但未 acquire page 時，不應執行 page/planner cleanup。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="claim-rejected",
                canonical_url="https://www.facebook.com/groups/claim-rejected",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def fake_scan_page(**_kwargs: Any) -> PostsScanSummary:
        raise AssertionError("scan should not run when running claim is rejected")

    async def run_test() -> tuple[
        TargetQueue,
        RecordingSchedulePlanner,
        ExecutorWorkerPool,
        list[tuple[str, str]],
        list[str],
    ]:
        target_queue = TargetQueue()
        planner = RecordingSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        release_if_calls: list[tuple[str, str]] = []
        release_calls: list[str] = []

        async def record_release_if_page_id(
            target_id: str,
            page_id: str,
            *,
            current_url: str = "",
        ) -> bool:
            release_if_calls.append((target_id, page_id))
            return False

        async def record_release(target_id: str, *, current_url: str = "") -> None:
            release_calls.append(target_id)

        monkeypatch.setattr(page_pool, "release_if_page_id", record_release_if_page_id)
        monkeypatch.setattr(page_pool, "release", record_release)
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=60,
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(fake_scan_page),
        )
        assert (
            await executor.enqueue_due_targets(
                (
                    DueTarget(
                        target_id=target.id,
                        interval_seconds=60,
                        due_at=utc_now(),
                    ),
                )
            )
            == 1
        )
        item = await target_queue.get()
        assert item is not None
        with SqliteApplicationContext(db_path) as app:
            app.services.targets.mark_target_running(
                target.id,
                "worker-other",
                page_id="page-other",
            )

        result = await executor._run_queue_item("worker-1", item)  # noqa: SLF001
        await asyncio.wait_for(target_queue.join(), timeout=1)
        assert result.skipped is True
        return target_queue, planner, executor, release_if_calls, release_calls

    target_queue, planner, executor, release_if_calls, release_calls = asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == "worker-other"
    assert state.active_page_id == "page-other"
    assert latest_scan is None
    assert asyncio.run(target_queue.snapshot()) == (0, 0, ())
    assert planner.dispatched_target_ids == []
    assert planner.finished_target_ids == []
    assert release_if_calls == []
    assert release_calls == []
    assert executor._active_attempt_tasks == {}  # noqa: SLF001
    assert executor._active_scan_tasks == {}  # noqa: SLF001


def test_resident_pre_admission_cancellation_marks_queued_idle_and_cleans_queue(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """claim running 前取消時，現行語義是回 idle、無 scan run，並 re-raise。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="pre-admission-cancel",
                canonical_url="https://www.facebook.com/groups/pre-admission-cancel",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def fake_scan_page(**_kwargs: Any) -> PostsScanSummary:
        raise AssertionError("scan should not run before target admission")

    async def run_test() -> tuple[TargetQueue, RecordingSchedulePlanner, ExecutorWorkerPool]:
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
            scan_page=as_async_scan_callable(fake_scan_page),
        )
        assert (
            await executor.enqueue_due_targets(
                (
                    DueTarget(
                        target_id=target.id,
                        interval_seconds=60,
                        due_at=utc_now(),
                    ),
                )
            )
            == 1
        )
        item = await target_queue.get()
        assert item is not None

        original_run_db_operation = executor._run_db_operation_with_retry  # noqa: SLF001
        load_started = asyncio.Event()

        async def delayed_load(operation_name: str, operation: Any) -> Any:
            if operation_name == "load_resident_target":
                load_started.set()
                await asyncio.sleep(10)
            return await original_run_db_operation(operation_name, operation)

        monkeypatch.setattr(executor, "_run_db_operation_with_retry", delayed_load)

        task = asyncio.create_task(executor._run_queue_item("worker-1", item))  # noqa: SLF001
        await asyncio.wait_for(load_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(target_queue.join(), timeout=1)
        return target_queue, planner, executor

    target_queue, planner, executor = asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.active_worker_id == ""
    assert state.active_page_id == ""
    assert state.last_error == ""
    assert latest_scan is None
    assert asyncio.run(target_queue.snapshot()) == (0, 0, ())
    assert planner.dispatched_target_ids == []
    assert planner.finished_target_ids == []
    assert executor._active_attempt_tasks == {}  # noqa: SLF001
    assert executor._active_scan_tasks == {}  # noqa: SLF001


def test_resident_scheduler_stopping_cancellation_records_guarded_idle_failure(
    tmp_path: Path,
) -> None:
    """running 後一般取消會記錄 scheduler_stopping failure，清理後 re-raise。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="scheduler-stopping",
                canonical_url="https://www.facebook.com/groups/scheduler-stopping",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    scan_started = asyncio.Event()
    scan_cancelled = asyncio.Event()

    async def blocking_scan_page(**_kwargs: Any) -> PostsScanSummary:
        scan_started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            scan_cancelled.set()
            raise
        raise AssertionError("scan should be cancelled")

    async def run_test() -> tuple[TargetQueue, RecordingSchedulePlanner, ExecutorWorkerPool]:
        target_queue = TargetQueue()
        planner = RecordingSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=60,
                heartbeat_interval_seconds=1,
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(blocking_scan_page),
        )
        assert (
            await executor.enqueue_due_targets(
                (
                    DueTarget(
                        target_id=target.id,
                        interval_seconds=60,
                        due_at=utc_now(),
                    ),
                )
            )
            == 1
        )
        item = await target_queue.get()
        assert item is not None
        task = asyncio.create_task(executor._run_queue_item("worker-1", item))  # noqa: SLF001
        await asyncio.wait_for(scan_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(target_queue.join(), timeout=1)
        assert scan_cancelled.is_set()
        return target_queue, planner, executor

    target_queue, planner, executor = asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        pending_outbox = app.repositories.notification_outbox.list_pending()
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.last_error == ""
    assert state.consecutive_failure_count == 0
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.FAILED
    assert latest_scan.metadata["reason"] == SCHEDULER_STOPPING_REASON
    assert pending_outbox == []
    assert asyncio.run(target_queue.snapshot()) == (0, 0, ())
    assert planner.dispatched_target_ids == [target.id]
    assert planner.finished_target_ids == [target.id]
    assert executor._active_attempt_tasks == {}  # noqa: SLF001
    assert executor._active_scan_tasks == {}  # noqa: SLF001
    assert target.id in executor.page_pool.pages
    assert executor.page_pool.pages[target.id].in_use_by_worker == ""


def test_resident_page_prepare_playwright_failure_discards_page_and_cleans_attempt(
    tmp_path: Path,
) -> None:
    """page prepare 的 Playwright failure 會保留 failure 語義並清掉 acquired page。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="prepare-failure",
                canonical_url="https://www.facebook.com/groups/prepare-failure",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    class FailingGotoPage(FakeAsyncPage):
        """測試用 page：第一次 goto 即模擬 Playwright navigation failure。"""

        async def goto(self, url: str, wait_until: str, timeout: float) -> None:
            await super().goto(url, wait_until=wait_until, timeout=timeout)
            raise AsyncPlaywrightError("Page.goto: Execution context was destroyed")

    class FailingPrepareContext(FakeAsyncBrowserContext):
        """建立會在 prepare 階段失敗的 page。"""

        async def new_page(self) -> FakeAsyncPage:
            page = FailingGotoPage()
            self.pages.append(page)
            return page

    async def fake_scan_page(**_kwargs: Any) -> PostsScanSummary:
        raise AssertionError("scan should not run when page prepare fails")

    async def run_test() -> tuple[TargetQueue, RecordingSchedulePlanner, ExecutorWorkerPool]:
        target_queue = TargetQueue()
        planner = RecordingSchedulePlanner()
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=60,
            ),
            page_pool=AsyncResidentPagePool(FailingPrepareContext()),
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(fake_scan_page),
        )
        assert (
            await executor.enqueue_due_targets(
                (
                    DueTarget(
                        target_id=target.id,
                        interval_seconds=60,
                        due_at=utc_now(),
                    ),
                )
            )
            == 1
        )
        item = await target_queue.get()
        assert item is not None
        result = await executor._run_queue_item("worker-1", item)  # noqa: SLF001
        await asyncio.wait_for(target_queue.join(), timeout=1)
        assert result.failure is True
        return target_queue, planner, executor

    target_queue, planner, executor = asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.active_worker_id == ""
    assert state.active_page_id == ""
    assert state.consecutive_failure_count == 1
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.FAILED
    assert asyncio.run(target_queue.snapshot()) == (0, 0, ())
    assert planner.dispatched_target_ids == [target.id]
    assert planner.finished_target_ids == [target.id]
    assert executor._active_attempt_tasks == {}  # noqa: SLF001
    assert executor._active_scan_tasks == {}  # noqa: SLF001
    assert asyncio.run(executor.page_pool.size()) == 0


def test_resident_failure_discard_ignores_newer_page_id() -> None:
    """failure discard 必須帶 page id guard，避免舊 attempt 關掉新 page。"""

    async def run_test() -> None:
        target_id = "target-1"
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        new_page = FakeAsyncPage()
        page_pool.pages[target_id] = PageOwnership(
            page=new_page,
            page_id="new-page",
            target_id=target_id,
            in_use_by_worker="worker-new",
            current_url="https://new.example",
        )

        class DiscardHost:
            """測試用 host，只提供 discard helper 需要的 page pool。"""

            def __init__(self) -> None:
                self.page_pool = page_pool

        await attempt_module._discard_failed_attempt_page(  # noqa: SLF001
            cast(attempt_module.ResidentExecutorAttemptHost, DiscardHost()),
            attempt_module.ResidentQueueAttemptState(
                target_id=target_id,
                page_id="old-page",
            ),
        )

        assert page_pool.pages[target_id].page_id == "new-page"
        assert page_pool.pages[target_id].in_use_by_worker == "worker-new"
        assert not new_page.closed

    asyncio.run(run_test())


def test_resident_scheduler_stopping_cancellation_guard_mismatch_writes_no_failure(
    tmp_path: Path,
) -> None:
    """scheduler stopping outcome 遇新 owner 時不可寫 stale failure。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="scheduler-stopping-stale",
                canonical_url="https://www.facebook.com/groups/scheduler-stopping-stale",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        running = app.services.targets.mark_target_running(
            target.id,
            "worker-a",
            page_id="page-a",
        )
        commit_guard = scan_commit_guard_from_runtime_state(running)
        app.services.targets.mark_target_running(
            target.id,
            "worker-b",
            page_id="page-b",
        )

    class CancellationHost:
        """測試用 host，只提供 cancellation helper 需要的 options。"""

        def __init__(self) -> None:
            self.options = ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=60,
            )

    async def run_test() -> attempt_module.ResidentAttemptTerminalTransition:
        return await attempt_module._record_scheduler_stopping_cancellation(  # noqa: SLF001
            pool=cast(attempt_module.ResidentExecutorAttemptHost, CancellationHost()),
            state=attempt_module.ResidentQueueAttemptState(
                target_id=target.id,
                page_id="page-a",
                acquired_page=True,
                owner_key="owner-a",
                commit_guard=commit_guard,
            ),
        )

    transition = asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        pending_outbox = app.repositories.notification_outbox.list_pending()
    assert transition.outcome.kind.value == "owner_changed"
    assert transition.outcome.to_scan_result().skipped is True
    assert transition.cleanup_plan is not None
    assert transition.cleanup_plan.target_id == target.id
    assert transition.cleanup_plan.page_id == "page-a"
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == "worker-b"
    assert state.active_page_id == "page-b"
    assert latest_scan is None
    assert pending_outbox == []


def test_resident_scheduler_stopping_stale_guard_re_raises_and_preserves_new_owner(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """scheduler stopping stale guard 的完整 attempt 仍 re-raise 並保留新 owner。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="scheduler-stopping-full-stale",
                canonical_url=("https://www.facebook.com/groups/scheduler-stopping-full-stale"),
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    scan_started = asyncio.Event()
    scan_cancelled = asyncio.Event()

    async def unused_scan_page(**_kwargs: Any) -> PostsScanSummary:
        raise AssertionError("scan callable should be wrapped by monkeypatch")

    async def cancellable_scan_with_heartbeat(
        _scan_page: Any,
        **_kwargs: Any,
    ) -> object:
        scan_started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            scan_cancelled.set()
            raise
        raise AssertionError("scan should be cancelled")

    async def run_test() -> tuple[
        TargetQueue, RecordingSchedulePlanner, ExecutorWorkerPool, FakeAsyncPage
    ]:
        target_queue = TargetQueue()
        planner = RecordingSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=60,
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(unused_scan_page),
        )
        monkeypatch.setattr(
            executor,
            "_run_scan_with_heartbeat",
            cancellable_scan_with_heartbeat,
        )
        assert (
            await executor.enqueue_due_targets(
                (
                    DueTarget(
                        target_id=target.id,
                        interval_seconds=60,
                        due_at=utc_now(),
                    ),
                )
            )
            == 1
        )
        item = await target_queue.get()
        assert item is not None
        task = asyncio.create_task(executor._run_queue_item("worker-a", item))  # noqa: SLF001
        await asyncio.wait_for(scan_started.wait(), timeout=1)

        with SqliteApplicationContext(db_path) as app:
            app.services.targets.mark_target_running(
                target.id,
                "worker-b",
                page_id="page-b",
            )
        await target_queue.bind_running_owner(target.id, "new-owner")
        new_page = FakeAsyncPage()
        page_pool.pages[target.id] = PageOwnership(
            page=new_page,
            page_id="page-b",
            target_id=target.id,
            in_use_by_worker="worker-b",
            current_url="https://new.example",
        )

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(target_queue.join(), timeout=1)
        return target_queue, planner, executor, new_page

    target_queue, planner, executor, new_page = asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        pending_outbox = app.repositories.notification_outbox.list_pending()
    assert scan_cancelled.is_set()
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == "worker-b"
    assert state.active_page_id == "page-b"
    assert latest_scan is None
    assert pending_outbox == []
    assert asyncio.run(target_queue.snapshot()) == (0, 1, ())
    assert planner.dispatched_target_ids == [target.id]
    assert planner.finished_target_ids == [target.id]
    assert executor._active_attempt_tasks == {}  # noqa: SLF001
    assert executor.page_pool.pages[target.id].page_id == "page-b"
    assert executor.page_pool.pages[target.id].in_use_by_worker == "worker-b"
    assert not new_page.closed


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

    async def fake_scan_page(**kwargs: Any) -> ProtectiveSkipScanResult:
        return ProtectiveSkipScanResult(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            metadata={
                "worker": "resident_main",
                "skip_reason": SORT_ADJUST_UNCONFIRMED_REASON,
            },
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
            scan_page=as_async_scan_callable(fake_scan_page),
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
            scan_page=as_async_scan_callable(fake_scan_page),
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

    async def fake_scan_page(**kwargs: Any) -> object:
        return build_success_scan_result_for_test(
            target=kwargs["target"],
            page_url=kwargs["page"].url,
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
            scan_page=as_async_scan_callable(fake_scan_page),
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

    async def fake_scan_page(**kwargs: Any) -> object:
        return build_success_scan_result_for_test(
            target=kwargs["target"],
            page_url=kwargs["page"].url,
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
            scan_page=as_async_scan_callable(fake_scan_page),
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


def test_resident_success_result_writes_visible_scan_state_once(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """resident success result 由 coordinator 寫入 visible state 一次。"""

    db_path = tmp_path / "app.db"
    dispatch_calls = _stub_runtime_outbox_dispatch(monkeypatch)
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="resident-success",
                canonical_url="https://www.facebook.com/groups/resident-success",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase1-resident",
                ),
            )
        )
        target = app.services.targets.restart_target_monitoring(target.id)
        app.repositories.scan_scope_state.mark_initialized(target.scope_id)

    async def scan_to_success_result(**kwargs: Any) -> SuccessScanResult:
        return SuccessScanResult(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            items=(
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:resident-success",
                    alias_keys=("post:resident-success",),
                    group_id="resident-success",
                    author="作者",
                    text="這是一篇票券貼文",
                    permalink=("https://www.facebook.com/groups/resident-success/posts/1"),
                    raw_target_kind=kwargs["target"].target_kind.value,
                ),
            ),
            item_count=1,
            metadata={"worker": "resident_main"},
        )

    async def run_test() -> tuple[RecordingSchedulePlanner, ExecutorWorkerPool]:
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
            scan_page=as_async_scan_callable(scan_to_success_result),
        )
        assert (
            await executor.enqueue_due_targets(
                (
                    DueTarget(
                        target_id=target.id,
                        interval_seconds=60,
                        due_at=utc_now(),
                    ),
                )
            )
            == 1
        )
        item = await target_queue.get()
        assert item is not None
        result = await executor._run_queue_item("worker-1", item)  # noqa: SLF001
        await asyncio.wait_for(target_queue.join(), timeout=1)
        assert result.success is True
        assert result.failure is False
        assert result.skipped is False
        assert result.opened_page is True
        assert result.reused_page is False
        assert await target_queue.snapshot() == (0, 0, ())
        return planner, executor

    planner, executor = asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)
        history = app.repositories.match_history.list_by_target(target.id)
        outbox_entry = app.repositories.notification_outbox.get_by_idempotency_key(
            build_notification_idempotency_key(
                target_id=target.id,
                item_key="post:resident-success",
                channel=NotificationChannel.NTFY,
            )
        )
        scan_count = app.repositories.scan_runs.connection.execute(
            "SELECT COUNT(*) FROM scan_runs WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert scan_count == 1
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.SUCCESS
    assert len(latest_items) == 1
    assert latest_items[0].item_key == "post:resident-success"
    assert len(history) == 1
    assert outbox_entry is not None
    assert dispatch_calls == [db_path]
    assert planner.dispatched_target_ids == [target.id]
    assert planner.finished_target_ids == [target.id]
    assert executor._active_attempt_tasks == {}  # noqa: SLF001
    assert executor._active_scan_tasks == {}  # noqa: SLF001
    assert executor.page_pool.pages[target.id].in_use_by_worker == ""


def test_resident_success_result_is_committed_by_coordinator(
    tmp_path: Path,
) -> None:
    """resident scanner 回傳 success result 時，由 coordinator 寫 visible scan state。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="resident-success-result",
                canonical_url="https://www.facebook.com/groups/resident-success-result",
                config=TargetConfigPatch(include_keywords=("票券",)),
            )
        )
        target = app.services.targets.restart_target_monitoring(target.id)
        app.repositories.scan_scope_state.mark_initialized(target.scope_id)

    async def scan_to_success_result(**kwargs: Any) -> SuccessScanResult:
        return SuccessScanResult(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            items=(
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:resident-success-result",
                    alias_keys=("post:resident-success-result",),
                    group_id="resident-success-result",
                    author="作者",
                    text="這是一篇票券貼文",
                    permalink=("https://www.facebook.com/groups/resident-success-result/posts/1"),
                    raw_target_kind=kwargs["target"].target_kind.value,
                ),
            ),
            item_count=1,
            metadata={"worker": "resident_main"},
        )

    async def run_test() -> tuple[RecordingSchedulePlanner, ExecutorWorkerPool]:
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
            scan_page=as_async_scan_callable(scan_to_success_result),
        )
        assert (
            await executor.enqueue_due_targets(
                (
                    DueTarget(
                        target_id=target.id,
                        interval_seconds=60,
                        due_at=utc_now(),
                    ),
                )
            )
            == 1
        )
        item = await target_queue.get()
        assert item is not None
        result = await executor._run_queue_item("worker-1", item)  # noqa: SLF001
        await asyncio.wait_for(target_queue.join(), timeout=1)
        assert result.success is True
        assert result.failure is False
        assert result.skipped is False
        return planner, executor

    planner, executor = asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)
        history = app.repositories.match_history.list_by_target(target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.SUCCESS
    assert latest_scan.metadata["worker"] == "resident_main"
    assert len(latest_items) == 1
    assert latest_items[0].item_key == "post:resident-success-result"
    assert len(history) == 1
    assert planner.dispatched_target_ids == [target.id]
    assert planner.finished_target_ids == [target.id]
    assert executor._active_attempt_tasks == {}  # noqa: SLF001
    assert executor._active_scan_tasks == {}  # noqa: SLF001
    assert executor.page_pool.pages[target.id].in_use_by_worker == ""


def test_resident_comments_success_result_writes_visible_state_once(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """resident comments success result 由 coordinator 寫入 visible state 一次。"""

    parent_post_id = "2187454285426518"
    comment_id = "9876543210987654"
    db_path = tmp_path / "app.db"
    dispatch_calls = _stub_runtime_outbox_dispatch(monkeypatch)
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="resident-comments",
                parent_post_id=parent_post_id,
                canonical_url=(
                    f"https://www.facebook.com/groups/resident-comments/posts/{parent_post_id}"
                ),
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase5-comments-resident",
                ),
            )
        )
        target = app.services.targets.restart_target_monitoring(target.id)
        app.repositories.scan_scope_state.mark_initialized(target.scope_id)

    async def scan_to_success_result(**kwargs: Any) -> SuccessScanResult:
        return SuccessScanResult(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            items=(
                NormalizedScanItem(
                    item_kind=ItemKind.COMMENT,
                    item_key="comment:resident-success",
                    alias_keys=("comment:resident-success",),
                    group_id="resident-comments",
                    parent_post_id=parent_post_id,
                    comment_id=comment_id,
                    author="留言作者",
                    text="這是一則有票券關鍵字的留言",
                    permalink=f"{kwargs['target'].canonical_url}?comment_id={comment_id}",
                    raw_target_kind=kwargs["target"].target_kind.value,
                    metadata={"commentId": comment_id},
                ),
            ),
            item_count=1,
            metadata={
                "worker": "resident_main",
                "comment_sort": {"reason": "unit_contract"},
                "comments_meta": {"commentsWithCommentIdCount": 1},
            },
        )

    async def run_test() -> tuple[RecordingSchedulePlanner, ExecutorWorkerPool]:
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
            scan_page=as_async_scan_callable(scan_to_success_result),
            scan_comments_target_page=scan_to_success_result,
        )
        assert (
            await executor.enqueue_due_targets(
                (
                    DueTarget(
                        target_id=target.id,
                        interval_seconds=60,
                        due_at=utc_now(),
                    ),
                )
            )
            == 1
        )
        item = await target_queue.get()
        assert item is not None
        result = await executor._run_queue_item("worker-1", item)  # noqa: SLF001
        await asyncio.wait_for(target_queue.join(), timeout=1)
        assert result.success is True
        assert result.failure is False
        assert result.skipped is False
        assert result.opened_page is True
        assert await target_queue.snapshot() == (0, 0, ())
        return planner, executor

    planner, executor = asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)
        history = app.repositories.match_history.list_by_target(target.id)
        outbox_entry = app.repositories.notification_outbox.get_by_idempotency_key(
            build_notification_idempotency_key(
                target_id=target.id,
                item_key="comment:resident-success",
                channel=NotificationChannel.NTFY,
            )
        )
        scan_count = app.repositories.scan_runs.connection.execute(
            "SELECT COUNT(*) FROM scan_runs WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert scan_count == 1
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.SUCCESS
    assert latest_scan.metadata["comment_sort"] == {"reason": "unit_contract"}
    assert len(latest_items) == 1
    assert latest_items[0].item_kind == ItemKind.COMMENT
    assert latest_items[0].debug_metadata["commentId"] == comment_id
    assert len(history) == 1
    assert history[0].item_kind == ItemKind.COMMENT
    assert history[0].parent_post_id == parent_post_id
    assert history[0].comment_id == comment_id
    assert outbox_entry is not None
    assert outbox_entry.item_kind == ItemKind.COMMENT
    assert dispatch_calls == [db_path]
    assert planner.dispatched_target_ids == [target.id]
    assert planner.finished_target_ids == [target.id]
    assert executor._active_attempt_tasks == {}  # noqa: SLF001
    assert executor._active_scan_tasks == {}  # noqa: SLF001
    assert executor.page_pool.pages[target.id].in_use_by_worker == ""


def test_resident_stale_owner_before_finalize_writes_no_visible_scan_state(
    tmp_path: Path,
) -> None:
    """resident scanner 若遇 stop/start 後舊 guard，不可寫 latest/history/outbox。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="resident-stale",
                canonical_url="https://www.facebook.com/groups/resident-stale",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase1-resident",
                ),
            )
        )
        target = app.services.targets.restart_target_monitoring(target.id)
        app.repositories.scan_scope_state.mark_initialized(target.scope_id)

    async def stale_finalize_scan(**kwargs: Any) -> PostsScanSummary:
        runtime_state = kwargs["app"].services.targets.ensure_runtime_state(
            kwargs["target"].id,
        )
        commit_guard = scan_commit_guard_from_runtime_state(runtime_state)
        assert commit_guard is not None
        with SqliteApplicationContext(db_path) as app:
            app.services.targets.pause_target_monitoring(target.id)
            app.services.targets.restart_target_monitoring(target.id)
        finalize_scan_items(
            app=kwargs["app"],
            target=kwargs["target"],
            config=kwargs["config"],
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:stale",
                    alias_keys=("post:stale",),
                    group_id="resident-stale",
                    text="票券",
                )
            ],
            item_count=1,
            metadata={"worker": "resident_main"},
            commit_guard=commit_guard,
        )
        raise AssertionError("stale finalize should raise WorkerFailure")

    async def run_test() -> tuple[RecordingSchedulePlanner, ExecutorWorkerPool]:
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
            scan_page=as_async_scan_callable(stale_finalize_scan),
        )
        assert (
            await executor.enqueue_due_targets(
                (
                    DueTarget(
                        target_id=target.id,
                        interval_seconds=60,
                        due_at=utc_now(),
                    ),
                )
            )
            == 1
        )
        item = await target_queue.get()
        assert item is not None
        result = await executor._run_queue_item("worker-1", item)  # noqa: SLF001
        await asyncio.wait_for(target_queue.join(), timeout=1)
        assert result.skipped is True
        assert result.success is False
        assert result.failure is False
        assert await target_queue.snapshot() == (0, 0, ())
        return planner, executor

    planner, executor = asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)
        history = app.repositories.match_history.list_by_target(target.id)
        pending_outbox = app.repositories.notification_outbox.list_pending()

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.active_worker_id == ""
    assert state.active_page_id == ""
    assert latest_scan is None
    assert latest_items == []
    assert history == []
    assert pending_outbox == []
    assert planner.dispatched_target_ids == [target.id]
    assert planner.finished_target_ids == [target.id]
    assert executor._active_attempt_tasks == {}  # noqa: SLF001
    assert executor._active_scan_tasks == {}  # noqa: SLF001


def test_resident_manual_scan_request_during_running_survives_guarded_finish(
    tmp_path: Path,
) -> None:
    """running 期間送出的 scan-once request 不可被本輪 guarded idle 清掉。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="manual-during-running",
                canonical_url="https://www.facebook.com/groups/manual-during-running",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def request_scan_before_finish(**kwargs: Any) -> object:
        with SqliteApplicationContext(db_path) as app:
            app.services.targets.request_target_scan(kwargs["target"].id)
        return build_success_scan_result_for_test(
            target=kwargs["target"],
            page_url=kwargs["page"].url,
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
            scan_page=as_async_scan_callable(request_scan_before_finish),
        )
        assert (
            await executor.enqueue_due_targets(
                (
                    DueTarget(
                        target_id=target.id,
                        interval_seconds=60,
                        due_at=utc_now(),
                    ),
                )
            )
            == 1
        )
        item = await target_queue.get()
        assert item is not None
        result = await executor._run_queue_item("worker-1", item)  # noqa: SLF001
        await asyncio.wait_for(target_queue.join(), timeout=1)
        assert result.success

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.scan_requested_at is not None
