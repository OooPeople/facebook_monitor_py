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
from threading import Thread
from typing import Protocol

from facebook_monitor.core.models import utc_now
from facebook_monitor.scheduler.loop import SchedulerOptions
from facebook_monitor.scheduler.loop import run_scheduler_loop
from facebook_monitor.worker.async_resident import run_async_resident_worker_loop_sync
from facebook_monitor.worker.resident import ResidentCycleSummary
from facebook_monitor.worker.resident import ResidentWorkerOptions


class AutoScanMode(StrEnum):
    """Web UI 自動掃描執行模式。"""

    ONE_SHOT = "one_shot"
    RESIDENT = "resident"


class SchedulerRunner(Protocol):
    """定義背景 scheduler 可注入的單輪執行函式。"""

    def __call__(self, options: SchedulerOptions) -> object:
        """執行 scheduler 掃描。"""


class ResidentRunner(Protocol):
    """定義背景 resident worker 可注入的執行函式。"""

    def __call__(
        self,
        options: ResidentWorkerOptions,
        stop_event: Event,
        on_cycle: Callable[[ResidentCycleSummary], None],
        sleep_fn: Callable[[float], object] | None = None,
    ) -> object:
        """執行 resident worker。"""


@dataclass(frozen=True)
class SchedulerSessionOptions:
    """保存 Web UI 背景 scheduler 啟動設定。"""

    db_path: Path
    profile_dir: Path
    auto_scan_mode: AutoScanMode = AutoScanMode.RESIDENT
    interval_seconds: float = 60
    scheduler_tick_seconds: float = 2
    max_concurrent_scans: int = 2
    scroll_rounds: int = 3
    scroll_wait_ms: int = 2500
    scan_timeout_seconds: float = 120
    stale_running_after_seconds: float = 180


@dataclass(frozen=True)
class SchedulerSessionState:
    """保存背景 scheduler 目前狀態，供 UI 顯示。"""

    running: bool
    interval_seconds: float
    auto_scan_mode: AutoScanMode = AutoScanMode.RESIDENT
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

    @property
    def mode_label(self) -> str:
        """回傳 UI 顯示的自動掃描模式名稱。"""

        if self.auto_scan_mode == AutoScanMode.RESIDENT:
            return "常駐"
        return "一次性"


