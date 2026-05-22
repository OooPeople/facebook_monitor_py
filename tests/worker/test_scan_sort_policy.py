"""Worker sort policy tests。"""

from __future__ import annotations

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.facebook.sort_controls import SORT_REASON_SORT_CONTROL_NOT_FOUND
from facebook_monitor.facebook.sort_controls import SortAdjustResult
from facebook_monitor.worker.scan_sort_policy import should_skip_scan_for_unconfirmed_sort
from facebook_monitor.worker.scan_sort_policy import sort_control_absent_without_observed_label


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


def test_should_skip_scan_for_unconfirmed_sort_skips_absent_sort_control_by_default() -> None:
    """shared policy 預設仍保護性跳過，避免 comments 被 posts 特例放寬。"""

    result = SortAdjustResult(
        attempted=False,
        changed=False,
        preferred_label="新貼文",
        before_label="",
        after_label="",
        reason=SORT_REASON_SORT_CONTROL_NOT_FOUND,
    )

    assert should_skip_scan_for_unconfirmed_sort(
        config=TargetConfig(target_id="target", auto_adjust_sort=True),
        sort_adjust_result=result,
    )


def test_should_skip_scan_for_unconfirmed_sort_allows_absent_sort_control_when_opted_in() -> None:
    """posts pipeline 可明確允許完全沒有排序控制與目前標籤的社團 feed。"""

    result = SortAdjustResult(
        attempted=False,
        changed=False,
        preferred_label="新貼文",
        before_label="",
        after_label="",
        reason=SORT_REASON_SORT_CONTROL_NOT_FOUND,
    )

    assert not should_skip_scan_for_unconfirmed_sort(
        config=TargetConfig(target_id="target", auto_adjust_sort=True),
        sort_adjust_result=result,
        allow_absent_sort_control_without_label=True,
    )
    assert sort_control_absent_without_observed_label(result)


def test_should_skip_scan_for_unconfirmed_sort_keeps_label_mismatch_guard() -> None:
    """只要曾觀察到排序標籤，未確認 preferred label 仍需保護性跳過。"""

    result = SortAdjustResult(
        attempted=False,
        changed=False,
        preferred_label="新貼文",
        before_label="最相關",
        after_label="最相關",
        reason=SORT_REASON_SORT_CONTROL_NOT_FOUND,
    )

    assert should_skip_scan_for_unconfirmed_sort(
        config=TargetConfig(target_id="target", auto_adjust_sort=True),
        sort_adjust_result=result,
    )
