"""Shared scan configuration helpers。

職責：保留 posts/comments pipeline 共用的掃描設定計算；Facebook 頁面 guard
已集中於 `worker.facebook_page_guard`。
"""

from __future__ import annotations

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.facebook.collection_policy import get_effective_scroll_rounds


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


__all__ = ["resolve_effective_scan_scroll_rounds"]
