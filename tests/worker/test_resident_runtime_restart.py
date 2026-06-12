"""Resident main worker tests。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from pytest import MonkeyPatch
from playwright.async_api import Error as AsyncPlaywrightError

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.worker.resident_main import run_resident_main_loop
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.scan_finalize import record_skipped_scan


from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext


def test_resident_main_loop_restarts_browser_context_on_scheduler_runtime(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """scheduler_runtime_restart 要關閉舊 persistent context 並建立新 context。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[FakeAsyncBrowserContext] = []
    scan_calls = 0
    stop_event = asyncio.Event()

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    class FakePlaywrightManager:
        """測試用 async_playwright context manager。"""

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    async def fake_launch_persistent_context_async(
        _playwright: object,
        _options: object,
    ) -> FakeAsyncBrowserContext:
        context = FakeAsyncBrowserContext()
        contexts.append(context)
        return context

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        nonlocal scan_calls
        scan_calls += 1
        if scan_calls == 1:
            raise AsyncPlaywrightError("Target page, context or browser has been closed")
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch_persistent_context_async,
    )

    def stop_after_success(summary: Any) -> None:
        if summary.success_count > 0:
            stop_event.set()

    async def run_test() -> None:
        await asyncio.wait_for(
            run_resident_main_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    scheduler_tick_seconds=0,
                ),
                scan_page=fake_scan_page,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_success,
            ),
            timeout=2,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert len(contexts) == 2
    assert contexts[0].closed is True
    assert contexts[1].closed is True
    assert scan_calls == 2
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.consecutive_failure_reason == ""
    assert latest_scan is not None
    assert latest_scan.metadata["reason"] == SCHEDULER_RUNTIME_REASON


def test_resident_main_loop_runtime_restart_is_not_worker_pool_unhealthy(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    caplog: Any,
) -> None:
    """runtime restart request 不應被誤記成 worker pool unhealthy。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[FakeAsyncBrowserContext] = []
    summaries: list[Any] = []
    stop_event = asyncio.Event()
    refresh_calls = 0
    caplog.set_level(logging.WARNING, logger="facebook_monitor.worker.resident_main")

    class FakePlaywrightManager:
        """測試用 async_playwright context manager。"""

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    async def fake_launch_persistent_context_async(
        _playwright: object,
        _options: object,
    ) -> FakeAsyncBrowserContext:
        context = FakeAsyncBrowserContext()
        contexts.append(context)
        return context

    async def fake_refresh_requested_target_metadata(**kwargs: Any) -> int:
        nonlocal refresh_calls
        refresh_calls += 1
        kwargs["request_runtime_restart"]()
        return 0

    async def unused_scan_page(**_kwargs: Any) -> PostsScanSummary:
        """本測試不會掃描 target。"""

        raise AssertionError("scan should not run")

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch_persistent_context_async,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_maintenance.refresh_requested_target_metadata",
        fake_refresh_requested_target_metadata,
    )

    def stop_after_cycle(summary: Any) -> None:
        summaries.append(summary)
        stop_event.set()

    async def run_test() -> None:
        await asyncio.wait_for(
            run_resident_main_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    scheduler_tick_seconds=0,
                ),
                scan_page=unused_scan_page,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_cycle,
            ),
            timeout=2,
        )

    asyncio.run(run_test())

    assert refresh_calls == 1
    assert len(contexts) == 1
    assert contexts[0].closed is True
    assert summaries[0].worker_health_ok is True
    assert summaries[0].resident_browser_alive is False
    assert "worker_pool_unhealthy" not in caplog.text


def test_resident_main_loop_rebuilds_full_pool_after_worker_task_death(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    caplog: Any,
) -> None:
    """worker task 在 complete 前死亡時，外層 loop 要重建完整 executor pool。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[FakeAsyncBrowserContext] = []
    summaries_by_context: list[tuple[int, Any]] = []
    stop_event = asyncio.Event()
    release_failed = False
    caplog.set_level(logging.INFO, logger="facebook_monitor.worker")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    class FakePlaywrightManager:
        """測試用 async_playwright context manager。"""

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    class ReleaseFailsOncePagePool(AsyncResidentPagePool):
        """第一個 runtime 的 page release 失敗，用來模擬 complete 前 worker death。"""

        async def release_if_page_id(
            self,
            target_id: str,
            page_id: str,
            *,
            current_url: str = "",
        ) -> bool:
            nonlocal release_failed
            if not release_failed:
                release_failed = True
                raise RuntimeError("page release failed before queue complete")
            return await super().release_if_page_id(
                target_id,
                page_id,
                current_url=current_url,
            )

    async def fake_launch_persistent_context_async(
        _playwright: object,
        _options: object,
    ) -> FakeAsyncBrowserContext:
        context = FakeAsyncBrowserContext()
        contexts.append(context)
        return context

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

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch_persistent_context_async,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.AsyncResidentPagePool",
        ReleaseFailsOncePagePool,
    )

    def stop_after_second_runtime(summary: Any) -> None:
        summaries_by_context.append((len(contexts), summary))
        if len(contexts) >= 2:
            stop_event.set()

    async def run_test() -> None:
        await asyncio.wait_for(
            run_resident_main_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    scheduler_tick_seconds=0,
                    max_concurrent_scans=4,
                ),
                scan_page=fake_scan_page,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_second_runtime,
            ),
            timeout=2,
        )

    asyncio.run(run_test())

    second_runtime_summary = next(
        summary for context_count, summary in summaries_by_context if context_count == 2
    )
    assert release_failed is True
    assert len(contexts) == 2
    assert contexts[0].closed is True
    assert contexts[1].closed is True
    assert second_runtime_summary.worker_health_ok is True
    assert set(second_runtime_summary.worker_statuses) == {
        "resident-slot-1:running",
        "resident-slot-2:running",
        "resident-slot-3:running",
        "resident-slot-4:running",
    }
    assert caplog.text.count("resident_executor_start max_concurrent_scans=4") == 2
    assert "resident_executor_worker_stopped worker_id=resident-slot-" in caplog.text
    assert "reason=exception exception_class=RuntimeError" in caplog.text


