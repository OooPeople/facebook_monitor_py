"""Resident async page pool。

職責：保存 target 到 Playwright page 的 ownership metadata，讓 resident
executor 能重用 page 並提供 active page diagnostics。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from facebook_monitor.worker.resident_main_executor_types import (
    AsyncPagePoolBrowserContextLike,
)
from facebook_monitor.worker.resident_main_executor_types import AsyncReusablePageLike
from facebook_monitor.worker.resident_shared import ResidentTarget


@dataclass
class PageOwnership:
    """保存 resident page 與 worker ownership 診斷資訊。"""

    page: AsyncReusablePageLike
    page_id: str
    target_id: str
    in_use_by_worker: str = ""
    current_url: str = ""
    last_reloaded_at: datetime | None = None


class AsyncResidentPagePool:
    """維護 resident main worker 的 target page 與 ownership metadata。"""

    def __init__(self, context: AsyncPagePoolBrowserContextLike) -> None:
        self.context = context
        self.pages: dict[str, PageOwnership] = {}
        self.lock = asyncio.Lock()

    async def reserve_page_id(self, target_id: str) -> str:
        """回傳既有 page id，沒有可用 page 時先產生本輪 attempt page id。"""

        async with self.lock:
            ownership = self.pages.get(target_id)
            if ownership is not None and not ownership.page.is_closed():
                return ownership.page_id
        return f"page-{uuid4()}"

    async def acquire(
        self,
        target: ResidentTarget,
        worker_id: str,
        *,
        page_id: str = "",
    ) -> tuple[AsyncReusablePageLike, str, bool]:
        """取得 target 對應 page 並記錄目前 worker ownership。"""

        target_id = target.target.id
        async with self.lock:
            ownership = self.pages.get(target_id)
            if ownership is not None and not ownership.page.is_closed():
                ownership.in_use_by_worker = worker_id
                return ownership.page, ownership.page_id, False

        page = await self.context.new_page()
        close_unused_page = False
        async with self.lock:
            ownership = self.pages.get(target_id)
            if ownership is None or ownership.page.is_closed():
                ownership = PageOwnership(
                    page=page,
                    page_id=page_id or f"page-{uuid4()}",
                    target_id=target_id,
                    current_url=str(getattr(page, "url", "") or ""),
                )
                self.pages[target_id] = ownership
                ownership.in_use_by_worker = worker_id
                return ownership.page, ownership.page_id, True
            close_unused_page = True
            ownership.in_use_by_worker = worker_id
            owned_page = ownership.page
            owned_page_id = ownership.page_id
        if close_unused_page:
            await close_page_quietly(page)
        return owned_page, owned_page_id, False

    async def release(self, target_id: str, *, current_url: str = "") -> None:
        """釋放 target page ownership，但保留 page 供下輪重用。"""

        async with self.lock:
            ownership = self.pages.get(target_id)
            if ownership is None:
                return
            ownership.in_use_by_worker = ""
            ownership.current_url = current_url or str(getattr(ownership.page, "url", "") or "")

    async def release_if_page_id(
        self,
        target_id: str,
        page_id: str,
        *,
        current_url: str = "",
    ) -> bool:
        """只在 page id 仍相同時釋放 ownership，避免舊 attempt 影響新 page。"""

        async with self.lock:
            ownership = self.pages.get(target_id)
            if ownership is None or ownership.page_id != page_id:
                return False
            ownership.in_use_by_worker = ""
            ownership.current_url = current_url or str(getattr(ownership.page, "url", "") or "")
            return True

    async def mark_reloaded(self, target_id: str, *, current_url: str = "") -> datetime | None:
        """記錄 target page 已完成 reload/goto，供 ownership diagnostics 使用。"""

        return await self.mark_reloaded_if_page_id(
            target_id,
            "",
            current_url=current_url,
        )

    async def mark_reloaded_if_page_id(
        self,
        target_id: str,
        page_id: str,
        *,
        current_url: str = "",
    ) -> datetime | None:
        """只在 page id 相符時記錄 reload/goto，避免舊 attempt 覆寫新 page。"""

        reloaded_at = datetime.now().astimezone()
        async with self.lock:
            ownership = self.pages.get(target_id)
            if ownership is None or (page_id and ownership.page_id != page_id):
                return None
            ownership.current_url = current_url or str(getattr(ownership.page, "url", "") or "")
            ownership.last_reloaded_at = reloaded_at
        return reloaded_at

    async def discard(self, target_id: str) -> None:
        """關閉並移除單一 target page。"""

        async with self.lock:
            ownership = self.pages.pop(target_id, None)
        await close_page_quietly(ownership.page if ownership else None)

    async def discard_if_page_id(self, target_id: str, page_id: str) -> bool:
        """只在 page id 相符時關閉並移除單一 target page。"""

        async with self.lock:
            ownership = self.pages.get(target_id)
            if ownership is None or ownership.page_id != page_id:
                return False
            self.pages.pop(target_id, None)
        await close_page_quietly(ownership.page)
        return True

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


async def close_page_quietly(page: AsyncReusablePageLike | None) -> None:
    """安靜關閉 async Playwright page。"""

    if page is None or page.is_closed():
        return
    try:
        await page.close()
    except Exception:
        return
