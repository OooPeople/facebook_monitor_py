"""Resident main worker tests。"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Any

from pytest import MonkeyPatch
from playwright.async_api import Error as AsyncPlaywrightError

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import TargetConfigPatch
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import TargetCoverImageRefreshStatus
from facebook_monitor.core.models import TargetCoverImageRefreshResult
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.resident_main import run_resident_main_loop
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions


from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext
from tests.worker.resident_main_test_helpers import RuntimeRefreshMetadataBrowserContext
from tests.worker.resident_main_test_helpers import RuntimeClosedOnPausedBrowserContext
from tests.worker.resident_main_test_helpers import _stub_runtime_outbox_dispatch


def test_resident_main_loop_keeps_non_active_metadata_runtime_failure_pending(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """非 active metadata runtime failure 不可重啟 context 或寫 scan failure。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[RuntimeRefreshMetadataBrowserContext] = []
    stop_event = asyncio.Event()

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.mark_target_metadata_refresh_pending(target.id)

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
    ) -> RuntimeRefreshMetadataBrowserContext:
        context = RuntimeRefreshMetadataBrowserContext(fail_new_page=not contexts)
        contexts.append(context)
        return context

    def stop_after_first_cycle(_summary: Any) -> None:
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
                ),
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_first_cycle,
            ),
            timeout=2,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert len(contexts) == 1
    assert contexts[0].closed is True
    assert updated is not None
    assert updated.metadata_status == TargetMetadataStatus.PENDING
    assert updated.metadata_error == ""
    assert latest_scan is None


def test_resident_main_loop_keeps_non_active_cover_runtime_failure_pending(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """非 active cover runtime failure 不可重啟 context 或寫 scan failure。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[RuntimeRefreshMetadataBrowserContext] = []
    stop_event = asyncio.Event()

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )

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
    ) -> RuntimeRefreshMetadataBrowserContext:
        context = RuntimeRefreshMetadataBrowserContext(fail_new_page=not contexts)
        contexts.append(context)
        return context

    def stop_after_first_cycle(_summary: Any) -> None:
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
                ),
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_first_cycle,
            ),
            timeout=2,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.cover_image_refreshes.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

    assert len(contexts) == 1
    assert contexts[0].closed is True
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.PENDING
    assert latest_scan is None


def test_active_metadata_runtime_failure_notifies_after_scan_retries(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """metadata runtime failure 要接回 target retry streak 與 outbox。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    dispatch_calls = _stub_runtime_outbox_dispatch(monkeypatch)
    contexts: list[RuntimeRefreshMetadataBrowserContext] = []
    stop_event = asyncio.Event()
    scan_calls = 0

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="Runtime metadata",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.clear_target_scan_request(target.id)
        app.services.targets.mark_target_metadata_refresh_pending(target.id)

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
    ) -> RuntimeRefreshMetadataBrowserContext:
        context = RuntimeRefreshMetadataBrowserContext(fail_new_page=True)
        contexts.append(context)
        return context

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        nonlocal scan_calls
        scan_calls += 1
        raise AsyncPlaywrightError("Target page, context or browser has been closed")

    def stop_after_terminal_failure(_summary: Any) -> None:
        with SqliteApplicationContext(db_path) as app:
            state = app.repositories.runtime_states.get(target.id)
        if state is not None and state.runtime_status == TargetRuntimeStatus.ERROR:
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
                ),
                scan_page=fake_scan_page,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_terminal_failure,
            ),
            timeout=3,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        entries = app.repositories.notification_outbox.list_pending()
        run_count = app.repositories.scan_runs.connection.execute(
            "SELECT COUNT(*) FROM scan_runs WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]

    assert 3 <= len(contexts) <= 4
    assert all(context.closed for context in contexts)
    assert scan_calls == 0
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.ERROR
    assert state.consecutive_failure_reason == SCHEDULER_RUNTIME_REASON
    assert state.consecutive_failure_count == 3
    assert latest_scan is not None
    assert latest_scan.metadata["reason"] == SCHEDULER_RUNTIME_REASON
    assert run_count == 3
    assert len(entries) == 1
    assert entries[0].failure_reason == SCHEDULER_RUNTIME_REASON
    assert entries[0].failure_count == 3
    assert db_path in dispatch_calls


def test_active_cover_runtime_failure_defers_refresh_until_scan_retry(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """cover runtime failure 後，要先讓正式 scan retry，不可反覆擋住掃描。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[RuntimeRefreshMetadataBrowserContext] = []
    stop_event = asyncio.Event()
    scan_calls = 0

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.clear_target_scan_request(target.id)
        app.services.targets.request_target_cover_image_refresh(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )

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
    ) -> RuntimeRefreshMetadataBrowserContext:
        context = RuntimeRefreshMetadataBrowserContext(fail_new_page=not contexts)
        contexts.append(context)
        return context

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        nonlocal scan_calls
        scan_calls += 1
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    def stop_after_scan_success(summary: Any) -> None:
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
                    scheduler_tick_seconds=0,
                ),
                scan_page=fake_scan_page,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_scan_success,
            ),
            timeout=3,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        cover_state = app.repositories.cover_image_refreshes.get(target.id)

    assert len(contexts) == 2
    assert contexts[0].closed is True
    assert contexts[1].closed is True
    assert scan_calls == 1
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.consecutive_failure_reason == ""
    assert latest_scan is not None
    assert latest_scan.metadata["reason"] == SCHEDULER_RUNTIME_REASON
    assert cover_state is not None
    assert cover_state.status == TargetCoverImageRefreshStatus.IDLE
    assert cover_state.last_result == TargetCoverImageRefreshResult.SUCCEEDED_CHANGED


def test_paused_metadata_runtime_failure_does_not_starve_active_scan(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """paused target 的 metadata job 不可重啟 runtime 並擋住 active scan。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[RuntimeRefreshMetadataBrowserContext] = []
    stop_event = asyncio.Event()
    scan_calls = 0

    with SqliteApplicationContext(db_path) as app:
        paused = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="paused",
                canonical_url="https://www.facebook.com/groups/paused",
            )
        )
        active = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="active",
                canonical_url="https://www.facebook.com/groups/active",
            )
        )
        app.services.targets.restart_target_monitoring(paused.id)
        app.services.targets.clear_target_scan_request(paused.id)
        app.services.targets.mark_target_metadata_refresh_pending(paused.id)
        app.services.targets.pause_target_monitoring(paused.id)
        app.services.targets.restart_target_monitoring(active.id)

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
    ) -> RuntimeClosedOnPausedBrowserContext:
        context = RuntimeClosedOnPausedBrowserContext()
        contexts.append(context)
        return context

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        nonlocal scan_calls
        scan_calls += 1
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    def stop_after_scan_success(summary: Any) -> None:
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
                    scheduler_tick_seconds=0,
                ),
                scan_page=fake_scan_page,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_scan_success,
            ),
            timeout=2,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        paused_state = app.repositories.runtime_states.get(paused.id)
        active_state = app.repositories.runtime_states.get(active.id)
        paused_latest_scan = app.repositories.scan_runs.latest_by_target(paused.id)

    assert len(contexts) == 1
    assert contexts[0].closed is True
    assert scan_calls == 1
    assert paused_state is not None
    assert active_state is not None
    assert paused_state.runtime_status == TargetRuntimeStatus.IDLE
    assert active_state.runtime_status == TargetRuntimeStatus.IDLE
    assert paused_latest_scan is None