def test_resident_main_loop_final_drain_exits_on_worker_task_death(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    caplog: Any,
) -> None:
    """max_cycles 進入 final drain 後，worker 死亡仍要喚醒 shutdown。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[FakeAsyncBrowserContext] = []
    allow_scan_finish = asyncio.Event()
    release_failed = False
    caplog.set_level(logging.INFO, logger="facebook_monitor.worker")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    class FakePlaywrightManager:
        """測試用 async_playwright context manager。"""

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    class ReleaseFailsOncePagePool(AsyncResidentPagePool):
        """第一次 release 失敗，模擬 queue complete 前 worker death。"""

        async def release_if_page_id(
            self,
            target_id: str,
            page_id: str,
            *,
            current_url: str = "",
        ) -> bool:
            nonlocal release_failed
            if not release_failed:
                release_failed = True
                raise RuntimeError("page release failed before queue complete")
            return await super().release_if_page_id(
                target_id,
                page_id,
                current_url=current_url,
            )

    async def fake_launch_persistent_context_async(
        _playwright: object,
        _options: object,
    ) -> FakeAsyncBrowserContext:
        context = FakeAsyncBrowserContext()
        contexts.append(context)
        return context

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        await allow_scan_finish.wait()
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch_persistent_context_async,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.AsyncResidentPagePool",
        ReleaseFailsOncePagePool,
    )

    def release_after_first_summary(_summary: Any) -> None:
        allow_scan_finish.set()

    async def run_test() -> None:
        await asyncio.wait_for(
            run_resident_main_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    scheduler_tick_seconds=0,
                    max_concurrent_scans=4,
                    max_cycles=1,
                ),
                scan_page=fake_scan_page,
                on_cycle=release_after_first_summary,
            ),
            timeout=2,
        )

    asyncio.run(run_test())

    assert release_failed is True
    assert len(contexts) == 1
    assert contexts[0].closed is True
    assert "resident_executor_worker_stopped worker_id=resident-slot-" in caplog.text
    assert "reason=exception exception_class=RuntimeError" in caplog.text


def test_resident_main_loop_runtime_restart_wakes_scheduler_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """sleep 期間偵測到 runtime restart 時，要立刻關閉並重建 context。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[FakeAsyncBrowserContext] = []
    scan_calls = 0
    scan_ready = asyncio.Event()
    release_scan_failure = asyncio.Event()
    sleep_started = asyncio.Event()
    sleep_cancelled = asyncio.Event()
    second_scan_done = asyncio.Event()
    stop_event = asyncio.Event()

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)

    class FakePlaywrightManager:
        """測試用 async_playwright context manager。"""

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    async def fake_launch_persistent_context_async(
        _playwright: object,
        _options: object,
    ) -> FakeAsyncBrowserContext:
        context = FakeAsyncBrowserContext()
        contexts.append(context)
        return context

    async def fake_sleep(_seconds: float) -> None:
        sleep_started.set()
        await scan_ready.wait()
        release_scan_failure.set()
        if len(contexts) > 1:
            await second_scan_done.wait()
            return
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            sleep_cancelled.set()
            raise

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        nonlocal scan_calls
        scan_calls += 1
        if scan_calls == 1:
            scan_ready.set()
            await sleep_started.wait()
            await release_scan_failure.wait()
            raise AsyncPlaywrightError("Target page, context or browser has been closed")
        second_scan_done.set()
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    def stop_after_success(summary: Any) -> None:
        if summary.success_count > 0:
            stop_event.set()

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch_persistent_context_async,
    )

    async def run_test() -> None:
        await asyncio.wait_for(
            run_resident_main_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    scheduler_tick_seconds=300,
                ),
                scan_page=fake_scan_page,
                sleep_fn=fake_sleep,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_success,
            ),
            timeout=2,
        )

    asyncio.run(run_test())

    assert sleep_cancelled.is_set()
    assert len(contexts) == 2
    assert contexts[0].closed is True
    assert contexts[1].closed is True
    assert scan_calls == 2


