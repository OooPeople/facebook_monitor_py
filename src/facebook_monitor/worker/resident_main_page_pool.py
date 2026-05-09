"""Resident async page pool。

職責：保存 target 到 Playwright page 的 ownership metadata，讓 resident
executor 能重用 page 並提供 active page diagnostics。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from facebook_monitor.worker.resident_shared import ResidentTarget


@dataclass
class PageOwnership:
    """保存 resident page 與 worker ownership 診斷資訊。"""

    page: Any
    page_id: str
    target_id: str
    in_use_by_worker: str = ""
    current_url: str = ""
    last_reloaded_at: datetime | None = None


class AsyncResidentPagePool:
    """維護 resident main worker 的 target page 與 ownership metadata。"""

    def __init__(self, context: Any) -> None:
        self.context = context
        self.pages: dict[str, PageOwnership] = {}
        self.lock = asyncio.Lock()

    async def acquire(self, target: ResidentTarget, worker_id: str) -> tuple[Any, str, bool]:
        """取得 target 對應 page 並記錄目前 worker ownership。"""

        async with self.lock:
            ownership = self.pages.get(target.target.id)
            opened = False
            if ownership is None or ownership.page.is_closed():
                page = await self.context.new_page()
                ownership = PageOwnership(
                    page=page,
                    page_id=f"page-{uuid4()}",
                    target_id=target.target.id,
                    current_url=str(getattr(page, "url", "") or ""),
                )
                self.pages[target.target.id] = ownership
                opened = True
            ownership.in_use_by_worker = worker_id
            return ownership.page, ownership.page_id, opened

    async def release(self, target_id: str, *, current_url: str = "") -> None:
        """釋放 target page ownership，但保留 page 供下輪重用。"""

        async with self.lock:
            ownership = self.pages.get(target_id)
            if ownership is None:
                return
            ownership.in_use_by_worker = ""
            ownership.current_url = current_url or str(getattr(ownership.page, "url", "") or "")

    async def mark_reloaded(self, target_id: str, *, current_url: str = "") -> datetime | None:
        """記錄 target page 已完成 reload/goto，供 ownership diagnostics 使用。"""

        reloaded_at = datetime.now().astimezone()
        async with self.lock:
            ownership = self.pages.get(target_id)
            if ownership is None:
                return None
            ownership.current_url = current_url or str(getattr(ownership.page, "url", "") or "")
            ownership.last_reloaded_at = reloaded_at
        return reloaded_at

    async def discard(self, target_id: str) -> None:
        """關閉並移除單一 target page。"""

        async with self.lock:
            ownership = self.pages.pop(target_id, None)
        await close_page_quietly(ownership.page if ownership else None)

    async def close_inactive(self, active_target_ids: set[str]) -> int:
        """關閉不再 active 且未被 worker 使用的 target pages。"""

        async with self.lock:
            inactive_ids = [
                target_id
                for target_id, ownership in self.pages.items()
                if target_id not in active_target_ids and not ownership.in_use_by_worker
            ]
        for target_id in inactive_ids:
            await self.discard(target_id)
        return len(inactive_ids)

    async def close_all(self) -> None:
        """關閉所有已建立 page。"""

        async with self.lock:
            target_ids = tuple(self.pages)
        for target_id in target_ids:
            await self.discard(target_id)

    async def size(self) -> int:
        """回傳 page pool 目前保存的 page 數量。"""

        async with self.lock:
            return len(self.pages)


async def close_page_quietly(page: Any | None) -> None:
    """安靜關閉 async Playwright page。"""

    if page is None or page.is_closed():
        return
    try:
        await page.close()
    except Exception:
        return
