"""Resident main worker tests。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from playwright.async_api import Error as AsyncPlaywrightError

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.scan_failure_policy import SCHEDULER_RUNTIME_RESTART_ACTION
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from tests.worker.scan_finalize_test_helpers import record_protective_skip_for_test


from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext
from tests.worker.resident_main_cycle_harness import (
    run_resident_main_cycle_harness as run_resident_main_cycle,
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
                scan_page=slow_scan_page,
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

    async def skipping_scan_page(**kwargs: Any) -> PostsScanSummary:
        result = record_protective_skip_for_test(
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
            url=str(kwargs["page"].url),
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=result.scan_run_id,
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
                ),
                page_pool=page_pool,
                scan_page=skipping_scan_page,
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
                scan_page=failing_scan_page,
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
                scan_page=failing_scan_page,
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
            scan_page=failing_scan_page,
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
                scan_page=blocking_scan_page,
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
