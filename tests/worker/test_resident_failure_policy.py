"""Resident main worker tests。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timedelta
import logging
from pathlib import Path
from typing import Any

from playwright.async_api import Error as AsyncPlaywrightError

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.scan_failure_policy import SCHEDULER_RUNTIME_RESTART_ACTION
from facebook_monitor.core.scan_failures import CONTENT_UNAVAILABLE_REASON
from facebook_monitor.core.scan_failures import FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.scheduler.planner import DueTarget
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.scan_pipeline_results import ProtectiveSkipScanResult


from tests.helpers.repository_reads import list_pending_notification_outbox
from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext
from tests.worker.resident_main_test_helpers import as_async_scan_callable
from tests.worker.resident_main_test_helpers import build_success_scan_result_for_test
from tests.worker.resident_main_cycle_harness import (
    run_resident_main_cycle_harness as run_resident_main_cycle,
)


class ControlledClockPlanner(TargetSchedulePlanner):
    """讓整合測試以明確時間驅動正式 planner，不實際等待重試間隔。"""

    def __init__(self, current_time: datetime) -> None:
        super().__init__()
        self.current_time = current_time
        self.dispatched_due_targets: list[DueTarget] = []

    def list_due_targets(
        self,
        db_path: Path,
        *,
        default_interval_seconds: float,
        max_count: int | None = None,
        now: datetime | None = None,
    ) -> tuple[DueTarget, ...]:
        """固定以測試時鐘列出 due targets。"""

        return super().list_due_targets(
            db_path,
            default_interval_seconds=default_interval_seconds,
            max_count=max_count,
            now=self.current_time if now is None else now,
        )

    def mark_dispatched(
        self,
        due_target: DueTarget,
        *,
        now: datetime | None = None,
    ) -> None:
        """記錄正式 executor dispatch，並以測試時鐘推進一般 cadence。"""

        self.dispatched_due_targets.append(due_target)
        super().mark_dispatched(
            due_target,
            now=self.current_time if now is None else now,
        )


def test_resident_main_scan_timeout_retries_until_third_failure(tmp_path: Path) -> None:
    """scan_timeout_seconds 會中止卡住的 scan，並重啟 page 後重試。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def slow_scan_page(**kwargs: Any) -> PostsScanSummary:
        await asyncio.sleep(0.2)
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
        context = FakeAsyncBrowserContext()
        page_pool = AsyncResidentPagePool(context)
        for attempt in range(1, 4):
            with SqliteApplicationContext(db_path) as app:
                app.services.targets.request_target_scan(target.id)
            summary = await run_resident_main_cycle(
                options=ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=tmp_path / "profile",
                    interval_seconds=0,
                    scan_timeout_seconds=0.01,
                    heartbeat_interval_seconds=0.01,
                ),
                page_pool=page_pool,
                scan_page=as_async_scan_callable(slow_scan_page),
                schedule_planner=TargetSchedulePlanner(),
                cycle_index=attempt,
            )
            assert summary.failure_count == 1
            assert await page_pool.size() == 0
            assert context.pages[-1].closed is True

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.ERROR
    assert state.consecutive_failure_reason == "scan_timeout"
    assert state.consecutive_failure_count == 3
    assert "已連續 3 次失敗" in state.last_error
    assert latest_scan is not None
    assert "已連續 3 次失敗" in latest_scan.error_message
    assert "會重啟" not in latest_scan.error_message
    assert latest_scan.metadata["reason"] == "scan_timeout"
    assert latest_scan.metadata["runtime_action"] == "error"
    assert latest_scan.metadata["retryable"] is False
    assert latest_scan.metadata["retry_streak"] == 3
    assert latest_scan.metadata["retry_limit"] == 3