def test_resident_main_loop_retries_other_running_targets_after_runtime_restart(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """runtime restart 取消的其他 running targets 要在新 context 立即補掃。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[FakeAsyncBrowserContext] = []
    first_target_started = asyncio.Event()
    second_target_started = asyncio.Event()
    stop_event = asyncio.Event()
    success_counts: dict[str, int] = {}

    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="First",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
                group_name="Second",
            )
        )
        app.services.targets.restart_target_monitoring(first.id)
        app.services.targets.restart_target_monitoring(second.id)

    class FakePlaywrightManager:
        """測試用 async_playwright context manager。"""

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    async def fake_launch_persistent_context_async(
        _playwright: object,
        _options: object,
    ) -> FakeAsyncBrowserContext:
        context = FakeAsyncBrowserContext()
        contexts.append(context)
        return context

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        target = kwargs["target"]
        if target.id == first.id and not first_target_started.is_set():
            first_target_started.set()
            await second_target_started.wait()
            raise AsyncPlaywrightError("Target page, context or browser has been closed")
        if target.id == second.id and not second_target_started.is_set():
            second_target_started.set()
            await asyncio.sleep(10)
        success_counts[target.id] = success_counts.get(target.id, 0) + 1
        return PostsScanSummary(
            target_id=target.id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    def stop_after_both_succeed(summary: Any) -> None:
        if summary.success_count and all(
            success_counts.get(target.id, 0) > 0 for target in (first, second)
        ):
            stop_event.set()

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.acquire_profile_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.async_playwright",
        lambda: FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.launch_persistent_context_async",
        fake_launch_persistent_context_async,
    )

    async def run_test() -> None:
        await asyncio.wait_for(
            run_resident_main_loop(
                ResidentRuntimeOptions(
                    db_path=db_path,
                    profile_dir=profile_dir,
                    interval_seconds=0,
                    scheduler_tick_seconds=0,
                    max_concurrent_scans=2,
                ),
                scan_page=fake_scan_page,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_both_succeed,
            ),
            timeout=3,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        first_state = app.repositories.runtime_states.get(first.id)
        second_state = app.repositories.runtime_states.get(second.id)
        first_latest = app.repositories.scan_runs.latest_by_target(first.id)
        second_latest = app.repositories.scan_runs.latest_by_target(second.id)

    assert len(contexts) == 2
    assert contexts[0].closed is True
    assert contexts[1].closed is True
    assert success_counts == {first.id: 1, second.id: 1}
    assert first_state is not None
    assert second_state is not None
    assert first_state.runtime_status == TargetRuntimeStatus.IDLE
    assert second_state.runtime_status == TargetRuntimeStatus.IDLE
    assert first_latest is not None
    assert second_latest is not None
    assert first_latest.metadata["reason"] == SCHEDULER_RUNTIME_REASON
    assert second_latest.metadata["reason"] == SCHEDULER_RUNTIME_REASON
