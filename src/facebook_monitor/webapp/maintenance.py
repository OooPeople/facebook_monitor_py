"""Web UI housekeeping background triggers。

職責：讓 Web request path 只觸發低頻 housekeeping，不同步承擔 SQLite
cleanup latency。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi import Request
from starlette.responses import Response


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


def register_bounded_retention_maintenance_middleware(app: FastAPI) -> None:
    """註冊 Web read path housekeeping trigger middleware。"""

    @app.middleware("http")
    async def bounded_retention_maintenance_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """成功讀取頁面後嘗試 housekeeping，避免搶在空 DB schema 初始化前執行。"""

        response = await call_next(request)
        if (
            request.method == "GET"
            and request.url.path in BOUNDED_RETENTION_MAINTENANCE_READ_PATHS
            and response.status_code < 500
        ):
            request.app.state.bounded_retention_maintenance_runner.trigger(
                request.app.state.db_path
            )
        return response


__all__ = [
    "BOUNDED_RETENTION_MAINTENANCE_READ_PATHS",
    "BoundedRetentionMaintenanceRunner",
    "register_bounded_retention_maintenance_middleware",
]