def test_resident_main_escalates_sort_skip_after_three_skipped_scans(
    tmp_path: Path,
) -> None:
    """async resident 的 sort skip 前兩次只跳過，第三次折算 recoverable failure。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def skipping_scan_page(**kwargs: Any) -> ProtectiveSkipScanResult:
        return ProtectiveSkipScanResult(
            target_id=kwargs["target"].id,
            url=str(kwargs["page"].url),
            metadata={
                "worker": "resident_main",
                "skip_reason": SORT_ADJUST_UNCONFIRMED_REASON,
            },
        )

    async def run_test() -> None:
        context = FakeAsyncBrowserContext()
        page_pool = AsyncResidentPagePool(context)
        for attempt in range(1, 4):
            with SqliteApplicationContext(db_path) as app:
                app.services.targets.request_target_scan(target.id)
            summary = await run_resident_main_cycle(
                options=ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=tmp_path / "profile",
                    interval_seconds=0,
                ),
                page_pool=page_pool,
                scan_page=as_async_scan_callable(skipping_scan_page),
                schedule_planner=TargetSchedulePlanner(),
                cycle_index=attempt,
            )
            with SqliteApplicationContext(db_path) as app:
                state = app.repositories.runtime_states.get(target.id)
                latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
            assert state is not None
            assert latest_scan is not None
            if attempt < 3:
                assert summary.failure_count == 0
                assert summary.skipped_count == 1
                assert latest_scan.status == ScanStatus.SUCCESS
                assert state.consecutive_scan_skip_count == attempt
            else:
                assert summary.failure_count == 1
                assert latest_scan.status == ScanStatus.FAILED
                assert latest_scan.metadata["reason"] == SORT_ADJUST_UNCONFIRMED_REASON
                assert latest_scan.metadata["retry_streak"] == 1
                assert state.runtime_status == TargetRuntimeStatus.IDLE
                assert state.consecutive_failure_count == 1
                assert state.consecutive_scan_skip_count == 0
                assert await page_pool.size() == 0
                assert context.pages[-1].closed is True

    asyncio.run(run_test())


def test_resident_main_page_load_timeout_retries_until_third_failure(
    tmp_path: Path,
) -> None:
    """page_load_timeout 前兩次只略過本輪，第三次才讓 target 進 error。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def failing_scan_page(**_kwargs: Any) -> PostsScanSummary:
        raise AsyncPlaywrightError(
            "Page.evaluate: Execution context was destroyed, most likely because of a navigation."
        )

    async def run_test() -> None:
        context = FakeAsyncBrowserContext()
        page_pool = AsyncResidentPagePool(context)
        for attempt in range(1, 4):
            with SqliteApplicationContext(db_path) as app:
                app.services.targets.request_target_scan(target.id)
            summary = await run_resident_main_cycle(
                options=ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=tmp_path / "profile",
                    interval_seconds=0,
                ),
                page_pool=page_pool,
                scan_page=as_async_scan_callable(failing_scan_page),
                schedule_planner=TargetSchedulePlanner(),
                cycle_index=attempt,
            )
            assert summary.failure_count == 1
            assert summary.opened_page_count == 0
            assert summary.reused_page_count == 0
            assert await page_pool.size() == 0
            assert context.pages[-1].closed is True

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.ERROR
    assert state.consecutive_failure_count == 3
    assert "已連續 3 次失敗" in state.last_error
    assert latest_scan is not None
    assert "已連續 3 次失敗" in latest_scan.error_message
    assert "Execution context was destroyed" not in latest_scan.error_message
    assert "會重啟" not in latest_scan.error_message
    assert latest_scan.metadata["reason"] == "page_load_timeout"
    assert latest_scan.metadata["retryable"] is False
    assert latest_scan.metadata["runtime_action"] == "error"
    assert latest_scan.metadata["retry_streak"] == 3
    assert latest_scan.metadata["retry_limit"] == 3
    assert "Execution context was destroyed" in latest_scan.metadata["raw_failure_detail"]


