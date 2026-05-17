"""Keyword rule pure logic tests。"""

from __future__ import annotations

from typing import Literal

from hypothesis import given
from hypothesis import strategies as st

from facebook_monitor.core.keyword_rules import build_keyword_rule
from facebook_monitor.core.keyword_rules import evaluate_keyword_rules
from facebook_monitor.core.keyword_rules import match_rules
from facebook_monitor.core.keyword_rules import mask_exclude_ignore_phrases
from facebook_monitor.core.keyword_rules import normalize_for_match
from facebook_monitor.core.keyword_rules import parse_keyword_input
from facebook_monitor.core.keyword_rules import parse_keyword_values


SURROGATE_CATEGORIES: tuple[Literal["Cs"], ...] = ("Cs",)


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
    """已保存 tuple 可逐項轉成 keyword 規則。"""

    rules = parse_keyword_values(("票,不應拆逗號", "讓票;交換"))

    assert [rule.raw for rule in rules] == ["票,不應拆逗號", "讓票", "交換"]


def test_match_rules_empty_include_does_not_match() -> None:
    """未設定 include 規則時不應命中任何文字。"""

    result = match_rules((), "任意內容")

    assert result.matched is False
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


def test_evaluate_keyword_rules_returns_all_include_matches() -> None:
    """同一內容可命中多組 include 規則，供 UI 全部標示與高亮。"""

    result = evaluate_keyword_rules(
        "售6/5,6/6的票各一張",
        include_keywords=("6/5;6/6",),
    )

    assert result.eligible is True
    assert result.include_rules == ("6/5", "6/6")
    assert result.include_rule == "6/5;6/6"
    assert result.display_rule == "6/5;6/6"


def test_evaluate_keyword_rules_deduplicates_repeated_include_matches() -> None:
    """重複 keyword 規則不應造成重複 badge 或重複 persistence 子列。"""

    result = evaluate_keyword_rules(
        "售6/5,6/6的票各一張",
        include_keywords=("6/5;6/5;6/6",),
    )

    assert result.eligible is True
    assert result.include_rules == ("6/5", "6/6")
    assert result.include_rule == "6/5;6/6"


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


def test_evaluate_keyword_rules_masks_exclude_ignore_phrase_only() -> None:
    """排除字忽略片語只遮罩片語本身，不放行整篇文字。"""

    allowed = evaluate_keyword_rules(
        "全收優先，紙本票兩張",
        include_keywords=("票",),
        exclude_keywords=("收",),
        exclude_ignore_phrases=("全收;回收",),
    )
    blocked = evaluate_keyword_rules(
        "全收優先，但我也想收 6/6 內野票",
        include_keywords=("票",),
        exclude_keywords=("收",),
        exclude_ignore_phrases=("全收;回收",),
    )

    assert allowed.eligible is True
    assert allowed.include_rule == "票"
    assert allowed.exclude_rule == ""
    assert blocked.eligible is False
    assert blocked.include_rule == "票"
    assert blocked.exclude_rule == "收"


def test_evaluate_keyword_rules_ignore_phrase_does_not_affect_include() -> None:
    """ignore phrase 只影響 exclude 判斷，include 仍使用原始 normalized text。"""

    result = evaluate_keyword_rules(
        "全收優先",
        include_keywords=("全收",),
        exclude_keywords=(),
        exclude_ignore_phrases=("全收",),
    )

    assert result.eligible is True
    assert result.include_rule == "全收"


def test_mask_exclude_ignore_phrases_uses_nfkc_normalization() -> None:
    """全形空白等 NFKC 差異不影響 ignore phrase 遮罩。"""

    masked = mask_exclude_ignore_phrases(
        normalize_for_match("全　收優先，紙本票兩張"),
        ("全 收",),
    )

    assert "收" not in masked
    assert "紙本票" in masked


def test_evaluate_keyword_rules_empty_include_is_not_eligible() -> None:
    """未設定 include 時不符合通知條件。"""

    result = evaluate_keyword_rules("任意貼文", include_keywords=())

    assert result.eligible is False
    assert result.include_rule == ""
    assert result.display_rule == ""


def test_evaluate_keyword_rules_uses_nfkc_normalization() -> None:
    """全形/半形差異不影響 keyword 比對。"""

    result = evaluate_keyword_rules(
        "想買 A席 １２３",
        include_keywords=("Ａ席 123",),
    )

    assert result.eligible is True
    assert result.include_rule == "Ａ席 123"


def test_build_keyword_rule_ignores_blank_input() -> None:
    """空白規則不會建立無效比對條件。"""

    assert build_keyword_rule(" \n ") is None


@given(st.text())
def test_normalize_for_match_is_idempotent(value: str) -> None:
    """任意文字重複 normalize 不應再改變結果。"""

    normalized = normalize_for_match(value)

    assert normalize_for_match(normalized) == normalized


@given(
    keyword=st.text(
        alphabet=st.characters(
            blacklist_categories=SURROGATE_CATEGORIES,
            blacklist_characters=";",
        ),
        min_size=1,
    ).filter(lambda value: bool(normalize_for_match(value))),
    prefix=st.text(alphabet=st.characters(blacklist_categories=SURROGATE_CATEGORIES)),
    suffix=st.text(alphabet=st.characters(blacklist_categories=SURROGATE_CATEGORIES)),
)
def test_single_include_keyword_matches_when_normalized_text_contains_keyword(
    keyword: str,
    prefix: str,
    suffix: str,
) -> None:
    """單一 include keyword 出現在 normalized text 中時必須命中。"""

    result = evaluate_keyword_rules(
        f"{prefix} {keyword} {suffix}",
        include_keywords=(keyword,),
    )

    assert result.eligible
