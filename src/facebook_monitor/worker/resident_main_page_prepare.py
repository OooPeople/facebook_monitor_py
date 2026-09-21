"""Resident executor page preparation helpers."""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.facebook_temporary_block import FacebookActionKind
from facebook_monitor.core.models import TargetKind
from facebook_monitor.worker.page_timing import RESIDENT_PAGE_READY_WAIT_MS
from facebook_monitor.worker.resident_main_executor_types import AsyncResidentPageLike
from facebook_monitor.worker.resident_shared import ResidentTarget
from facebook_monitor.worker.resident_shared import should_reload_resident_page


@dataclass(frozen=True)
class ResidentPagePrepareOutcome:
    """保存 resident page prepare 實際採用的導航動作。"""

    action_kind: FacebookActionKind = FacebookActionKind.UNKNOWN


async def prepare_resident_main_page(
    *,
    page: AsyncResidentPageLike,
    target: ResidentTarget,
    timeout_ms: float,
) -> ResidentPagePrepareOutcome:
    """讓 async page 停在 posts/comments canonical route；同一 route 只 reload。"""

    current_url = str(getattr(page, "url", "") or "")
    if should_reload_resident_page(current_url, target.target.canonical_url):
        await page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
        action_kind = FacebookActionKind.RELOAD
    else:
        await page.goto(
            target.target.canonical_url, wait_until="domcontentloaded", timeout=timeout_ms
        )
        action_kind = (
            FacebookActionKind.DIRECT_DOCUMENT
            if target.target.target_kind == TargetKind.COMMENTS
            else FacebookActionKind.GROUP_FEED_DOCUMENT
        )
    await page.wait_for_timeout(RESIDENT_PAGE_READY_WAIT_MS)
    return ResidentPagePrepareOutcome(action_kind=action_kind)


__all__ = [
    "ResidentPagePrepareOutcome",
    "prepare_resident_main_page",
]