def test_paused_cover_runtime_failure_does_not_starve_active_scan(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """paused target 的 cover job 不可重啟 runtime 並擋住 active scan。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    contexts: list[RuntimeClosedOnPausedBrowserContext] = []
    stop_event = asyncio.Event()
    scan_calls = 0

    with SqliteApplicationContext(db_path) as app:
        paused = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="paused",
                canonical_url="https://www.facebook.com/groups/paused",
                group_cover_image_url="https://scontent.xx.fbcdn.net/paused.jpg",
            )
        )
        active = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="active",
                canonical_url="https://www.facebook.com/groups/active",
            )
        )
        app.services.targets.restart_target_monitoring(paused.id)
        app.services.targets.clear_target_scan_request(paused.id)
        app.services.targets.request_target_cover_image_refresh(
            paused.id,
            reported_url="https://scontent.xx.fbcdn.net/paused.jpg",
            min_interval_seconds=21600,
        )
        app.services.targets.pause_target_monitoring(paused.id)
        app.services.targets.restart_target_monitoring(active.id)

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
    ) -> RuntimeClosedOnPausedBrowserContext:
        context = RuntimeClosedOnPausedBrowserContext()
        contexts.append(context)
        return context

    async def fake_scan_page(**kwargs: Any) -> PostsScanSummary:
        nonlocal scan_calls
        scan_calls += 1
        return PostsScanSummary(
            target_id=kwargs["target"].id,
            url=kwargs["page"].url,
            item_count=0,
            new_count=0,
            matched_count=0,
            scan_run_id=1,
            round_stats=(),
        )

    def stop_after_scan_success(summary: Any) -> None:
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
                    scheduler_tick_seconds=0,
                ),
                scan_page=fake_scan_page,
                should_stop=lambda: stop_event.is_set(),
                on_cycle=stop_after_scan_success,
            ),
            timeout=2,
        )

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        paused_state = app.repositories.runtime_states.get(paused.id)
        active_state = app.repositories.runtime_states.get(active.id)
        paused_latest_scan = app.repositories.scan_runs.latest_by_target(paused.id)
        cover_state = app.repositories.cover_image_refreshes.get(paused.id)

    assert len(contexts) == 1
    assert contexts[0].closed is True
    assert scan_calls == 1
    assert paused_state is not None
    assert active_state is not None
    assert paused_state.runtime_status == TargetRuntimeStatus.IDLE
    assert active_state.runtime_status == TargetRuntimeStatus.IDLE
    assert paused_latest_scan is None
    assert cover_state is not None
    assert cover_state.status == TargetCoverImageRefreshStatus.PENDING


def test_runtime_restart_pending_retry_preserves_failure_streak(
    tmp_path: Path,
) -> None:
    """runtime restart 取消 queued retry 時，不可清掉既有 failure streak。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="queued",
                canonical_url="https://www.facebook.com/groups/queued",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        state = app.services.targets.mark_target_queued(target.id, "manual")
        app.repositories.runtime_states.save(
            replace(
                state,
                scan_requested_at=None,
                consecutive_failure_reason=SCHEDULER_RUNTIME_REASON,
                consecutive_failure_count=2,
            )
        )

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試只檢查 retry helper，不會執行掃描。"""

        raise AssertionError("scan should not run")

    executor = ExecutorWorkerPool(
        options=ResidentRuntimeOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
        ),
        page_pool=AsyncResidentPagePool(FakeAsyncBrowserContext()),
        target_queue=TargetQueue(),
        schedule_planner=TargetSchedulePlanner(),
        scan_page=scan_page,
    )
    executor._request_target_retry_after_runtime_restart(target.id)

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.runtime_states.get(target.id)

    assert updated is not None
    assert updated.runtime_status == TargetRuntimeStatus.IDLE
    assert updated.scan_requested_at is not None
    assert updated.consecutive_failure_reason == SCHEDULER_RUNTIME_REASON
    assert updated.consecutive_failure_count == 2
