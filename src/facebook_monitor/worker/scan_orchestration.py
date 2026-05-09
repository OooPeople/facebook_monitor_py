"""Shared scan orchestration helpers。

職責：集中 posts/comments pipeline 共用的頁面 guard 與 scan policy 計算。
正式產品主路徑仍是 async resident；sync path 只作 fallback/debug。
"""

from __future__ import annotations

from typing import Any

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.facebook.collection_policy import get_effective_scroll_rounds
from facebook_monitor.worker.errors import WorkerFailure


def ensure_facebook_login_present(body_text: str) -> None:
    """檢查頁面是否要求登入；需要登入時拋出 worker failure。"""

    normalized = body_text.lower()
    if "log into facebook" in normalized or "登入 facebook" in normalized:
        raise WorkerFailure("login_required", "Facebook login is required.")


def ensure_sync_page_logged_in(page: Any) -> None:
    """sync Playwright page 登入 guard。"""

    ensure_facebook_login_present(page.locator("body").inner_text(timeout=10000))


async def ensure_async_page_logged_in(page: Any) -> None:
    """async Playwright page 登入 guard。"""

    body_text = await page.locator("body").inner_text(timeout=10000)
    ensure_facebook_login_present(body_text)


def resolve_effective_scan_scroll_rounds(
    *,
    config: TargetConfig,
    requested_scroll_rounds: int,
) -> int:
    """依 target config 與外部 request 計算實際 scroll rounds。"""

    return get_effective_scroll_rounds(
        target_count=config.max_items_per_scan,
        requested_scroll_rounds=requested_scroll_rounds,
        auto_load_more=config.auto_load_more,
    )
