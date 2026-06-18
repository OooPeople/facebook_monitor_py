"""Dashboard revision 長連線通知器。

職責：在 Web UI process 內集中輪詢 SQLite dashboard revision，並用 bounded
last-value-only delivery 喚醒 dashboard SSE subscribers。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import AsyncGenerator
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from facebook_monitor.webapp.dashboard_read_models import DashboardRevision
from facebook_monitor.webapp.dashboard_read_models import DashboardRevisionUnavailable


logger = logging.getLogger(__name__)
DashboardRevisionLoader = Callable[[Path], DashboardRevision]


@dataclass(eq=False)
class _DashboardRevisionSubscriber:
    """保存單一 SSE client 的 last-value-only delivery 狀態。"""

    latest: DashboardRevision | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    closed: bool = False


class DashboardRevisionNotifier:
    """集中管理 dashboard revision watcher 與 SSE subscribers。"""

    def __init__(
        self,
        *,
        db_path: Path,
        get_dashboard_revision: DashboardRevisionLoader,
        poll_interval_seconds: float,
        stop_timeout_seconds: float | None = None,
    ) -> None:
        self._db_path = db_path
        self._get_dashboard_revision = get_dashboard_revision
        self._poll_interval_seconds = max(0.05, float(poll_interval_seconds))
        self._stop_timeout_seconds = (
            max(0.1, float(stop_timeout_seconds))
            if stop_timeout_seconds is not None
            else max(1.0, self._poll_interval_seconds + 1.0)
        )
        self._wake_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._read_lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        self._subscribers: set[_DashboardRevisionSubscriber] = set()
        self._latest_revision: DashboardRevision | None = None
        self._watcher_task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def running(self) -> bool:
        """回傳 watcher task 是否仍在執行。"""

        return self._watcher_task is not None and not self._watcher_task.done()

    @property
    def subscriber_count(self) -> int:
        """回傳目前仍註冊的 SSE subscribers 數量，供測試與診斷使用。"""

        return len(self._subscribers)

    async def start(self) -> None:
        """啟動單一 process-local watcher task。"""

        if self._closed or self.running:
            return
        self._watcher_task = asyncio.create_task(
            self._watch_revisions(),
            name="dashboard-revision-notifier",
        )

    async def stop(self) -> None:
        """停止 watcher 並喚醒所有 subscribers；可重入且有 bounded timeout。"""

        async with self._stop_lock:
            self._closed = True
            self._stop_event.set()
            self._wake_event.set()
            self._close_subscribers()
            task = self._watcher_task
            try:
                if task is not None and not task.done():
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(task),
                            timeout=self._stop_timeout_seconds,
                        )
                    except TimeoutError:
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
                    except asyncio.CancelledError:
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
                        raise
            finally:
                self._watcher_task = None
                self._subscribers.clear()

    def wake(self) -> None:
        """要求 watcher 立即重讀 revision；不直接產生 revision event。"""

        if self._closed:
            return
        self._wake_event.set()

    async def subscribe(self) -> AsyncGenerator[DashboardRevision, None]:
        """訂閱 dashboard revision；slow subscriber 只會看到最新 revision。"""

        if self._closed:
            return

        subscriber = _DashboardRevisionSubscriber()
        latest = self._latest_revision
        if latest is not None:
            subscriber.latest = latest
            subscriber.changed.set()
        self._subscribers.add(subscriber)
        try:
            if latest is None:
                await self._ensure_latest_initialized()
            last_sent_revision = ""
            while not self._closed and not subscriber.closed:
                await subscriber.changed.wait()
                subscriber.changed.clear()
                if self._closed or subscriber.closed:
                    break
                revision = subscriber.latest
                if revision is None or revision.revision == last_sent_revision:
                    continue
                last_sent_revision = revision.revision
                yield revision
        finally:
            subscriber.closed = True
            self._subscribers.discard(subscriber)

    async def _watch_revisions(self) -> None:
        """輪詢 revision truth source，並接受 wake event 加速下一輪檢查。"""

        while not self._stop_event.is_set():
            await self._read_and_publish_once()
            if self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                pass
            else:
                self._wake_event.clear()

    async def _ensure_latest_initialized(self) -> bool:
        """初始化 latest revision；併發 cold subscribers 只允許第一個讀 DB。"""

        async with self._read_lock:
            if self._closed or self._latest_revision is not None:
                return False
            revision = await self._load_revision()
            if revision is None:
                return False
            self._latest_revision = revision
            self._publish_latest(revision)
            return True

    async def _read_and_publish_once(self) -> bool:
        """讀取一次 SQLite revision；成功且變更時通知 subscribers。"""

        async with self._read_lock:
            if self._closed:
                return False
            revision = await self._load_revision()
            if revision is None:
                return False
            current = self._latest_revision
            if current is not None and current.revision == revision.revision:
                return False
            self._latest_revision = revision
            self._publish_latest(revision)
            return True

    async def _load_revision(self) -> DashboardRevision | None:
        """在背景 thread 讀取 revision，並吞掉暫時性 SQLite read failure。"""

        try:
            return await asyncio.to_thread(
                self._get_dashboard_revision,
                self._db_path,
            )
        except (DashboardRevisionUnavailable, sqlite3.Error):
            logger.debug("dashboard revision read is temporarily unavailable")
            return None
        except Exception:
            logger.exception("dashboard revision watcher read failed")
            return None

    def _publish_latest(self, revision: DashboardRevision) -> None:
        """以 last-value-only 方式喚醒所有 subscribers。"""

        for subscriber in tuple(self._subscribers):
            if subscriber.closed:
                self._subscribers.discard(subscriber)
                continue
            subscriber.latest = revision
            subscriber.changed.set()

    def _close_subscribers(self) -> None:
        """喚醒並標記所有 subscribers 結束。"""

        for subscriber in tuple(self._subscribers):
            subscriber.closed = True
            subscriber.changed.set()


__all__ = ["DashboardRevisionLoader", "DashboardRevisionNotifier"]