class BackgroundSchedulerManager:
    """管理 Web UI process 內的背景自動掃描 thread。"""

    def __init__(
        self,
        *,
        runner: SchedulerRunner | None = None,
        resident_runner: ResidentRunner | None = None,
        wait_fn: Callable[[Event, float], bool] | None = None,
    ) -> None:
        self.runner = runner or _run_one_scheduler_cycle
        self.resident_runner = resident_runner or _run_resident_worker
        self.wait_fn = wait_fn or _wait_for_stop
        self.thread: Thread | None = None
        self.stop_event = Event()
        self.wake_event = Event()
        self.options: SchedulerSessionOptions | None = None
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

    def is_running(self) -> bool:
        """回傳背景 scheduler thread 是否仍在運作。"""

        return bool(self.thread and self.thread.is_alive())

    def state(self) -> SchedulerSessionState:
        """回傳 UI 可直接使用的背景 scheduler 狀態。"""

        return SchedulerSessionState(
            running=self.is_running(),
            interval_seconds=self.options.interval_seconds if self.options else 0,
            auto_scan_mode=self.options.auto_scan_mode if self.options else AutoScanMode.RESIDENT,
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
        )

    def start(self, options: SchedulerSessionOptions) -> None:
        """啟動背景自動掃描；模式或設定改變時會重啟背景 thread。"""

        if self.is_running():
            if self.options == options:
                return
            self.stop()

        self.options = options
        self.stop_event = Event()
        self.wake_event = Event()
        self.resident_browser_alive = False
        self.thread = Thread(
            target=self._run_loop,
            name="facebook-monitor-scheduler",
            daemon=True,
        )
        self.thread.start()

    def stop(self, timeout_seconds: float = 5) -> None:
        """停止背景自動掃描，不影響 target 設定與 seen/history。"""

        self.stop_event.set()
        self.wake_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=timeout_seconds)
        if not self.is_running():
            self.resident_browser_alive = False

    def wake(self) -> None:
        """喚醒背景 scheduler，供 manual-start 立即進入下一輪。"""

        self.wake_event.set()

    def _run_loop(self) -> None:
        """背景 thread 主迴圈，依自動掃描模式委派對應 worker。"""

        while not self.stop_event.is_set():
            options = self.options
            if options is None:
                return
            if options.auto_scan_mode == AutoScanMode.RESIDENT:
                self._run_resident_mode(options)
                return

            self._run_one_shot_mode(options)
            return

    def _run_one_shot_mode(self, options: SchedulerSessionOptions) -> None:
        """執行 one-shot scheduler 模式，每個 tick 開關一次 worker context。"""

        while not self.stop_event.is_set():
            try:
                self.runner(
                    SchedulerOptions(
                        db_path=options.db_path,
                        profile_dir=options.profile_dir,
                        interval_seconds=0,
                        scheduler_tick_seconds=0,
                        max_concurrent_scans=options.max_concurrent_scans,
                        scroll_rounds=options.scroll_rounds,
                        scroll_wait_ms=options.scroll_wait_ms,
                        scan_timeout_seconds=options.scan_timeout_seconds,
                        stale_running_after_seconds=options.stale_running_after_seconds,
                        max_cycles=1,
                    )
                )
                self.last_cycle_at = utc_now().isoformat(timespec="seconds")
                self.last_error = ""
            except Exception as exc:
                self.last_error = str(exc)

            if self._wait_for_next_cycle(max(options.scheduler_tick_seconds, 1)):
                return

    def _run_resident_mode(self, options: SchedulerSessionOptions) -> None:
        """執行 resident worker 模式，維持同一個 browser context。"""

        while not self.stop_event.is_set():
            try:
                self.resident_runner(
                    ResidentWorkerOptions(
                        db_path=options.db_path,
                        profile_dir=options.profile_dir,
                        interval_seconds=options.interval_seconds,
                        scheduler_tick_seconds=options.scheduler_tick_seconds,
                        max_concurrent_scans=options.max_concurrent_scans,
                        scroll_rounds=options.scroll_rounds,
                        scroll_wait_ms=options.scroll_wait_ms,
                        scan_timeout_seconds=options.scan_timeout_seconds,
                        stale_running_after_seconds=options.stale_running_after_seconds,
                    ),
                    self.stop_event,
                    self._record_resident_cycle,
                    self._wait_for_next_cycle,
                )
                return
            except Exception as exc:
                self.last_error = str(exc)
                self.resident_browser_alive = False
                if self.wait_fn(self.stop_event, max(options.scheduler_tick_seconds, 1)):
                    self.wake_event.clear()
                    return

    def _record_resident_cycle(self, summary: ResidentCycleSummary) -> None:
        """記錄 resident worker 已完成一輪掃描。"""

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


def _run_one_scheduler_cycle(options: SchedulerOptions) -> object:
    """執行一輪 scheduler，避免 manager 直接依賴 loop 細節。"""

    return run_scheduler_loop(options, sleep_fn=lambda _seconds: None)


def _run_resident_worker(
    options: ResidentWorkerOptions,
    stop_event: Event,
    on_cycle: Callable[[ResidentCycleSummary], None],
    sleep_fn: Callable[[float], object] | None = None,
) -> object:
    """執行 resident worker，避免 manager 直接依賴 resident loop 細節。"""

    return run_async_resident_worker_loop_sync(
        options,
        should_stop=stop_event.is_set,
        on_cycle=on_cycle,
        sleep_fn=sleep_fn or (lambda seconds: stop_event.wait(seconds)),
    )


def _wait_for_stop(stop_event: Event, seconds: float) -> bool:
    """等待下一輪掃描間隔；回傳是否收到停止訊號。"""

    return stop_event.wait(seconds)
