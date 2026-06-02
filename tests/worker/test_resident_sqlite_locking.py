"""Resident main worker tests。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any

from pytest import MonkeyPatch
from playwright.async_api import Error as AsyncPlaywrightError

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import TargetApplicationService
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import PAGE_LOAD_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker import resident_main as resident_main_module
from facebook_monitor.worker import resident_main_executor as resident_main_executor_module
from facebook_monitor.worker.resident_main import _publish_display_next_due_at
from facebook_monitor.worker.resident_main import run_resident_main_cycle
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.scan_finalize import record_skipped_scan
from facebook_monitor.worker import scan_failure_finalize as scan_failure_finalize_module


from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext


def test_resident_main_retries_page_reload_state_sqlite_lock(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """page-reloaded runtime state 寫入遇到 SQLite lock 時應重試，不殺 worker。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    calls = 0
    original = TargetApplicationService.mark_target_page_reloaded_if_owner

    def flaky_mark_page_reloaded(
        self: TargetApplicationService,
        *args: Any,
        **kwargs: Any,
    ) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        TargetApplicationService,
        "mark_target_page_reloaded_if_owner",
        flaky_mark_page_reloaded,
    )

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

    summary = asyncio.run(
        run_resident_main_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            scan_page=fake_scan_page,
            schedule_planner=TargetSchedulePlanner(),
            cycle_index=1,
        )
    )

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)

    assert calls == 2
    assert summary.success_count == 1
    assert summary.worker_health_ok is True
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.last_page_reloaded_at is not None


def test_resident_main_scan_sqlite_lock_requeues_without_failure(
    tmp_path: Path,
) -> None:
    """scan commit 遇到 SQLite lock 時不寫 target failure，保留待補掃狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def locked_scan_page(**_kwargs: Any) -> PostsScanSummary:
        raise sqlite3.OperationalError("database is locked")

    summary = asyncio.run(
        run_resident_main_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            scan_page=locked_scan_page,
            schedule_planner=TargetSchedulePlanner(),
            cycle_index=1,
        )
    )

    with SqliteApplicationContext(db_path) as app:
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        state = app.repositories.runtime_states.get(target.id)

    assert summary.failure_count == 0
    assert summary.skipped_count == 1
    assert summary.worker_health_ok is True
    assert latest_scan is None
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.scan_requested_at is not None
    assert state.consecutive_failure_count == 0


def test_resident_main_scan_commit_writer_lock_requeues_without_failure(
    tmp_path: Path,
) -> None:
    """scan finalize 的 BEGIN IMMEDIATE 遇 writer lock 時應快速 requeue。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def locked_finalize_scan_page(**kwargs: Any) -> PostsScanSummary:
        lock_connection = sqlite3.connect(db_path, timeout=0.1)
        lock_connection.execute("PRAGMA busy_timeout = 100")
        lock_connection.execute("BEGIN IMMEDIATE")
        try:
            record_skipped_scan(
                app=kwargs["app"],
                target=kwargs["target"],
                metadata={
                    "worker": "resident_main",
                    "skip_reason": SORT_ADJUST_UNCONFIRMED_REASON,
                },
                commit_guard=kwargs["commit_guard"],
            )
        finally:
            lock_connection.rollback()
            lock_connection.close()
        raise AssertionError("record_skipped_scan should fail while writer lock is held")

    summary = asyncio.run(
        run_resident_main_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            scan_page=locked_finalize_scan_page,
            schedule_planner=TargetSchedulePlanner(),
            cycle_index=1,
        )
    )

    with SqliteApplicationContext(db_path) as app:
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        state = app.repositories.runtime_states.get(target.id)

    assert summary.failure_count == 0
    assert summary.skipped_count == 1
    assert summary.worker_health_ok is True
    assert latest_scan is None
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.scan_requested_at is not None
    assert state.consecutive_failure_count == 0


