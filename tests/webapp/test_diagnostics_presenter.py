"""Scan diagnostics presenter tests。"""

from __future__ import annotations

from facebook_monitor.webapp.diagnostics_presenter import _append_sort_block
from facebook_monitor.webapp.diagnostics_presenter import format_scan_failure_reason
from facebook_monitor.webapp.diagnostics_presenter import format_scan_cycle_result_reason
from facebook_monitor.webapp.diagnostics_presenter import format_scan_stop_reason


def test_append_sort_block_shows_menu_candidate_texts_when_present() -> None:
    """sort block 有候選文字時才顯示，方便複製給 review。"""

    lines: list[str] = []
    _append_sort_block(
        lines,
        "comment_sort",
        {
            "attempted": True,
            "changed": False,
            "preferred_label": "由新到舊",
            "before_label": "最相關",
            "after_label": "最相關",
            "reason": "preferred_sort_option_not_found",
            "mutation_suppression_ms": 3200,
            "mutation_suppression_reason": "auto_adjust_sort",
            "menu_candidate_texts": ["最相關", "所有留言"],
        },
    )

    assert 'menu_candidate_texts=["最相關", "所有留言"]' in lines


def test_append_sort_block_hides_empty_menu_candidate_texts() -> None:
    """空候選文字不污染日常 diagnostics。"""

    lines: list[str] = []
    _append_sort_block(
        lines,
        "comment_sort",
        {
            "attempted": False,
            "changed": False,
            "preferred_label": "由新到舊",
            "menu_candidate_texts": [],
        },
    )

    assert not any(line.startswith("menu_candidate_texts=") for line in lines)


def test_sort_unconfirmed_skip_reason_is_user_readable() -> None:
    """排序未確認的保護性跳過會顯示在本輪結果位置。"""

    assert (
        format_scan_cycle_result_reason("sort_adjust_unconfirmed_skip")
        == "調整排序失敗，已跳過掃描"
    )


def test_content_unavailable_failure_reason_is_user_readable() -> None:
    """內容不可見的 failed scan reason 會顯示成連結已失效。"""

    assert format_scan_failure_reason("content_unavailable") == "連結已失效"
    assert (
        format_scan_stop_reason("sort_adjust_unconfirmed_skip")
        == "調整排序失敗，已跳過掃描"
    )