def test_resident_main_browser_context_closed_retries_until_third_failure(
    tmp_path: Path,
) -> None:
    """browser/context closed 應歸類為 scheduler_runtime，第三次才進 error。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def failing_scan_page(**_kwargs: Any) -> PostsScanSummary:
        raise AsyncPlaywrightError("Target page, context or browser has been closed")

    async def run_test() -> None:
        context = FakeAsyncBrowserContext()
        page_pool = AsyncResidentPagePool(context)
        for attempt in range(1, 4):
            with SqliteApplicationContext(db_path) as app:
                app.services.targets.request_target_scan(target.id)
            summary = await run_resident_main_cycle(
                options=ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=tmp_path / "profile",
                    interval_seconds=0,
                ),
                page_pool=page_pool,
                scan_page=as_async_scan_callable(failing_scan_page),
                schedule_planner=TargetSchedulePlanner(),
                cycle_index=attempt,
            )
            assert summary.failure_count == 1
            assert context.pages[-1].closed is True

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.ERROR
    assert state.consecutive_failure_reason == SCHEDULER_RUNTIME_REASON
    assert state.consecutive_failure_count == 3
    assert latest_scan is not None
    assert latest_scan.metadata["reason"] == SCHEDULER_RUNTIME_REASON
    assert latest_scan.metadata["runtime_action"] == "error"
    assert latest_scan.metadata["retry_streak"] == 3
    assert latest_scan.metadata["retry_limit"] == 3


def test_resident_main_wrapped_driver_closed_requests_runtime_restart(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """一般 Exception 若包住 Playwright driver 斷線，也要重建 browser runtime。"""

    caplog.set_level(
        logging.WARNING,
        logger="facebook_monitor.worker.resident_main_executor_attempt",
    )
    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def failing_scan_page(**_kwargs: Any) -> PostsScanSummary:
        raise Exception("Page.evaluate: Connection closed while reading from the driver")

    async def run_test() -> None:
        context = FakeAsyncBrowserContext()
        page_pool = AsyncResidentPagePool(context)
        summary = await run_resident_main_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=page_pool,
            scan_page=as_async_scan_callable(failing_scan_page),
            schedule_planner=TargetSchedulePlanner(),
            cycle_index=1,
        )
        assert summary.failure_count == 1
        assert summary.resident_browser_alive is False
        assert context.pages[-1].closed is True

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.consecutive_failure_reason == SCHEDULER_RUNTIME_REASON
    assert state.consecutive_failure_count == 1
    assert state.scan_requested_at is not None
    assert latest_scan is not None
    assert latest_scan.metadata["reason"] == SCHEDULER_RUNTIME_REASON
    assert latest_scan.metadata["runtime_action"] == "will_retry"
    assert latest_scan.metadata["recovery_action"] == SCHEDULER_RUNTIME_RESTART_ACTION
    assert latest_scan.metadata["retryable"] is True
    assert (
        latest_scan.metadata["raw_failure_detail"]
        == "Page.evaluate: Connection closed while reading from the driver"
    )
    assert (
        "reason=scheduler_runtime runtime_action=will_retry "
        f"recovery_action={SCHEDULER_RUNTIME_RESTART_ACTION}"
    ) in caplog.text
    assert "reason=scheduler_runtime runtime_action=idle" not in caplog.text


def test_resident_main_cancels_scan_when_target_is_stopped(tmp_path: Path) -> None:
    """target 停止後，正在跑的 resident scan 會被 watchdog 取消且不寫失敗。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    started = asyncio.Event()

    async def blocking_scan_page(**kwargs: Any) -> PostsScanSummary:
        started.set()
        await asyncio.sleep(10)
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
        task = asyncio.create_task(
            run_resident_main_cycle(
                options=ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=tmp_path / "profile",
                    interval_seconds=0,
                    scan_timeout_seconds=5,
                    heartbeat_interval_seconds=0.01,
                ),
                page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
                scan_page=as_async_scan_callable(blocking_scan_page),
                schedule_planner=TargetSchedulePlanner(),
                cycle_index=1,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        with SqliteApplicationContext(db_path) as app:
            app.services.targets.pause_target_monitoring(target.id)
        summary = await asyncio.wait_for(task, timeout=1)
        assert summary.failure_count == 0
        assert summary.skipped_count == 1

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.last_error == ""


def test_resident_inconclusive_page_guard_retries_twice_then_stops(
    tmp_path: Path,
) -> None:
    """inconclusive第1/2次回idle，第3次error；每次discard且只在terminal入列通知。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="inconclusive-three-strikes",
                canonical_url=("https://www.facebook.com/groups/inconclusive-three-strikes"),
                config=TargetConfigPatch(enable_desktop_notification=True),
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def inconclusive_scan(**_kwargs: Any) -> object:
        raise WorkerFailure(
            FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON,
            "Facebook page guard evidence is inconclusive.",
        )

    async def run_test() -> None:
        context = FakeAsyncBrowserContext()
        page_pool = AsyncResidentPagePool(context)
        for attempt in range(1, 4):
            with SqliteApplicationContext(db_path) as app:
                app.services.targets.request_target_scan(target.id)
            summary = await run_resident_main_cycle(
                options=ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=tmp_path / "profile",
                    interval_seconds=0,
                ),
                page_pool=page_pool,
                scan_page=as_async_scan_callable(inconclusive_scan),
                schedule_planner=TargetSchedulePlanner(),
                cycle_index=attempt,
            )
            assert summary.failure_count == 1
            assert await page_pool.size() == 0
            assert context.pages[-1].closed is True
            with SqliteApplicationContext(db_path) as app:
                state = app.repositories.runtime_states.get(target.id)
                latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
                pending_outbox = list_pending_notification_outbox(
                    app.repositories.notification_outbox,
                )
            assert state is not None
            assert latest_scan is not None
            assert state.consecutive_failure_count == attempt
            assert latest_scan.metadata["retry_streak"] == attempt
            assert latest_scan.metadata["retry_limit"] == 3
            if attempt < 3:
                assert state.runtime_status == TargetRuntimeStatus.IDLE
                assert latest_scan.metadata["runtime_action"] == "will_retry"
                assert "依連續失敗規則重試" in latest_scan.error_message
                assert "已停止此監視項目的自動重試" not in latest_scan.error_message
                assert pending_outbox == []
            else:
                assert state.runtime_status == TargetRuntimeStatus.ERROR
                assert latest_scan.metadata["runtime_action"] == "error"
                assert "已連續 3 次失敗" in latest_scan.error_message
                assert "已停止此監視項目" in latest_scan.error_message
                assert len(pending_outbox) == 1
                assert "系統已停止此監視項目" in pending_outbox[0].message

    asyncio.run(run_test())


def test_resident_content_unavailable_reopens_page_before_terminal_error(
    tmp_path: Path,
) -> None:
    """自動到期後等待兩次30秒並重開page，第三次才停止target。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="content-unavailable-three-strikes",
                canonical_url=("https://www.facebook.com/groups/content-unavailable-three-strikes"),
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.clear_target_scan_request(target.id)

    seen_page_ids: list[int] = []

    async def unavailable_scan(**kwargs: Any) -> object:
        seen_page_ids.append(id(kwargs["page"]))
        raise WorkerFailure(
            CONTENT_UNAVAILABLE_REASON,
            "Facebook 顯示目前無法查看此內容。",
        )

    async def run_test() -> None:
        context = FakeAsyncBrowserContext()
        page_pool = AsyncResidentPagePool(context)
        planner = ControlledClockPlanner(datetime.now().astimezone())
        options = ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            interval_seconds=60,
        )
        for attempt in range(1, 4):
            summary = await run_resident_main_cycle(
                options=options,
                page_pool=page_pool,
                scan_page=as_async_scan_callable(unavailable_scan),
                schedule_planner=planner,
                cycle_index=attempt,
            )
            assert summary.selected_count == 1
            assert summary.failure_count == 1
            assert await page_pool.size() == 0
            assert len(context.pages) == attempt
            assert all(page.closed for page in context.pages)
            assert len(seen_page_ids) == attempt
            assert len(set(seen_page_ids)) == attempt
            assert len(planner.dispatched_due_targets) == attempt
            assert planner.dispatched_due_targets[-1].target_id == target.id
            assert planner.dispatched_due_targets[-1].scan_requested is False
            with SqliteApplicationContext(db_path) as app:
                state = app.repositories.runtime_states.get(target.id)
                latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
            assert state is not None
            assert latest_scan is not None
            assert state.consecutive_failure_count == attempt
            assert latest_scan.metadata["retry_streak"] == attempt
            assert latest_scan.metadata["retry_limit"] == 3
            assert state.scan_requested_at is None
            assert "auto_restart" not in latest_scan.metadata
            if attempt < 3:
                assert state.runtime_status == TargetRuntimeStatus.IDLE
                assert latest_scan.metadata["runtime_action"] == "will_retry"
                assert latest_scan.metadata["retry_delay_seconds"] == 30
                retry_due_at = latest_scan.finished_at + timedelta(seconds=30)
                planner.current_time = retry_due_at - timedelta(seconds=1)
                assert (
                    planner.list_due_targets(
                        db_path,
                        default_interval_seconds=options.interval_seconds,
                    )
                    == ()
                )
                planner.current_time = retry_due_at
            else:
                assert state.runtime_status == TargetRuntimeStatus.ERROR
                assert latest_scan.metadata["runtime_action"] == "error"
                assert "retry_delay_seconds" not in latest_scan.metadata
                planner.current_time = latest_scan.finished_at + timedelta(days=1)
                assert (
                    planner.list_due_targets(
                        db_path,
                        default_interval_seconds=options.interval_seconds,
                    )
                    == ()
                )

    asyncio.run(run_test())


