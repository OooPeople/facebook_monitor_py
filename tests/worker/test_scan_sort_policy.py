"""Worker sort policy tests。"""

from __future__ import annotations

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.facebook.sort_controls import SortAdjustResult
from facebook_monitor.worker.scan_sort_policy import should_skip_scan_for_unconfirmed_sort


def test_should_skip_scan_for_unconfirmed_sort_requires_enabled_auto_adjust() -> None:
    """關閉 auto_adjust_sort 時即使排序未確認也不保護性跳過。"""

    result = SortAdjustResult(
        attempted=True,
        changed=False,
        preferred_label="由新到舊",
        after_label="最相關",
        reason="sort_update_unconfirmed",
    )

    assert not should_skip_scan_for_unconfirmed_sort(
        config=TargetConfig(target_id="target", auto_adjust_sort=False),
        sort_adjust_result=result,
    )


def test_should_skip_scan_for_unconfirmed_sort_allows_already_preferred() -> None:
    """已是 preferred sort 時 changed=False 仍應允許掃描。"""

    result = SortAdjustResult(
        attempted=True,
        changed=False,
        preferred_label="由新到舊",
        after_label="由新到舊",
        reason="already_preferred_sort",
    )

    assert not should_skip_scan_for_unconfirmed_sort(
        config=TargetConfig(target_id="target", auto_adjust_sort=True),
        sort_adjust_result=result,
    )


def test_should_skip_scan_for_unconfirmed_sort_requires_confirmed_label() -> None:
    """開啟 auto_adjust_sort 但 after label 不符時跳過本輪。"""

    result = SortAdjustResult(
        attempted=True,
        changed=False,
        preferred_label="由新到舊",
        after_label="最相關",
        reason="sort_update_unconfirmed",
    )

    assert should_skip_scan_for_unconfirmed_sort(
        config=TargetConfig(target_id="target", auto_adjust_sort=True),
        sort_adjust_result=result,
    )
