"""Worker scan sort policy helpers。

職責：集中 worker 對 auto_adjust_sort 結果的保護性判斷，避免 posts/comments
pipeline 各自維護一份排序失敗語義。
"""

from __future__ import annotations

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.facebook.sort_controls import SortAdjustResult


def should_skip_scan_for_unconfirmed_sort(
    *,
    config: TargetConfig,
    sort_adjust_result: SortAdjustResult,
) -> bool:
    """判斷 auto_adjust_sort 開啟時是否因排序未確認而跳過本輪。"""

    return (
        config.auto_adjust_sort
        and sort_adjust_result.after_label != sort_adjust_result.preferred_label
    )
