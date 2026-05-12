"""Scan diagnostics presenter tests。"""

from __future__ import annotations

from facebook_monitor.webapp.diagnostics_presenter import _append_sort_block


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
