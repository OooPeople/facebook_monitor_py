"""Web UI scheduler session tests。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event

from facebook_monitor.webapp.scheduler_session import BackgroundSchedulerManager
from facebook_monitor.webapp.scheduler_session import SchedulerLifecycleState
from facebook_monitor.webapp.scheduler_session import SchedulerSessionOptions
from facebook_monitor.worker.resident_shared import ResidentCycleSummary
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions


def test_background_scheduler_manager_runs_resident_mode(tmp_path: Path) -> None:
    """背景 manager 在 resident 模式會呼叫 resident runner 並記錄 cycle。"""

    calls: list[ResidentRuntimeOptions] = []

    def fake_resident_main_runner(
        options: ResidentRuntimeOptions,
        stop_event: Event,
        on_cycle: Callable[[ResidentCycleSummary], None],
        sleep_fn: Callable[[float], object] | None = None,
    ) -> object:
        """記錄 resident main runner 呼叫並送出一筆 cycle summary。"""

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

    manager = BackgroundSchedulerManager(resident_main_runner=fake_resident_main_runner)
    manager.start(
        SchedulerSessionOptions(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
            interval_seconds=45,
        )
    )
    assert manager.thread is not None
    manager.thread.join(timeout=2)

    state = manager.state()
    assert len(calls) == 1
    assert calls[0].interval_seconds == 45
    assert state.mode_label == "常駐"
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


def test_background_scheduler_manager_passes_metadata_refresh_requests(
    tmp_path: Path,
) -> None:
    """metadata refresh request 會交給 resident runtime options 並去重。"""

    calls: list[tuple[str, ...]] = []

    def fake_resident_main_runner(
        options: ResidentRuntimeOptions,
        stop_event: Event,
        on_cycle: Callable[[ResidentCycleSummary], None],
        sleep_fn: Callable[[float], object] | None = None,
    ) -> object:
        """取出 metadata refresh request 並結束 runner。"""

        assert options.metadata_refresh_provider is not None
        calls.append(options.metadata_refresh_provider())
        calls.append(options.metadata_refresh_provider())
        on_cycle(
            ResidentCycleSummary(
                cycle_index=1,
                selected_count=0,
                success_count=0,
                failure_count=0,
                skipped_count=0,
                opened_page_count=0,
                reused_page_count=0,
                closed_page_count=0,
            )
        )
        stop_event.set()
        return object()

    manager = BackgroundSchedulerManager(resident_main_runner=fake_resident_main_runner)
    manager.request_metadata_refresh("target-1")
    manager.request_metadata_refresh("target-1")
    manager.request_metadata_refresh("target-2")
    manager.start(
        SchedulerSessionOptions(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
        )
    )
    assert manager.thread is not None
    manager.thread.join(timeout=2)

    assert calls == [("target-1", "target-2"), ()]


def test_background_scheduler_stop_timeout_keeps_stopping_state(tmp_path: Path) -> None:
    """stop timeout 後 thread 未結束時不得顯示為 stopped。"""

    entered_event = Event()
    release_event = Event()

    def blocking_resident_main_runner(
        _options: ResidentRuntimeOptions,
        stop_event: Event,
        _on_cycle: Callable[[ResidentCycleSummary], None],
        _sleep_fn: Callable[[float], object] | None = None,
    ) -> object:
        """模擬 resident runner 正在等待 browser cleanup。"""

        entered_event.set()
        stop_event.wait(timeout=2)
        release_event.wait(timeout=2)
        return object()

    manager = BackgroundSchedulerManager(resident_main_runner=blocking_resident_main_runner)
    manager.start(
        SchedulerSessionOptions(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
        )
    )
    assert entered_event.wait(timeout=2)
    manager.stop(timeout_seconds=0.01)
    state = manager.state()

    assert state.running
    assert state.lifecycle_state == SchedulerLifecycleState.STOPPING

    release_event.set()
    manager.stop(timeout_seconds=2)
    assert manager.state().lifecycle_state == SchedulerLifecycleState.STOPPED


def test_background_scheduler_rejects_start_while_stopping(tmp_path: Path) -> None:
    """STOPPING 中再次 start 會被拒絕，避免 profile lease 競態。"""

    entered_event = Event()
    release_event = Event()

    def blocking_resident_main_runner(
        _options: ResidentRuntimeOptions,
        stop_event: Event,
        _on_cycle: Callable[[ResidentCycleSummary], None],
        _sleep_fn: Callable[[float], object] | None = None,
    ) -> object:
        """模擬 stop timeout 後仍未釋放的 runner。"""

        entered_event.set()
        stop_event.wait(timeout=2)
        release_event.wait(timeout=2)
        return object()

    options = SchedulerSessionOptions(
        db_path=tmp_path / "app.db",
        profile_dir=tmp_path / "profile",
    )
    manager = BackgroundSchedulerManager(resident_main_runner=blocking_resident_main_runner)
    manager.start(options)
    assert entered_event.wait(timeout=2)
    manager.stop(timeout_seconds=0.01)

    try:
        manager.start(options)
    except RuntimeError as exc:
        assert "stopping" in str(exc).lower()
    else:
        raise AssertionError("start should reject while scheduler is stopping")
    finally:
        release_event.set()
        manager.stop(timeout_seconds=2)
