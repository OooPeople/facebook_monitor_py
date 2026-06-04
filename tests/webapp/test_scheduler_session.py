"""Web UI scheduler session tests。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sqlite3
from threading import Event
from threading import Thread

from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.webapp import scheduler_session as scheduler_session_module
from facebook_monitor.application.scheduler_preflight import SchedulerStartPreflightResult
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


def test_background_scheduler_manager_notifies_on_resident_crash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """resident main 整體崩潰時，manager 會通知目前 active targets。"""

    calls: list[dict[str, object]] = []

    def failing_resident_main_runner(
        _options: ResidentRuntimeOptions,
        _stop_event: Event,
        _on_cycle: Callable[[ResidentCycleSummary], None],
        _sleep_fn: Callable[[float], object] | None = None,
    ) -> object:
        """模擬 Playwright/browser context 整體壞掉。"""

        raise RuntimeError("Target page, context or browser has been closed")

    def fake_record_notifications(**kwargs: object) -> int:
        """記錄 manager 傳給 runtime failure helper 的參數。"""

        calls.append(kwargs)
        return 1

    def stop_after_failure(stop_event: Event, _seconds: float) -> bool:
        """讓測試在第一次 resident crash 後結束背景 thread。"""

        stop_event.set()
        return True

    monkeypatch.setattr(
        scheduler_session_module,
        "record_active_targets_runtime_failure_notifications_for_db",
        fake_record_notifications,
    )
    manager = BackgroundSchedulerManager(
        resident_main_runner=failing_resident_main_runner,
        wait_fn=stop_after_failure,
    )
    manager.start(
        SchedulerSessionOptions(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
        )
    )
    assert manager.thread is not None
    manager.thread.join(timeout=2)
    state = manager.state()

    assert state.lifecycle_state == SchedulerLifecycleState.ERROR
    assert "背景掃描執行錯誤" in state.last_error
    assert len(calls) == 1
    assert calls[0]["db_path"] == tmp_path / "app.db"
    assert calls[0]["reason"] == SCHEDULER_RUNTIME_REASON
    assert calls[0]["worker_path"] == "resident_scheduler"
    assert calls[0]["exception_class"] == "RuntimeError"


def test_background_scheduler_manager_normalizes_unknown_crash_reason(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """resident 整體 unknown crash 應走 scheduler_runtime 三次重試策略。"""

    calls: list[dict[str, object]] = []

    class UnknownResidentError(RuntimeError):
        """模擬帶 unknown reason 的 resident top-level 錯誤。"""

        reason = UNKNOWN_REASON

    def failing_resident_main_runner(
        _options: ResidentRuntimeOptions,
        _stop_event: Event,
        _on_cycle: Callable[[ResidentCycleSummary], None],
        _sleep_fn: Callable[[float], object] | None = None,
    ) -> object:
        raise UnknownResidentError("unknown top-level resident failure")

    def fake_record_notifications(**kwargs: object) -> int:
        calls.append(kwargs)
        return 1

    def stop_after_failure(stop_event: Event, _seconds: float) -> bool:
        stop_event.set()
        return True

    monkeypatch.setattr(
        scheduler_session_module,
        "record_active_targets_runtime_failure_notifications_for_db",
        fake_record_notifications,
    )
    manager = BackgroundSchedulerManager(
        resident_main_runner=failing_resident_main_runner,
        wait_fn=stop_after_failure,
    )
    manager.start(
        SchedulerSessionOptions(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
        )
    )
    assert manager.thread is not None
    manager.thread.join(timeout=2)

    assert len(calls) == 1
    assert calls[0]["reason"] == SCHEDULER_RUNTIME_REASON


def test_background_scheduler_manager_skips_target_notifications_for_sqlite_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """resident top-level DB lock 不應批次寫成所有 active targets 的 failure。"""

    calls: list[dict[str, object]] = []

    def failing_resident_main_runner(
        _options: ResidentRuntimeOptions,
        _stop_event: Event,
        _on_cycle: Callable[[ResidentCycleSummary], None],
        _sleep_fn: Callable[[float], object] | None = None,
    ) -> object:
        raise sqlite3.OperationalError("database is locked")

    def fake_record_notifications(**kwargs: object) -> int:
        calls.append(kwargs)
        return 1

    def stop_after_failure(stop_event: Event, _seconds: float) -> bool:
        stop_event.set()
        return True

    monkeypatch.setattr(
        scheduler_session_module,
        "record_active_targets_runtime_failure_notifications_for_db",
        fake_record_notifications,
    )
    manager = BackgroundSchedulerManager(
        resident_main_runner=failing_resident_main_runner,
        wait_fn=stop_after_failure,
    )
    manager.start(
        SchedulerSessionOptions(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
        )
    )
    assert manager.thread is not None
    manager.thread.join(timeout=2)
    state = manager.state()

    assert state.lifecycle_state == SchedulerLifecycleState.ERROR
    assert "背景掃描執行錯誤" in state.last_error
    assert calls == []


def test_background_scheduler_manager_blocks_start_on_preflight_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """preflight 擋下 start 時不啟動 resident runner，也不寫 runtime failure 通知。"""

    runner_called = False
    notification_calls: list[dict[str, object]] = []

    def resident_main_runner(
        _options: ResidentRuntimeOptions,
        _stop_event: Event,
        _on_cycle: Callable[[ResidentCycleSummary], None],
        _sleep_fn: Callable[[float], object] | None = None,
    ) -> object:
        nonlocal runner_called
        runner_called = True
        return object()

    def fake_record_notifications(**kwargs: object) -> int:
        notification_calls.append(kwargs)
        return 1

    monkeypatch.setattr(
        scheduler_session_module,
        "record_active_targets_runtime_failure_notifications_for_db",
        fake_record_notifications,
    )
    manager = BackgroundSchedulerManager(
        resident_main_runner=resident_main_runner,
        preflight_check=lambda _options: SchedulerStartPreflightResult.blocked(
            "背景掃描啟動前資料檢查失敗：schema missing"
        ),
    )

    manager.start(
        SchedulerSessionOptions(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
        )
    )
    state = manager.state()

    assert manager.thread is None
    assert not runner_called
    assert notification_calls == []
    assert state.lifecycle_state == SchedulerLifecycleState.ERROR
    assert not state.running
    assert "啟動前資料檢查失敗" in state.last_error


def test_background_scheduler_stop_cancels_start_during_preflight(
    tmp_path: Path,
) -> None:
    """preflight 執行期間收到 stop，不得在 preflight 結束後仍啟動 thread。"""

    preflight_entered = Event()
    release_preflight = Event()
    runner_called = False

    def resident_main_runner(
        _options: ResidentRuntimeOptions,
        _stop_event: Event,
        _on_cycle: Callable[[ResidentCycleSummary], None],
        _sleep_fn: Callable[[float], object] | None = None,
    ) -> object:
        nonlocal runner_called
        runner_called = True
        return object()

    def blocking_preflight(
        _options: SchedulerSessionOptions,
    ) -> SchedulerStartPreflightResult:
        preflight_entered.set()
        release_preflight.wait(timeout=2)
        return SchedulerStartPreflightResult.passed()

    manager = BackgroundSchedulerManager(
        resident_main_runner=resident_main_runner,
        preflight_check=blocking_preflight,
    )
    options = SchedulerSessionOptions(
        db_path=tmp_path / "app.db",
        profile_dir=tmp_path / "profile",
    )
    start_thread = Thread(target=lambda: manager.start(options))
    start_thread.start()
    assert preflight_entered.wait(timeout=2)

    manager.stop(timeout_seconds=0.01)
    release_preflight.set()
    start_thread.join(timeout=2)
    state = manager.state()

    assert not start_thread.is_alive()
    assert not runner_called
    assert manager.thread is None
    assert not state.running
    assert state.lifecycle_state == SchedulerLifecycleState.STOPPED


def test_background_scheduler_cycle_recovers_lifecycle_after_crash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """resident crash 後下一輪成功 cycle 會把 lifecycle 從 ERROR 恢復 RUNNING。"""

    calls: list[dict[str, object]] = []
    run_count = 0
    cycle_recorded = Event()
    release_runner = Event()

    def flaky_resident_main_runner(
        _options: ResidentRuntimeOptions,
        stop_event: Event,
        on_cycle: Callable[[ResidentCycleSummary], None],
        _sleep_fn: Callable[[float], object] | None = None,
    ) -> object:
        nonlocal run_count
        run_count += 1
        if run_count == 1:
            raise RuntimeError("Target page, context or browser has been closed")
        on_cycle(
            ResidentCycleSummary(
                cycle_index=2,
                selected_count=0,
                success_count=0,
                failure_count=0,
                skipped_count=0,
                opened_page_count=0,
                reused_page_count=0,
                closed_page_count=0,
                resident_browser_alive=True,
            )
        )
        cycle_recorded.set()
        release_runner.wait(timeout=2)
        return object()

    def fake_record_notifications(**kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    def continue_after_first_failure(_stop_event: Event, _seconds: float) -> bool:
        return False

    monkeypatch.setattr(
        scheduler_session_module,
        "record_active_targets_runtime_failure_notifications_for_db",
        fake_record_notifications,
    )
    manager = BackgroundSchedulerManager(
        resident_main_runner=flaky_resident_main_runner,
        wait_fn=continue_after_first_failure,
    )
    manager.start(
        SchedulerSessionOptions(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
        )
    )
    assert manager.thread is not None
    assert cycle_recorded.wait(timeout=2)
    state = manager.state()

    assert run_count == 2
    assert len(calls) == 1
    assert state.lifecycle_state == SchedulerLifecycleState.RUNNING
    assert not state.last_error
    assert state.resident_browser_alive is True

    release_runner.set()
    manager.stop(timeout_seconds=2)


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