def test_resident_success_resets_inconclusive_page_guard_streak(tmp_path: Path) -> None:
    """成功輪沿用既有runtime reset，下一次inconclusive重新從streak 1開始。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="inconclusive-success-reset",
                canonical_url="https://www.facebook.com/groups/inconclusive-success-reset",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    outcomes = ["inconclusive", "success", "inconclusive"]

    async def sequenced_scan(**kwargs: Any) -> object:
        outcome = outcomes.pop(0)
        if outcome == "inconclusive":
            raise WorkerFailure(
                FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON,
                "Facebook page guard evidence is inconclusive.",
            )
        return build_success_scan_result_for_test(
            target=kwargs["target"],
            page_url=kwargs["page"].url,
        )

    async def run_test() -> None:
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        for cycle_index in range(1, 4):
            with SqliteApplicationContext(db_path) as app:
                app.services.targets.request_target_scan(target.id)
            await run_resident_main_cycle(
                options=ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=tmp_path / "profile",
                    interval_seconds=0,
                ),
                page_pool=page_pool,
                scan_page=as_async_scan_callable(sequenced_scan),
                schedule_planner=TargetSchedulePlanner(),
                cycle_index=cycle_index,
            )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
    assert state is not None
    assert latest_scan is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.consecutive_failure_reason == FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON
    assert state.consecutive_failure_count == 1
    assert latest_scan.metadata["retry_streak"] == 1
    assert latest_scan.metadata["runtime_action"] == "will_retry"
