"""Resident executor page preparation helpers."""

from __future__ import annotations

from facebook_monitor.worker.page_timing import RESIDENT_PAGE_READY_WAIT_MS
from facebook_monitor.worker.resident_main_executor_types import AsyncResidentPageLike
from facebook_monitor.worker.resident_shared import ResidentTarget
from facebook_monitor.worker.resident_shared import should_reload_resident_page


async def prepare_resident_main_page(
    *,
    page: AsyncResidentPageLike,
    target: ResidentTarget,
    timeout_ms: float,
) -> None:
    """讓 async page 停在 target route；同一 route 只 reload。"""

    current_url = str(getattr(page, "url", "") or "")
    if should_reload_resident_page(current_url, target.target.canonical_url):
        await page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
    else:
        await page.goto(
            target.target.canonical_url, wait_until="domcontentloaded", timeout=timeout_ms
        )
    await page.wait_for_timeout(RESIDENT_PAGE_READY_WAIT_MS)
