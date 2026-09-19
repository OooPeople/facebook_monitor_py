from __future__ import annotations

import asyncio

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.worker.facebook_page_lifecycle import (
    close_existing_context_pages_async,
)
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_page_pool import PageBudgetExceededError
from facebook_monitor.worker.resident_main_page_pool import PageOwnership
from facebook_monitor.worker.resident_main_page_pool import PagePoolPoisonedError
from facebook_monitor.worker.resident_shared import ResidentTarget

from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext
from tests.worker.resident_main_test_helpers import FakeAsyncPage


def _target(group_id: str) -> ResidentTarget:
    """建立 page-budget 測試用 posts target。"""

    target = TargetDescriptor.for_group_posts(
        group_id=group_id,
        canonical_url=f"https://www.facebook.com/groups/{group_id}",
    )
    return ResidentTarget(
        target=target,
        config=TargetConfig(target_id=target.id),
    )


def test_page_budget_evicts_idle_page_for_a_b_a_sequence() -> None:
    """hard budget=1 時 A→B→A 每次都只保留一頁並關閉被驅逐頁。"""

    async def scenario() -> None:
        context = FakeAsyncBrowserContext()
        pool = AsyncResidentPagePool(
            context,
            max_open_pages=1,
            retain_idle_pages=True,
        )
        target_a = _target("111")
        target_b = _target("222")

        page_a1, page_a1_id, _ = await pool.acquire(target_a, "worker-1")
        await pool.release_if_page_id(target_a.target.id, page_a1_id)
        page_b, page_b_id, _ = await pool.acquire(target_b, "worker-1")
        assert page_a1.is_closed()
        assert await pool.size() == 1

        await pool.release_if_page_id(target_b.target.id, page_b_id)
        page_a2, _page_a2_id, opened = await pool.acquire(target_a, "worker-1")
        assert page_b.is_closed()
        assert opened
        assert page_a2 is not page_a1
        assert await pool.size() == 1
        await pool.close_all()

    asyncio.run(scenario())


def test_page_budget_rejects_second_page_while_first_is_in_use() -> None:
    """第一頁仍在 lease 內使用時，不得暫時超出 hard budget。"""

    async def scenario() -> None:
        pool = AsyncResidentPagePool(
            FakeAsyncBrowserContext(),
            max_open_pages=1,
            retain_idle_pages=True,
        )
        await pool.acquire(_target("111"), "worker-1")
        try:
            await pool.acquire(_target("222"), "worker-2")
        except PageBudgetExceededError:
            pass
        else:
            raise AssertionError("second in-use Facebook page must be rejected")
        assert await pool.size() == 1
        await pool.close_all()

    asyncio.run(scenario())


def test_safe_release_closes_page_before_automation_lease_ends() -> None:
    """正式安全模式 release 即關頁，不留下 lease 外 idle Facebook page。"""

    async def scenario() -> None:
        pool = AsyncResidentPagePool(
            FakeAsyncBrowserContext(),
            max_open_pages=1,
            retain_idle_pages=False,
        )
        target = _target("111")
        page, page_id, _ = await pool.acquire(target, "worker-1")
        assert await pool.release_if_page_id(target.target.id, page_id)
        assert page.is_closed()
        assert await pool.size() == 0

    asyncio.run(scenario())


def test_existing_context_page_is_closed_before_pool_opens_formal_page() -> None:
    """persistent context 自帶 page 必須先關閉，context-level peak 才不會超過一頁。"""

    async def scenario() -> None:
        context = FakeAsyncBrowserContext()
        existing_page = await context.new_page()
        await close_existing_context_pages_async(context)
        assert existing_page.is_closed()

        pool = AsyncResidentPagePool(context, max_open_pages=1, retain_idle_pages=False)
        target = _target("111")
        page, page_id, opened = await pool.acquire(target, "worker-1")
        assert opened
        assert page is not existing_page
        assert sum(not candidate.is_closed() for candidate in context.pages) == 1
        assert await pool.release_if_page_id(target.target.id, page_id)

    asyncio.run(scenario())


def test_unmanaged_existing_context_page_consumes_hard_budget() -> None:
    """漏做 startup 清理時，pool 也不可忽略 context 既有 page 再開第二頁。"""

    async def scenario() -> None:
        context = FakeAsyncBrowserContext()
        await context.new_page()
        pool = AsyncResidentPagePool(context, max_open_pages=1, retain_idle_pages=False)

        try:
            await pool.acquire(_target("111"), "worker-1")
        except PageBudgetExceededError:
            pass
        else:
            raise AssertionError("unmanaged context page must consume the hard budget")
        assert len(context.pages) == 1

    asyncio.run(scenario())


def test_page_close_failure_poison_pool_and_preserves_ownership() -> None:
    """close 未確認時保留 ownership，並禁止同 context 再建立 Facebook page。"""

    class CloseFailsPage(FakeAsyncPage):
        """模擬 Playwright close 失敗且 page 仍存活。"""

        async def close(self) -> None:
            raise RuntimeError("close failed")

    async def scenario() -> None:
        context = FakeAsyncBrowserContext()
        failing_page = CloseFailsPage()
        context.pages.append(failing_page)
        pool = AsyncResidentPagePool(context, max_open_pages=1, retain_idle_pages=False)
        pool.pages["target"] = PageOwnership(
            page=failing_page,
            page_id="page-1",
            target_id="target",
            in_use_by_worker="worker-1",
        )

        try:
            await pool.release_if_page_id("target", "page-1")
        except PagePoolPoisonedError:
            pass
        else:
            raise AssertionError("unconfirmed close must poison the pool")
        assert "target" in pool.pages
        try:
            await pool.acquire(_target("222"), "worker-2")
        except PagePoolPoisonedError:
            pass
        else:
            raise AssertionError("poisoned pool must reject later work")

    asyncio.run(scenario())
