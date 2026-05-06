"""Web UI scheduler session tests。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event

from facebook_monitor.scheduler.loop import SchedulerOptions
from facebook_monitor.webapp.scheduler_session import AutoScanMode
from facebook_monitor.webapp.scheduler_session import BackgroundSchedulerManager
from facebook_monitor.webapp.scheduler_session import SchedulerSessionOptions
from facebook_monitor.worker.resident import ResidentCycleSummary
from facebook_monitor.worker.resident import ResidentWorkerOptions


def test_background_scheduler_manager_runs_one_shot_mode(tmp_path: Path) -> None:
    """背景 manager 在 one-shot 模式會呼叫 scheduler runner。"""

    calls: list[SchedulerOptions] = []

    def fake_runner(options: SchedulerOptions) -> object:
        """記錄 one-shot runner 呼叫。"""

        calls.append(options)
        return object()

    manager = BackgroundSchedulerManager(
        runner=fake_runner,
        wait_fn=lambda _event, _seconds: True,
    )
    manager.start(
        SchedulerSessionOptions(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
            auto_scan_mode=AutoScanMode.ONE_SHOT,
            interval_seconds=30,
            scheduler_tick_seconds=30,
        )
    )
    assert manager.thread is not None
    manager.thread.join(timeout=2)

    assert len(calls) == 1
    assert calls[0].interval_seconds == 0
    assert manager.state().auto_scan_mode == AutoScanMode.ONE_SHOT


def test_background_scheduler_manager_runs_resident_mode(tmp_path: Path) -> None:
    """背景 manager 在 resident 模式會呼叫 resident runner 並記錄 cycle。"""

    calls: list[ResidentWorkerOptions] = []

    def fake_resident_runner(
        options: ResidentWorkerOptions,
        stop_event: Event,
        on_cycle: Callable[[ResidentCycleSummary], None],
        sleep_fn: Callable[[float], object] | None = None,
    ) -> object:
        """記錄 resident runner 呼叫並送出一筆 cycle summary。"""

        calls.append(options)
        on_cycle(
            ResidentCycleSummary(
                cycle_index=1,
                selected_count=1,
                success_count=1,
                failure_count=0,
                skipped_count=0,
                opened_page_count=1,
                reused_page_count=0,
                closed_page_count=0,
                queued_count=1,
                running_count=2,
                queue_length=1,
                queued_target_ids=("target-3",),
                worker_ids=("resident-slot-1", "resident-slot-2"),
                page_pool_size=3,
                resident_browser_alive=True,
            )
        )
        stop_event.set()
        return object()

    manager = BackgroundSchedulerManager(resident_runner=fake_resident_runner)
    manager.start(
        SchedulerSessionOptions(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
            auto_scan_mode=AutoScanMode.RESIDENT,
            interval_seconds=45,
        )
    )
    assert manager.thread is not None
    manager.thread.join(timeout=2)

    state = manager.state()
    assert len(calls) == 1
    assert calls[0].interval_seconds == 45
    assert state.auto_scan_mode == AutoScanMode.RESIDENT
    assert state.last_cycle_at
    assert not state.last_error
    assert state.current_running_count == 2
    assert state.current_queued_count == 1
    assert state.queue_length == 1
    assert state.queued_target_ids == ("target-3",)
    assert state.worker_ids == ("resident-slot-1", "resident-slot-2")
    assert state.page_pool_size == 3
    assert state.last_opened_page_count == 1
    assert state.last_reused_page_count == 0
    assert state.last_closed_page_count == 0
    assert state.resident_browser_alive
