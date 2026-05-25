"""Web UI background scheduler manager。

職責：在 FastAPI process 內管理一個背景 scheduler thread，讓本機 UI
可以直接啟停自動掃描，而不需要使用者另外開 terminal 執行 scheduler script。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from time import monotonic
from threading import Event
from threading import RLock
from threading import Thread
from typing import Protocol

from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.user_messages import format_failure_message
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.worker.resident_main import run_resident_main_loop_sync
from facebook_monitor.worker.resident_shared import ResidentCycleSummary
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions


class SchedulerLifecycleState(StrEnum):
    """Web UI 背景 scheduler 的 thread lifecycle。"""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class ResidentMainRunner(Protocol):
    """定義背景 resident main worker 可注入的執行函式。"""

    def __call__(
        self,
        options: ResidentRuntimeOptions,
        stop_event: Event,
        on_cycle: Callable[[ResidentCycleSummary], None],
        sleep_fn: Callable[[float], object] | None = None,
        /,
    ) -> object:
        """執行 resident main worker。"""


@dataclass(frozen=True)
class SchedulerSessionOptions:
    """保存 Web UI 背景 scheduler 啟動設定。"""

    db_path: Path
    profile_dir: Path
    interval_seconds: float = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.resident_interval_seconds
    scheduler_tick_seconds: float = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.scheduler_tick_seconds
    max_concurrent_scans: int = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.max_concurrent_scans
    scroll_rounds: int = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.scroll_rounds
    scroll_wait_ms: int = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.scroll_wait_ms
    scan_timeout_seconds: float = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.scan_timeout_seconds
    stale_running_after_seconds: float = (
        PYTHON_SCHEDULER_RUNTIME_DEFAULTS.stale_running_after_seconds
    )


@dataclass(frozen=True)
class SchedulerSessionState:
    """保存背景 scheduler 目前狀態，供 UI 顯示。"""

    running: bool
    interval_seconds: float
    lifecycle_state: SchedulerLifecycleState = SchedulerLifecycleState.STOPPED
    last_cycle_at: str = ""
    last_error: str = ""
    max_concurrent_scans: int = 0
    current_running_count: int = 0
    current_queued_count: int = 0
    queue_length: int = 0
    queued_target_ids: tuple[str, ...] = ()
    worker_ids: tuple[str, ...] = ()
    page_pool_size: int = 0
    last_opened_page_count: int = 0
    last_reused_page_count: int = 0
    last_closed_page_count: int = 0
    resident_browser_alive: bool = False
    recovered_runtime_count: int = 0
    notification_dispatch_count: int = 0
    worker_health_ok: bool = True

    @property
    def mode_label(self) -> str:
        """回傳 UI 顯示的自動掃描模式名稱。"""

        return "常駐"


class SchedulerManagerLike(Protocol):
    """Web UI 需要的 scheduler manager 介面，供正式實作與測試替身共用。"""

    @property
    def options(self) -> SchedulerSessionOptions | None:
        """回傳目前 scheduler 啟動設定。"""

    def is_running(self) -> bool:
        """回傳 scheduler 是否執行中。"""

    def state(self) -> SchedulerSessionState:
        """回傳 UI 可呈現的 scheduler 狀態。"""

    def start(self, options: SchedulerSessionOptions) -> None:
        """依指定設定啟動 scheduler。"""

    def stop(self) -> None:
        """停止 scheduler。"""

    def wake(self) -> None:
        """喚醒 scheduler 進入下一輪工作。"""

    def request_metadata_refresh(self, target_id: str) -> None:
        """要求 scheduler 補齊指定 target metadata。"""


class BackgroundSchedulerManager:
    """管理 Web UI process 內的背景自動掃描 thread。"""

    def __init__(
        self,
        *,
        resident_main_runner: ResidentMainRunner | None = None,
        wait_fn: Callable[[Event, float], bool] | None = None,
    ) -> None:
        self.resident_main_runner = resident_main_runner or _run_resident_main
        self.wait_fn = wait_fn or _wait_for_stop
        self.thread: Thread | None = None
        self.stop_event = Event()
        self.wake_event = Event()
        self.options: SchedulerSessionOptions | None = None
        self.lifecycle_state = SchedulerLifecycleState.STOPPED
        self.last_cycle_at = ""
        self.last_error = ""
        self.current_running_count = 0
        self.current_queued_count = 0
        self.queue_length = 0
        self.queued_target_ids: tuple[str, ...] = ()
        self.worker_ids: tuple[str, ...] = ()
        self.page_pool_size = 0
        self.last_opened_page_count = 0
        self.last_reused_page_count = 0
        self.last_closed_page_count = 0
        self.resident_browser_alive = False
        self.recovered_runtime_count = 0
        self.notification_dispatch_count = 0
        self.worker_health_ok = True
        self.metadata_refresh_target_ids: set[str] = set()
        self.metadata_refresh_order: list[str] = []
        self._lock = RLock()

    def is_running(self) -> bool:
        """回傳背景 scheduler thread 是否仍在運作。"""

        with self._lock:
            return self._is_thread_alive_locked()

    def state(self) -> SchedulerSessionState:
        """回傳 UI 可直接使用的背景 scheduler 狀態。"""

        with self._lock:
            running = self._is_thread_alive_locked() or (
                self.lifecycle_state == SchedulerLifecycleState.STOPPING
            )
            return SchedulerSessionState(
                running=running,
                interval_seconds=self.options.interval_seconds if self.options else 0,
                lifecycle_state=self.lifecycle_state,
                last_cycle_at=self.last_cycle_at,
                last_error=self.last_error,
                max_concurrent_scans=self.options.max_concurrent_scans if self.options else 0,
                current_running_count=self.current_running_count,
                current_queued_count=self.current_queued_count,
                queue_length=self.queue_length,
                queued_target_ids=self.queued_target_ids,
                worker_ids=self.worker_ids,
                page_pool_size=self.page_pool_size,
                last_opened_page_count=self.last_opened_page_count,
                last_reused_page_count=self.last_reused_page_count,
                last_closed_page_count=self.last_closed_page_count,
                resident_browser_alive=self.resident_browser_alive,
                recovered_runtime_count=self.recovered_runtime_count,
                notification_dispatch_count=self.notification_dispatch_count,
                worker_health_ok=self.worker_health_ok,
            )

    def start(self, options: SchedulerSessionOptions) -> None:
        """啟動背景自動掃描；模式或設定改變時會重啟背景 thread。"""

        should_stop_existing = False
        with self._lock:
            if self.lifecycle_state == SchedulerLifecycleState.STOPPING:
                raise RuntimeError("Scheduler is stopping; wait for it to finish before starting.")
            if self._is_thread_alive_locked():
                if self.options == options:
                    return
                should_stop_existing = True
        if should_stop_existing:
            self.stop()

        with self._lock:
            if self.lifecycle_state == SchedulerLifecycleState.STOPPING:
                raise RuntimeError("Scheduler is stopping; wait for it to finish before starting.")
            self.options = options
            self.stop_event = Event()
            self.wake_event = Event()
            self.resident_browser_alive = False
            self.lifecycle_state = SchedulerLifecycleState.STARTING
            self.thread = Thread(
                target=self._run_loop,
                name="facebook-monitor-scheduler",
                daemon=True,
            )
            self.thread.start()

    def stop(self, timeout_seconds: float = 5) -> None:
        """停止背景自動掃描，不影響 target 設定與 seen/history。"""

        with self._lock:
            thread = self.thread
            self.lifecycle_state = SchedulerLifecycleState.STOPPING
            self.stop_event.set()
            self.wake_event.set()
        if thread and thread.is_alive():
            thread.join(timeout=timeout_seconds)
        with self._lock:
            if not self._is_thread_alive_locked():
                self.resident_browser_alive = False
                self.lifecycle_state = SchedulerLifecycleState.STOPPED

    def wake(self) -> None:
        """喚醒背景 scheduler，供 manual-start 立即進入下一輪。"""

        with self._lock:
            self.wake_event.set()

    def request_metadata_refresh(self, target_id: str) -> None:
        """要求 resident scheduler 用既有 browser context 補齊 target metadata。"""

        normalized_target_id = target_id.strip()
        if not normalized_target_id:
            return
        with self._lock:
            if normalized_target_id not in self.metadata_refresh_target_ids:
                self.metadata_refresh_target_ids.add(normalized_target_id)
                self.metadata_refresh_order.append(normalized_target_id)
            self.wake_event.set()

    def take_metadata_refresh_requests(self) -> tuple[str, ...]:
        """取出並清空等待中的 metadata refresh target ids。"""

        with self._lock:
            target_ids = tuple(self.metadata_refresh_order)
            self.metadata_refresh_target_ids.clear()
            self.metadata_refresh_order.clear()
            return target_ids

    def _run_loop(self) -> None:
        """背景 thread 主迴圈，依自動掃描模式委派對應 worker。"""

        with self._lock:
            if self.lifecycle_state == SchedulerLifecycleState.STARTING:
                self.lifecycle_state = SchedulerLifecycleState.RUNNING
        try:
            while not self.stop_event.is_set():
                options = self.options
                if options is None:
                    return
                self._run_resident_mode(options)
                return
        finally:
            with self._lock:
                if self.lifecycle_state != SchedulerLifecycleState.STOPPING:
                    self.lifecycle_state = (
                        SchedulerLifecycleState.ERROR
                        if self.last_error
                        else SchedulerLifecycleState.STOPPED
                    )
                if not self._is_thread_alive_locked() and self.stop_event.is_set():
                    self.lifecycle_state = SchedulerLifecycleState.STOPPED

    def _run_resident_mode(self, options: SchedulerSessionOptions) -> None:
        """執行 resident main worker 模式，維持同一個 browser context。"""

        while not self.stop_event.is_set():
            try:
                self.resident_main_runner(
                    ResidentRuntimeOptions(
                        db_path=options.db_path,
                        profile_dir=options.profile_dir,
                        interval_seconds=options.interval_seconds,
                        scheduler_tick_seconds=options.scheduler_tick_seconds,
                        max_concurrent_scans=options.max_concurrent_scans,
                        scroll_rounds=options.scroll_rounds,
                        scroll_wait_ms=options.scroll_wait_ms,
                        scan_timeout_seconds=options.scan_timeout_seconds,
                        stale_running_after_seconds=options.stale_running_after_seconds,
                        metadata_refresh_provider=self.take_metadata_refresh_requests,
                    ),
                    self.stop_event,
                    self._record_resident_cycle,
                    self._wait_for_next_cycle,
                )
                return
            except Exception as exc:
                reason = getattr(exc, "reason", "")
                with self._lock:
                    self.last_error = (
                        format_failure_message(str(reason), str(exc))
                        if reason
                        else format_failure_message_text(str(exc))
                    )
                    self.resident_browser_alive = False
                    self.lifecycle_state = SchedulerLifecycleState.ERROR
                if self.wait_fn(self.stop_event, max(options.scheduler_tick_seconds, 1)):
                    self.wake_event.clear()
                    return

    def _record_resident_cycle(self, summary: ResidentCycleSummary) -> None:
        """記錄 resident main worker 已完成一輪掃描。"""

        with self._lock:
            self.last_cycle_at = utc_now().isoformat(timespec="seconds")
            self.last_error = ""
            self.current_running_count = summary.running_count
            self.current_queued_count = summary.queued_count
            self.queue_length = summary.queue_length
            self.queued_target_ids = summary.queued_target_ids
            self.worker_ids = summary.worker_ids
            self.page_pool_size = summary.page_pool_size
            self.last_opened_page_count = summary.opened_page_count
            self.last_reused_page_count = summary.reused_page_count
            self.last_closed_page_count = summary.closed_page_count
            self.resident_browser_alive = summary.resident_browser_alive
            self.recovered_runtime_count = summary.recovered_runtime_count
            self.notification_dispatch_count = summary.notification_dispatch_count
            self.worker_health_ok = summary.worker_health_ok

    def _wait_for_next_cycle(self, seconds: float) -> bool:
        """等待下一輪；manual-start wake 可提前結束等待。"""

        deadline = monotonic() + max(seconds, 0)
        while not self.stop_event.is_set():
            if self.wake_event.is_set():
                self.wake_event.clear()
                return False
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            if self.stop_event.wait(min(remaining, 0.5)):
                return True
        return True

    def _is_thread_alive_locked(self) -> bool:
        """在已持有 lock 時檢查 thread 是否仍存活。"""

        return bool(self.thread and self.thread.is_alive())


def _run_resident_main(
    options: ResidentRuntimeOptions,
    stop_event: Event,
    on_cycle: Callable[[ResidentCycleSummary], None],
    sleep_fn: Callable[[float], object] | None = None,
) -> object:
    """執行 resident main worker，避免 manager 直接依賴 resident loop 細節。"""

    return run_resident_main_loop_sync(
        options,
        should_stop=stop_event.is_set,
        on_cycle=on_cycle,
        sleep_fn=sleep_fn or (lambda seconds: stop_event.wait(seconds)),
    )


def _wait_for_stop(stop_event: Event, seconds: float) -> bool:
    """等待下一輪掃描間隔；回傳是否收到停止訊號。"""

    return stop_event.wait(seconds)
