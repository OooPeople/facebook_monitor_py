"""Web UI housekeeping background triggers。

職責：讓 Web request path 只觸發低頻 housekeeping，不同步承擔 SQLite
cleanup latency。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from pathlib import Path

from fastapi import FastAPI
from starlette.types import ASGIApp
from starlette.types import Message
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send


logger = logging.getLogger(__name__)
BOUNDED_RETENTION_MAINTENANCE_READ_PATHS = frozenset({"/", "/settings"})


class BoundedRetentionMaintenanceRunner:
    """在 Web process 內序列化 bounded retention background task。"""

    def __init__(self, maintenance: Callable[[Path], int]) -> None:
        self._maintenance = maintenance
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        """回傳目前是否已有 Web-triggered cleanup 正在執行。"""

        return self._task is not None and not self._task.done()

    def trigger(self, db_path: Path) -> bool:
        """排入一次 background cleanup；已在執行時不重複排程。"""

        if self.running:
            return False
        self._task = asyncio.create_task(self._run(db_path))
        return True

    async def wait_until_idle(self) -> None:
        """等待目前已排入的 cleanup 完成，供 app shutdown 收尾。"""

        task = self._task
        if task is None:
            return
        await task

    async def _run(self, db_path: Path) -> None:
        """在 background thread 執行 blocking SQLite housekeeping。"""

        try:
            await asyncio.to_thread(self._maintenance, db_path)
        except Exception:
            logger.exception("bounded retention background maintenance failed")


class BoundedRetentionMaintenanceMiddleware:
    """成功讀取 Web UI 頁面後排入 bounded retention housekeeping。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """套用 pure ASGI middleware，避免長 SSE response 被 request wrapper 包住。"""

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "")).upper()
        path = str(scope.get("path", ""))
        should_observe = (
            method == "GET"
            and path in BOUNDED_RETENTION_MAINTENANCE_READ_PATHS
        )
        triggered = False

        async def send_with_maintenance(message: Message) -> None:
            nonlocal triggered
            if (
                should_observe
                and not triggered
                and message["type"] == "http.response.start"
                and int(message.get("status", 500)) < 500
            ):
                triggered = True
                scope["app"].state.bounded_retention_maintenance_runner.trigger(
                    scope["app"].state.db_path
                )
            await send(message)

        await self.app(scope, receive, send_with_maintenance)


def register_bounded_retention_maintenance_middleware(app: FastAPI) -> None:
    """註冊 Web read path housekeeping trigger middleware。"""

    app.add_middleware(BoundedRetentionMaintenanceMiddleware)


__all__ = [
    "BOUNDED_RETENTION_MAINTENANCE_READ_PATHS",
    "BoundedRetentionMaintenanceMiddleware",
    "BoundedRetentionMaintenanceRunner",
    "register_bounded_retention_maintenance_middleware",
]
