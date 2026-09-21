from __future__ import annotations

import asyncio

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.worker.facebook_page_lifecycle import FacebookPageCloseError
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_page_pool import PageOwnership
from facebook_monitor.worker.resident_shared import ResidentTarget

from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext
from tests.worker.resident_main_test_helpers import FakeAsyncPage


def _target(group_id: str) -> ResidentTarget:
    """建立 page reuse 測試用 posts target。"""

    target = TargetDescriptor.for_group_posts(
        group_id=group_id,
        canonical_url=f"https://www.facebook.com/groups/{group_id}",
    )
    return ResidentTarget(
        target=target,
        config=TargetConfig(target_id=target.id),
    )


def test_page_pool_reuses_each_target_page_for_a_b_a_sequence() -> None:
    """A→B→A 應各自保留 page，回到 A 時直接重用。"""

    async def scenario() -> None:
        context = FakeAsyncBrowserContext()
        pool = AsyncResidentPagePool(context)
        target_a = _target("111")
        target_b = _target("222")

        page_a, page_a_id, opened_a = await pool.acquire(target_a, "worker-1")
        assert opened_a
        assert await pool.release_if_page_id(target_a.target.id, page_a_id)

        page_b, page_b_id, opened_b = await pool.acquire(target_b, "worker-2")
        assert opened_b
        assert await pool.release_if_page_id(target_b.target.id, page_b_id)

        reused_a, reused_a_id, opened_again = await pool.acquire(target_a, "worker-3")
        assert reused_a is page_a
        assert reused_a_id == page_a_id
        assert not opened_again
        assert not page_a.is_closed()
        assert not page_b.is_closed()
        assert await pool.size() == 2
        await pool.close_all()

    asyncio.run(scenario())


def test_page_pool_allows_multiple_targets_to_be_in_use() -> None:
    """正式 concurrency 設定可同時取得多個 target page，不套 hard page=1。"""

    async def scenario() -> None:
        pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        page_a, _, _ = await pool.acquire(_target("111"), "worker-1")
        page_b, _, _ = await pool.acquire(_target("222"), "worker-2")

        assert page_a is not page_b
        assert await pool.size() == 2
        await pool.close_all()

    asyncio.run(scenario())


def test_release_keeps_page_open_for_next_cycle() -> None:
    """release 只交還 ownership，page 留在 context 供下一輪重用。"""

    async def scenario() -> None:
        pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        target = _target("111")
        page, page_id, _ = await pool.acquire(target, "worker-1")

        assert await pool.release_if_page_id(target.target.id, page_id)
        assert not page.is_closed()
        assert await pool.size() == 1
        await pool.close_all()

    asyncio.run(scenario())


def test_unmanaged_context_page_does_not_impose_a_pool_budget() -> None:
    """context 既有 page 不應把正式 target page 擋在虛構的 hard budget 外。"""

    async def scenario() -> None:
        context = FakeAsyncBrowserContext()
        existing_page = await context.new_page()
        pool = AsyncResidentPagePool(context)

        target_page, _, opened = await pool.acquire(_target("111"), "worker-1")

        assert opened
        assert target_page is not existing_page
        assert sum(not candidate.is_closed() for candidate in context.pages) == 2
        await pool.close_all()
        await existing_page.close()

    asyncio.run(scenario())


def test_page_close_failure_preserves_ownership_without_poisoning_other_targets() -> None:
    """單頁 close 失敗保留 ownership，但不建立跨 target runtime poison。"""

    class CloseFailsPage(FakeAsyncPage):
        """模擬 Playwright close 失敗且 page 仍存活。"""

        async def close(self) -> None:
            raise RuntimeError("close failed")

    async def scenario() -> None:
        context = FakeAsyncBrowserContext()
        failing_page = CloseFailsPage()
        context.pages.append(failing_page)
        pool = AsyncResidentPagePool(context)
        pool.pages["target"] = PageOwnership(
            page=failing_page,
            page_id="page-1",
            target_id="target",
            in_use_by_worker="worker-1",
        )

        try:
            await pool.discard_if_page_id("target", "page-1")
        except FacebookPageCloseError:
            pass
        else:
            raise AssertionError("unconfirmed close must remain visible to the caller")

        assert "target" in pool.pages
        other_page, _, opened = await pool.acquire(_target("222"), "worker-2")
        assert opened
        assert not other_page.is_closed()

    asyncio.run(scenario())
