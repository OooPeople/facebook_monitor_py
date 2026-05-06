"""Keyword rule pure logic tests。"""

from __future__ import annotations

from facebook_monitor.core.keyword_rules import INCLUDE_ALL_LABEL
from facebook_monitor.core.keyword_rules import build_keyword_rule
from facebook_monitor.core.keyword_rules import evaluate_keyword_rules
from facebook_monitor.core.keyword_rules import match_rules
from facebook_monitor.core.keyword_rules import normalize_for_match
from facebook_monitor.core.keyword_rules import parse_keyword_input
from facebook_monitor.core.keyword_rules import parse_keyword_values


def test_normalize_for_match_removes_zero_width_and_collapses_spaces() -> None:
    """比對文字會移除零寬字元、壓縮空白並轉小寫。"""

    assert normalize_for_match(" A\u200b  B\nC ") == "a b c"


def test_parse_keyword_input_uses_semicolon_or_and_space_and() -> None:
    """分號拆成 OR 規則，規則內空白拆成 AND terms。"""

    rules = parse_keyword_input("4/4 熱區;讓票")

    assert [rule.raw for rule in rules] == ["4/4 熱區", "讓票"]
    assert rules[0].terms == ("4/4", "熱區")
    assert rules[1].terms == ("讓票",)


def test_parse_keyword_values_keeps_existing_storage_shape() -> None:
    """已保存 tuple 可逐項轉成 userscript 規則。"""

    rules = parse_keyword_values(("票,不應拆逗號", "讓票;交換"))

    assert [rule.raw for rule in rules] == ["票,不應拆逗號", "讓票", "交換"]


def test_match_rules_empty_include_matches_all() -> None:
    """未設定 include 規則時，沿用 userscript 的 include-all 語義。"""

    result = match_rules((), "任意內容")

    assert result.matched is True
    assert result.rule == ""


def test_evaluate_keyword_rules_matches_and_terms() -> None:
    """同一規則內所有 terms 都出現才視為命中。"""

    result = evaluate_keyword_rules(
        "我想徵 4/4 熱區 兩張",
        include_keywords=("4/4 熱區",),
    )

    assert result.eligible is True
    assert result.include_rule == "4/4 熱區"
    assert result.display_rule == "4/4 熱區"


def test_evaluate_keyword_rules_uses_semicolon_or() -> None:
    """同一輸入中的分號規則任一命中即可。"""

    result = evaluate_keyword_rules(
        "有人想交換票券",
        include_keywords=("讓票;交換",),
    )

    assert result.eligible is True
    assert result.include_rule == "交換"


def test_evaluate_keyword_rules_exclude_blocks_include_match() -> None:
    """exclude 命中時，即使 include 命中也不符合通知條件。"""

    result = evaluate_keyword_rules(
        "售完 4/4 熱區",
        include_keywords=("4/4 熱區",),
        exclude_keywords=("售完",),
    )

    assert result.eligible is False
    assert result.include_rule == "4/4 熱區"
    assert result.exclude_rule == "售完"
    assert result.display_rule == ""


def test_evaluate_keyword_rules_include_all_display_label() -> None:
    """未設定 include 時，display rule 使用明確標籤。"""

    result = evaluate_keyword_rules("任意貼文", include_keywords=())

    assert result.eligible is True
    assert result.include_rule == ""
    assert result.display_rule == INCLUDE_ALL_LABEL


def test_build_keyword_rule_ignores_blank_input() -> None:
    """空白規則不會建立無效比對條件。"""

    assert build_keyword_rule(" \n ") is None
