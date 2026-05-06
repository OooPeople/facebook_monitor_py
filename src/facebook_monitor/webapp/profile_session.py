"""Web UI automation profile session manager。

職責：管理全域 Facebook automation profile 的 headed browser 視窗，
供使用者登入、登出或檢查帳號狀態。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from threading import Event
from threading import Lock
from threading import Thread
from typing import Any

from playwright.sync_api import sync_playwright

from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.facebook.browser_capture import get_start_page


DEFAULT_START_URL = "https://www.facebook.com/groups/"


class ProfileSessionError(RuntimeError):
    """表示 automation profile 視窗無法完成指定操作。"""


@dataclass(frozen=True)
class ProfileSessionOptions:
    """保存開啟 Facebook automation profile 視窗所需設定。"""

    profile_dir: Path
    start_url: str = DEFAULT_START_URL


@dataclass
class _ActiveProfileSession:
    """保存目前開啟中的 profile browser thread。"""

    thread: Thread
    stop_event: Event


class ProfileSessionManager:
    """管理單一 automation profile browser session。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._active_session: _ActiveProfileSession | None = None

    def is_active(self) -> bool:
        """回傳目前是否有登入 / 設定視窗開啟中。"""

        with self._lock:
            active_session = self._active_session
            if active_session is None:
                return False
            if not active_session.thread.is_alive():
                self._active_session = None
                return False
            return True

    def open(self, options: ProfileSessionOptions) -> None:
        """開啟 headed browser，供使用者操作 Facebook 登入狀態。"""

        options.profile_dir.mkdir(parents=True, exist_ok=True)
        ready_queue: Queue[object] = Queue(maxsize=1)
        stop_event = Event()

        with self._lock:
            if self._active_session is not None and self._active_session.thread.is_alive():
                raise ProfileSessionError("Facebook 設定視窗已開啟")
            thread = Thread(
                target=self._run_worker,
                args=(options, ready_queue, stop_event),
                daemon=True,
            )
            self._active_session = _ActiveProfileSession(
                thread=thread,
                stop_event=stop_event,
            )
            thread.start()

        ready_result = ready_queue.get()
        if isinstance(ready_result, BaseException):
            self._clear_active_session()
            raise ProfileSessionError(str(ready_result)) from ready_result

    def close(self) -> None:
        """關閉目前 Facebook 設定視窗。"""

        with self._lock:
            active_session = self._active_session
        if active_session is None:
            return
        active_session.stop_event.set()
        active_session.thread.join(timeout=5)
        self._clear_active_session()

    def _clear_active_session(self) -> None:
        """清除目前 active session 記錄。"""

        with self._lock:
            self._active_session = None

    def _run_worker(
        self,
        options: ProfileSessionOptions,
        ready_queue: Queue[object],
        stop_event: Event,
    ) -> None:
        """在專用 thread 中持有同步 Playwright context。"""

        context: Any | None = None
        try:
            with acquire_profile_lease(options.profile_dir, "Facebook 設定視窗"):
                with sync_playwright() as playwright:
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(options.profile_dir),
                        headless=False,
                        viewport={"width": 1366, "height": 900},
                    )
                    page = get_start_page(context)
                    page.goto(options.start_url, wait_until="domcontentloaded")
                    ready_queue.put(None)

                    while not stop_event.wait(1):
                        open_pages = [
                            browser_page
                            for browser_page in context.pages
                            if not getattr(browser_page, "is_closed", lambda: False)()
                        ]
                        if not open_pages:
                            break
        except Exception as exc:
            ready_queue.put(exc)
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            self._clear_active_session()
