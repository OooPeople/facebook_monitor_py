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
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import PAGE_LOAD_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.persistence.repositories.target_runtime_state import (
    TargetRuntimeStateRepository,
)
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker import resident_main as resident_main_module
from facebook_monitor.worker import resident_main_executor as resident_main_executor_module
from facebook_monitor.worker.resident_main import _publish_display_next_due_at
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.resident_scan_db import RESIDENT_SCAN_DB_BUSY_TIMEOUT_MS
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from tests.worker.scan_finalize_test_helpers import record_protective_skip_for_test
from facebook_monitor.worker.scan_finalize import scan_commit_guard_from_runtime_state
from facebook_monitor.worker import scan_failure_finalize as scan_failure_finalize_module
from tests.worker.resident_main_cycle_harness import (
    run_resident_main_cycle_harness as run_resident_main_cycle,
)


from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext
from tests.worker.resident_main_test_helpers import as_async_scan_callable
from tests.worker.resident_main_test_helpers import build_success_scan_result_for_test


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
    original = TargetApplicationService.guarded_mark_target_page_reloaded

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
        "guarded_mark_target_page_reloaded",
        flaky_mark_page_reloaded,
    )

    async def fake_scan_page(**kwargs: Any) -> object:
        return build_success_scan_result_for_test(
            target=kwargs["target"],
            page_url=kwargs["page"].url,
        )

    summary = asyncio.run(
        run_resident_main_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            scan_page=as_async_scan_callable(fake_scan_page),
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
            scan_page=as_async_scan_callable(locked_scan_page),
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
            record_protective_skip_for_test(
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
        raise AssertionError("record_protective_skip_for_test should fail while writer lock is held")

    summary = asyncio.run(
        run_resident_main_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            scan_page=as_async_scan_callable(locked_finalize_scan_page),
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


def test_resident_main_scan_connection_uses_short_busy_timeout(
    tmp_path: Path,
) -> None:
    """resident scan callable 取得的 DB connection 應使用短 busy timeout。"""

    db_path = tmp_path / "app.db"
    observed_timeout_ms = -1
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    async def inspect_scan_db_timeout(**kwargs: Any) -> object:
        nonlocal observed_timeout_ms
        row = kwargs["app"].repositories.runtime_states.connection.execute(
            "PRAGMA busy_timeout"
        ).fetchone()
        observed_timeout_ms = int(row[0])
        return build_success_scan_result_for_test(
            target=kwargs["target"],
            page_url=kwargs["page"].url,
        )

    summary = asyncio.run(
        run_resident_main_cycle(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                interval_seconds=0,
            ),
            page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
            scan_page=as_async_scan_callable(inspect_scan_db_timeout),
            schedule_planner=TargetSchedulePlanner(),
            cycle_index=1,
        )
    )

    assert summary.success_count == 1
    assert observed_timeout_ms == RESIDENT_SCAN_DB_BUSY_TIMEOUT_MS


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
        app.services.targets.try_claim_target_running(
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
        scan_page=as_async_scan_callable(unused_scan_page),
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


def test_resident_main_sqlite_lock_requeue_begins_transaction_before_read(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """sqlite lock 補償需先取得 write lock，再讀取 target/runtime guard 狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        running = app.services.targets.try_claim_target_running(
            target.id,
            "worker-current",
            page_id="page-current",
        )
        assert running is not None
        guard = scan_commit_guard_from_runtime_state(running)

    events: list[str] = []
    original_begin = resident_main_executor_module.begin_scan_commit_transaction
    original_get = TargetRepository.get
    original_runtime_get = TargetRuntimeStateRepository.get
    original_guard_check = resident_main_executor_module.target_matches_scan_commit_guard

    def spy_begin(*args: Any, **kwargs: Any) -> object:
        events.append("begin")
        return original_begin(*args, **kwargs)

    def spy_get(self: TargetRepository, target_id: str) -> object:
        events.append("target_get")
        return original_get(self, target_id)

    def spy_runtime_get(
        self: TargetRuntimeStateRepository,
        target_id: str,
    ) -> object:
        events.append("runtime_get")
        return original_runtime_get(self, target_id)

    def spy_guard_check(*args: Any, **kwargs: Any) -> object:
        events.append("guard_check")
        return original_guard_check(*args, **kwargs)

    monkeypatch.setattr(
        resident_main_executor_module,
        "begin_scan_commit_transaction",
        spy_begin,
    )
    monkeypatch.setattr(TargetRepository, "get", spy_get)
    monkeypatch.setattr(TargetRuntimeStateRepository, "get", spy_runtime_get)
    monkeypatch.setattr(
        resident_main_executor_module,
        "target_matches_scan_commit_guard",
        spy_guard_check,
    )

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
        scan_page=as_async_scan_callable(unused_scan_page),
    )
    executor._write_target_retry_after_sqlite_lock(
        target_id=target.id,
        commit_guard=guard,
    )

    assert events[0] == "begin"
    begin_index = events.index("begin")
    assert any(event == "target_get" for event in events)
    assert any(event == "runtime_get" for event in events)
    assert any(event == "guard_check" for event in events)
    assert all(
        index > begin_index
        for index, event in enumerate(events)
        if event in {"target_get", "runtime_get", "guard_check"}
    )


def test_resident_main_guarded_sqlite_lock_requeue_ignores_stale_owner(
    tmp_path: Path,
) -> None:
    """sqlite lock 補償若帶舊 owner guard，不可覆蓋新的 running attempt。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        old_running = app.services.targets.try_claim_target_running(
            target.id,
            "worker-old",
            page_id="page-old",
        )
        assert old_running is not None
        old_guard = scan_commit_guard_from_runtime_state(old_running)
        app.services.targets.restart_target_monitoring(target.id)
        new_running = app.services.targets.try_claim_target_running(
            target.id,
            "worker-new",
            page_id="page-new",
        )
        assert new_running is not None

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
        scan_page=as_async_scan_callable(unused_scan_page),
    )
    executor._write_target_retry_after_sqlite_lock(
        target_id=target.id,
        commit_guard=old_guard,
    )

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == "worker-new"
    assert state.active_page_id == "page-new"
    assert state.last_started_at == new_running.last_started_at


def test_resident_main_guarded_sqlite_lock_requeue_accepts_matching_owner(
    tmp_path: Path,
) -> None:
    """sqlite lock 補償只在 owner/page/start identity 符合時排入下一輪。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        running = app.services.targets.try_claim_target_running(
            target.id,
            "worker-current",
            page_id="page-current",
        )
        assert running is not None
        guard = scan_commit_guard_from_runtime_state(running)

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
        scan_page=as_async_scan_callable(unused_scan_page),
    )
    executor._write_target_retry_after_sqlite_lock(
        target_id=target.id,
        commit_guard=guard,
    )

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.active_worker_id == ""
    assert state.active_page_id == ""
    assert state.scan_requested_at is not None


def test_resident_main_guarded_sqlite_lock_requeue_respects_stopped_target(
    tmp_path: Path,
) -> None:
    """matching guard 也不可讓已停止 target 被 sqlite lock 補償重新排程。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        running = app.services.targets.try_claim_target_running(
            target.id,
            "worker-current",
            page_id="page-current",
        )
        assert running is not None
        guard = scan_commit_guard_from_runtime_state(running)
        app.services.targets.pause_target_monitoring(target.id)
        stopped_state = app.repositories.runtime_states.get(target.id)
        assert stopped_state is not None

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
        scan_page=as_async_scan_callable(unused_scan_page),
    )
    executor._write_target_retry_after_sqlite_lock(
        target_id=target.id,
        commit_guard=guard,
    )

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)

    assert state is not None
    assert state.runtime_status == stopped_state.runtime_status
    assert state.active_worker_id == stopped_state.active_worker_id
    assert state.active_page_id == stopped_state.active_page_id
    assert state.scan_requested_at == stopped_state.scan_requested_at


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
        if kwargs["operation_name"] == "guarded_record_target_heartbeat":
            raise sqlite3.OperationalError("database is locked")
        return await original_retry(operation, **kwargs)

    monkeypatch.setattr(
        resident_main_executor_module,
        "run_sqlite_operation_with_retry_async",
        fake_retry,
    )

    async def slow_scan_page(**kwargs: Any) -> object:
        await asyncio.sleep(0.05)
        return build_success_scan_result_for_test(
            target=kwargs["target"],
            page_url=kwargs["page"].url,
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
            scan_page=as_async_scan_callable(slow_scan_page),
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

    async def fake_scan_page(**kwargs: Any) -> object:
        return build_success_scan_result_for_test(
            target=kwargs["target"],
            page_url=kwargs["page"].url,
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
                scan_page=as_async_scan_callable(fake_scan_page),
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
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.SUCCESS
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
    original = scan_failure_finalize_module.record_guarded_scan_failure_result

    def flaky_record_guarded_scan_failure_result(**kwargs: Any) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return original(**kwargs)

    monkeypatch.setattr(
        scan_failure_finalize_module,
        "record_guarded_scan_failure_result",
        flaky_record_guarded_scan_failure_result,
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
            scan_page=as_async_scan_callable(failing_scan_page),
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
