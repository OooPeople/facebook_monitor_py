"""Resident async page pool。

職責：保存 target 到 Playwright page 的 ownership metadata，讓 resident
executor 能重用 page 並提供 active page diagnostics。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from facebook_monitor.worker.facebook_page_lifecycle import FacebookPageCloseError
from facebook_monitor.worker.facebook_page_lifecycle import close_page_checked_async
from facebook_monitor.worker.facebook_page_lifecycle import open_context_pages
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


class PageBudgetExceededError(RuntimeError):
    """表示 hard page budget 內沒有可安全驅逐的 idle page。"""


class PagePoolPoisonedError(RuntimeError):
    """表示 page 關閉結果不確定，整個 browser runtime 必須重建。"""


class AsyncResidentPagePool:
    """維護 resident main worker 的 target page 與 ownership metadata。"""

    def __init__(
        self,
        context: AsyncPagePoolBrowserContextLike,
        *,
        max_open_pages: int | None = None,
        retain_idle_pages: bool = True,
    ) -> None:
        self.context = context
        self.pages: dict[str, PageOwnership] = {}
        self.lock = asyncio.Lock()
        self.max_open_pages = max(int(max_open_pages), 1) if max_open_pages is not None else None
        self.retain_idle_pages = bool(retain_idle_pages)
        self._poisoned_error: BaseException | None = None

    def _ensure_healthy(self) -> None:
        """poisoned pool 不得再建立或移交 Facebook page。"""

        if self._poisoned_error is not None:
            raise PagePoolPoisonedError(
                "resident Facebook page pool is poisoned; runtime restart is required"
            ) from self._poisoned_error

    def _open_page_count(self) -> int:
        """以 context 實際存活 pages 計算 hard budget，避免漏算未納管頁。"""

        context_pages = open_context_pages(self.context)
        if context_pages or hasattr(self.context, "pages"):
            return len(context_pages)
        return sum(not owned.page.is_closed() for owned in self.pages.values())

    async def _close_owned_page(self, ownership: PageOwnership) -> None:
        """確認 page 關閉後才移除 ownership；失敗時 poison 整個 pool。"""

        try:
            await close_page_checked_async(ownership.page)
        except FacebookPageCloseError as exc:
            self._poisoned_error = exc
            raise PagePoolPoisonedError(
                "resident Facebook page close failed; runtime restart is required"
            ) from exc
        current = self.pages.get(ownership.target_id)
        if current is ownership:
            self.pages.pop(ownership.target_id, None)

    async def reserve_page_id(self, target_id: str) -> str:
        """回傳既有 page id，沒有可用 page 時先產生本輪 attempt page id。"""

        async with self.lock:
            self._ensure_healthy()
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
            self._ensure_healthy()
            closed_ids = [
                owned_target_id
                for owned_target_id, owned in self.pages.items()
                if owned.page.is_closed()
            ]
            for closed_target_id in closed_ids:
                self.pages.pop(closed_target_id, None)
            ownership = self.pages.get(target_id)
            if ownership is not None and not ownership.page.is_closed():
                ownership.in_use_by_worker = worker_id
                return ownership.page, ownership.page_id, False
            if self.max_open_pages is not None and self._open_page_count() >= self.max_open_pages:
                idle_target_id = next(
                    (
                        owned_target_id
                        for owned_target_id, owned in self.pages.items()
                        if not owned.in_use_by_worker
                    ),
                    "",
                )
                if not idle_target_id:
                    raise PageBudgetExceededError("resident Facebook page budget is fully in use")
                idle_ownership = self.pages[idle_target_id]
                await self._close_owned_page(idle_ownership)

            if self.max_open_pages is not None and self._open_page_count() >= self.max_open_pages:
                raise PageBudgetExceededError(
                    "resident Facebook page budget includes an unmanaged context page"
                )

            page = await self.context.new_page()
            if self.max_open_pages is not None and self._open_page_count() > self.max_open_pages:
                try:
                    await close_page_checked_async(page)
                except FacebookPageCloseError as exc:
                    self._poisoned_error = exc
                    raise PagePoolPoisonedError(
                        "new Facebook page exceeded budget and could not be closed"
                    ) from exc
                raise PageBudgetExceededError("resident Facebook page budget was exceeded")
            ownership = PageOwnership(
                page=page,
                page_id=page_id or f"page-{uuid4()}",
                target_id=target_id,
                current_url=str(getattr(page, "url", "") or ""),
                in_use_by_worker=worker_id,
            )
            self.pages[target_id] = ownership
            return ownership.page, ownership.page_id, True

    async def release(self, target_id: str, *, current_url: str = "") -> None:
        """釋放 target page ownership，但保留 page 供下輪重用。"""

        async with self.lock:
            self._ensure_healthy()
            ownership = self.pages.get(target_id)
            if ownership is None:
                return
            if not self.retain_idle_pages:
                await self._close_owned_page(ownership)
            else:
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
            self._ensure_healthy()
            ownership = self.pages.get(target_id)
            if ownership is None or ownership.page_id != page_id:
                return False
            if not self.retain_idle_pages:
                await self._close_owned_page(ownership)
            else:
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
            ownership = self.pages.get(target_id)
            if ownership is not None:
                await self._close_owned_page(ownership)

    async def discard_if_page_id(self, target_id: str, page_id: str) -> bool:
        """只在 page id 相符時關閉並移除單一 target page。"""

        async with self.lock:
            ownership = self.pages.get(target_id)
            if ownership is None or ownership.page_id != page_id:
                return False
            await self._close_owned_page(ownership)
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
            ownerships = tuple(self.pages.values())
            first_error: PagePoolPoisonedError | None = None
            for ownership in ownerships:
                try:
                    await self._close_owned_page(ownership)
                except PagePoolPoisonedError as exc:
                    first_error = first_error or exc
            if first_error is not None:
                raise first_error

    async def size(self) -> int:
        """回傳 page pool 目前保存的 page 數量。"""

        async with self.lock:
            return len(self.pages)
