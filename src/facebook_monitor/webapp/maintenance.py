"""Web UI housekeeping background triggers。

職責：讓 Web request path 只觸發低頻 housekeeping，不同步承擔 SQLite
cleanup latency。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from pathlib import Path


logger = logging.getLogger(__name__)


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

    async def _run(self, db_path: Path) -> None:
        """在 background thread 執行 blocking SQLite housekeeping。"""

        try:
            await asyncio.to_thread(self._maintenance, db_path)
        except Exception:
            logger.exception("bounded retention background maintenance failed")


__all__ = ["BoundedRetentionMaintenanceRunner"]