def test_resident_main_unguarded_sqlite_lock_requeue_does_not_override_owner(
    tmp_path: Path,
) -> None:
    """commit_guard None 的補償不可覆蓋 RUNNING 或 ERROR target。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        running = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        errored = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app.services.targets.restart_target_monitoring(running.id)
        app.services.targets.restart_target_monitoring(errored.id)
        app.services.targets.try_mark_target_running(
            running.id,
            "other-worker",
            page_id="other-page",
        )
        app.services.targets.mark_target_error(errored.id, "terminal error")
        original_running_state = app.repositories.runtime_states.get(running.id)
        original_errored_state = app.repositories.runtime_states.get(errored.id)
        assert original_running_state is not None
        assert original_errored_state is not None

    async def unused_scan_page(**_kwargs: Any) -> None:
        """本測試只呼叫 retry-after helper，不會執行 scan。"""

    executor = ExecutorWorkerPool(
        options=ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
        ),
        page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
        target_queue=TargetQueue(),
        schedule_planner=TargetSchedulePlanner(),
        scan_page=unused_scan_page,
    )
    executor._write_target_retry_after_sqlite_lock(
        target_id=running.id,
        commit_guard=None,
    )
    executor._write_target_retry_after_sqlite_lock(
        target_id=errored.id,
        commit_guard=None,
    )

    with SqliteApplicationContext(db_path) as app:
        running_state = app.repositories.runtime_states.get(running.id)
        errored_state = app.repositories.runtime_states.get(errored.id)

    assert running_state is not None
    assert running_state.runtime_status == TargetRuntimeStatus.RUNNING
    assert running_state.active_worker_id == "other-worker"
    assert running_state.active_page_id == "other-page"
    assert running_state.scan_requested_at == original_running_state.scan_requested_at
    assert errored_state is not None
    assert errored_state.runtime_status == TargetRuntimeStatus.ERROR
    assert errored_state.scan_requested_at == original_errored_state.scan_requested_at


def test_resident_main_heartbeat_sqlite_lock_does_not_cancel_scan(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """heartbeat DB lock exhaustion 應跳過該次 heartbeat，不取消 scan task。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    original_retry = resident_main_executor_module.run_sqlite_operation_with_retry_async

    async def fake_retry(operation: Any, **kwargs: Any) -> object:
        if kwargs["operation_name"] == "record_target_heartbeat_if_owner":
            raise sqlite3.OperationalError("database is locked")
        return await original_retry(operation, **kwargs)

    monkeypatch.setattr(
        resident_main_executor_module,
        "run_sqlite_operation_with_retry_async",
        fake_retry,
    )

    async def slow_scan_page(**kwargs: Any) -> PostsScanSummary:
        await asyncio.sleep(0.05)
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    summary = asyncio.run(
        run_resident_main_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
                heartbeat_interval_seconds=0.01,
                scan_timeout_seconds=1,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            scan_page=slow_scan_page,
            schedule_planner=TargetSchedulePlanner(),
            cycle_index=1,
        )
    )

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)

    assert summary.success_count == 1
    assert summary.failure_count == 0
    assert summary.worker_health_ok is True
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE


def test_resident_main_display_next_due_sqlite_lock_does_not_fail_target(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    caplog: Any,
) -> None:
    """display next due read model lock 只略過顯示更新，不影響 target scan。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    def locked_display_next_due(
        _db_path: Path,
        _target_id: str,
        _due_at: object,
    ) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        resident_main_module,
        "_write_display_next_due_at_best_effort",
        locked_display_next_due,
    )

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

    planner = TargetSchedulePlanner(
        on_display_next_due_changed=_publish_display_next_due_at(db_path)
    )
    with caplog.at_level(logging.WARNING, logger="facebook_monitor.worker.resident_main"):
        summary = asyncio.run(
            run_resident_main_cycle(
                options=ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=tmp_path / "profile",
                    interval_seconds=0,
                ),
                page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
                scan_page=fake_scan_page,
                schedule_planner=planner,
                cycle_index=1,
            )
        )

    with SqliteApplicationContext(db_path) as app:
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        state = app.repositories.runtime_states.get(target.id)

    for _ in range(100):
        if "display next due update skipped: database locked" in caplog.text:
            break
        time.sleep(0.01)

    assert "display next due update skipped: database locked" in caplog.text
    assert summary.success_count == 1
    assert summary.failure_count == 0
    assert summary.worker_health_ok is True
    assert latest_scan is None
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE


def test_display_next_due_best_effort_skips_held_writer_lock(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """display next due raw read model update 遇 held writer lock 時直接略過。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    lock_connection = sqlite3.connect(db_path, timeout=0.1)
    lock_connection.execute("PRAGMA busy_timeout = 100")
    lock_connection.execute("BEGIN IMMEDIATE")
    try:
        with caplog.at_level(logging.WARNING, logger="facebook_monitor.worker.resident_main"):
            resident_main_module._write_display_next_due_at_best_effort(
                db_path,
                target.id,
                utc_now(),
            )
    finally:
        lock_connection.rollback()
        lock_connection.close()

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)

    assert "display next due update skipped: database locked" in caplog.text
    assert state is not None
    assert state.display_next_due_at is None


def test_resident_main_retries_failure_finalize_sqlite_lock(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """failure finalize 遇到 SQLite lock 時應重開 context 重試同一筆 failure。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    calls = 0
    original = scan_failure_finalize_module.record_guarded_scan_failure

    def flaky_record_guarded_scan_failure(**kwargs: Any) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return original(**kwargs)

    monkeypatch.setattr(
        scan_failure_finalize_module,
        "record_guarded_scan_failure",
        flaky_record_guarded_scan_failure,
    )

    async def failing_scan_page(**_kwargs: Any) -> PostsScanSummary:
        raise AsyncPlaywrightError(
            "Page.evaluate: Execution context was destroyed, most likely because of a navigation."
        )

    summary = asyncio.run(
        run_resident_main_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            scan_page=failing_scan_page,
            schedule_planner=TargetSchedulePlanner(),
            cycle_index=1,
        )
    )

    with SqliteApplicationContext(db_path) as app:
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        state = app.repositories.runtime_states.get(target.id)

    assert calls == 2
    assert summary.failure_count == 1
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.FAILED
    assert latest_scan.metadata["reason"] == PAGE_LOAD_TIMEOUT_REASON
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.consecutive_failure_count == 1
