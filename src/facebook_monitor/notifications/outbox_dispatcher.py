"""Notification outbox background dispatcher。

職責：在 process 內以單一背景 thread drain pending outbox rows，讓 scan
commit after-commit hook 只負責喚醒，不直接等待外部通知 I/O。
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
from threading import Event
from threading import RLock
from threading import Thread

from facebook_monitor.core.defaults import PYTHON_NOTIFICATION_RUNTIME_DEFAULTS
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.notifications.senders import DesktopSender
from facebook_monitor.notifications.senders import DiscordSender
from facebook_monitor.notifications.senders import NtfySender


logger = logging.getLogger(__name__)
DEFAULT_DISPATCHER_STOP_TIMEOUT_SECONDS = 5.0
NotificationOutboxDispatchCallable = Callable[..., int]
_REGISTRY_LOCK = RLock()
_DISPATCHERS_BY_DB_PATH: dict[Path, "NotificationOutboxDispatcher"] = {}


class NotificationOutboxDispatcher:
    """以 bounded wake signal 管理 notification outbox dispatch 背景 thread。"""

    def __init__(
        self,
        *,
        db_path: Path,
        ntfy_sender: NtfySender = send_ntfy_notification,
        desktop_sender: DesktopSender = send_desktop_notification,
        discord_sender: DiscordSender = send_discord_notification,
        stale_processing_seconds: float = (
            PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.stale_processing_seconds
        ),
        batch_limit: int = PYTHON_NOTIFICATION_RUNTIME_DEFAULTS.dispatch_batch_limit,
        stop_timeout_seconds: float = DEFAULT_DISPATCHER_STOP_TIMEOUT_SECONDS,
        dispatch_pending: NotificationOutboxDispatchCallable | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.ntfy_sender = ntfy_sender
        self.desktop_sender = desktop_sender
        self.discord_sender = discord_sender
        self.stale_processing_seconds = stale_processing_seconds
        self.batch_limit = batch_limit
        self.stop_timeout_seconds = max(float(stop_timeout_seconds), 0.0)
        self.dispatch_pending = dispatch_pending or _dispatch_pending_outbox_for_db
        self._wake_event = Event()
        self._stop_event = Event()
        self._lock = RLock()
        self._thread: Thread | None = None
        self.dispatch_count = 0
        self.last_dispatch_count = 0
        self.last_error = ""

    def start(self, *, wake_on_start: bool = True) -> None:
        """啟動背景 dispatcher；可選擇啟動後立即 drain backlog。"""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if wake_on_start:
                    self._wake_event.set()
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._thread = Thread(
                target=self._run,
                name=f"notification-outbox-dispatcher-{self.db_path.name}",
                daemon=True,
            )
            self._thread.start()
            if wake_on_start:
                self._wake_event.set()

    def wake(self) -> bool:
        """喚醒背景 dispatcher；未啟動時回傳 False。"""

        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                return False
            self._wake_event.set()
            return True

    def stop(self, *, timeout_seconds: float | None = None) -> bool:
        """要求背景 dispatcher 停止，並以 timeout 限制 shutdown 等待時間。"""

        with self._lock:
            thread = self._thread
            if thread is None:
                return True
            self._stop_event.set()
            self._wake_event.set()
        thread.join(
            self.stop_timeout_seconds if timeout_seconds is None else max(timeout_seconds, 0.0)
        )
        stopped = not thread.is_alive()
        if stopped:
            with self._lock:
                if self._thread is thread:
                    self._thread = None
        return stopped

    def _run(self) -> None:
        """背景 thread 主迴圈；每次 wake drain 一批目前 pending outbox。"""

        while not self._stop_event.is_set():
            self._wake_event.wait()
            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            try:
                dispatched_count = int(
                    self.dispatch_pending(
                        db_path=self.db_path,
                        ntfy_sender=self.ntfy_sender,
                        desktop_sender=self.desktop_sender,
                        discord_sender=self.discord_sender,
                        stale_processing_seconds=self.stale_processing_seconds,
                        batch_limit=self.batch_limit,
                    )
                )
                self.dispatch_count += 1
                self.last_dispatch_count = dispatched_count
                self.last_error = ""
            except Exception as exc:
                self.dispatch_count += 1
                self.last_dispatch_count = 0
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("notification_outbox_dispatcher_failed")


def register_notification_outbox_dispatcher(
    db_path: Path,
    dispatcher: NotificationOutboxDispatcher,
) -> None:
    """把 DB path 對應到 process-local dispatcher，供 after-commit hook 喚醒。"""

    with _REGISTRY_LOCK:
        _DISPATCHERS_BY_DB_PATH[_normalize_db_path(db_path)] = dispatcher


def unregister_notification_outbox_dispatcher(
    db_path: Path,
    dispatcher: NotificationOutboxDispatcher | None = None,
) -> None:
    """移除 process-local dispatcher registry；可選擇要求同一 instance 才移除。"""

    normalized = _normalize_db_path(db_path)
    with _REGISTRY_LOCK:
        registered = _DISPATCHERS_BY_DB_PATH.get(normalized)
        if registered is None:
            return
        if dispatcher is not None and registered is not dispatcher:
            return
        _DISPATCHERS_BY_DB_PATH.pop(normalized, None)


def wake_notification_outbox_dispatcher_for_db(db_path: Path) -> bool:
    """喚醒指定 DB 的背景 dispatcher；未註冊時回傳 False，pending rows 留在 DB。"""

    with _REGISTRY_LOCK:
        dispatcher = _DISPATCHERS_BY_DB_PATH.get(_normalize_db_path(db_path))
    if dispatcher is None:
        return False
    return dispatcher.wake()


def _normalize_db_path(db_path: Path) -> Path:
    """正規化 registry key，避免相對路徑與絕對路徑註冊成不同 dispatcher。"""

    return Path(db_path).expanduser().resolve()


def _dispatch_pending_outbox_for_db(**kwargs: object) -> int:
    """延遲匯入 outbox service，避免 dispatcher 與 service 形成 import cycle。"""

    from facebook_monitor.notifications.outbox_dispatch_service import (
        dispatch_new_pending_notification_outbox_for_db,
    )

    return dispatch_new_pending_notification_outbox_for_db(**kwargs)  # type: ignore[arg-type]


__all__ = [
    "NotificationOutboxDispatcher",
    "register_notification_outbox_dispatcher",
    "unregister_notification_outbox_dispatcher",
    "wake_notification_outbox_dispatcher_for_db",
]
