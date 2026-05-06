"""Facebook 監視關鍵字規則。

職責：集中保存從 userscript 遷移而來的 include / exclude 比對語義。
分號代表 OR，空白代表 AND；未設定 include 規則時代表全部通過。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass


INCLUDE_ALL_LABEL = "(include-all)"

ZERO_WIDTH_PATTERN = re.compile(r"[\u200b-\u200d\ufeff]")
WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class KeywordRule:
    """保存單一 keyword 規則的原始顯示文字與比對 terms。"""

    raw: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class KeywordMatchResult:
    """保存一組規則對單段文字的比對結果。"""

    matched: bool
    rule: str = ""


@dataclass(frozen=True)
class KeywordEvaluation:
    """保存 include / exclude 合併判斷後的結果。"""

    eligible: bool
    include_rule: str = ""
    exclude_rule: str = ""

    @property
    def display_rule(self) -> str:
        """回傳適合 UI 與通知使用的命中規則名稱。"""

        if not self.eligible:
            return ""
        return self.include_rule or INCLUDE_ALL_LABEL


def normalize_text(value: object) -> str:
    """移除零寬字元、壓縮空白並去頭尾空白。"""

    text = ZERO_WIDTH_PATTERN.sub("", str(value or ""))
    return WHITESPACE_PATTERN.sub(" ", text).strip()


def normalize_for_match(value: object) -> str:
    """轉成小寫的 keyword 比對文字。"""

    return normalize_text(value).lower()


def build_keyword_rule(rule: object) -> KeywordRule | None:
    """將單一 keyword 規則整理成標準格式。"""

    normalized_rule = normalize_text(rule)
    if not normalized_rule:
        return None

    terms = tuple(
        term for term in (normalize_for_match(part) for part in normalized_rule.split(" ")) if term
    )
    if not terms:
        return None
    return KeywordRule(raw=normalized_rule, terms=terms)


def parse_keyword_input(raw_input: object) -> tuple[KeywordRule, ...]:
    """將 `a b;c` 類型輸入拆成 keyword 規則。"""

    rules: list[KeywordRule] = []
    for raw_rule in str(raw_input or "").split(";"):
        rule = build_keyword_rule(raw_rule)
        if rule is not None:
            rules.append(rule)
    return tuple(rules)


def parse_keyword_values(values: Iterable[object]) -> tuple[KeywordRule, ...]:
    """將已保存的 keyword tuple 轉成比對規則。"""

    rules: list[KeywordRule] = []
    for value in values:
        rules.extend(parse_keyword_input(value))
    return tuple(rules)


def matches_keyword_rule(rule: KeywordRule | None, normalized_text: str) -> bool:
    """檢查單一 keyword 規則是否命中指定文字。"""

    return bool(rule and all(term in normalized_text for term in rule.terms))


def match_rules(rules: Iterable[KeywordRule], normalized_text: str) -> KeywordMatchResult:
    """逐條規則比對，任一規則成立就視為命中。"""

    rule_tuple = tuple(rules)
    if not rule_tuple:
        return KeywordMatchResult(matched=True, rule="")

    for rule in rule_tuple:
        if matches_keyword_rule(rule, normalized_text):
            return KeywordMatchResult(matched=True, rule=rule.raw)
    return KeywordMatchResult(matched=False, rule="")


def evaluate_keyword_rules(
    text: object,
    include_keywords: Iterable[object],
    exclude_keywords: Iterable[object] = (),
) -> KeywordEvaluation:
    """依 include / exclude 規則判斷單段文字是否符合監視條件。"""

    normalized_text = normalize_for_match(text)
    include_result = match_rules(parse_keyword_values(include_keywords), normalized_text)
    exclude_rules = parse_keyword_values(exclude_keywords)
    exclude_result = (
        match_rules(exclude_rules, normalized_text)
        if exclude_rules
        else KeywordMatchResult(matched=False, rule="")
    )
    return KeywordEvaluation(
        eligible=include_result.matched and not exclude_result.matched,
        include_rule=include_result.rule,
        exclude_rule=exclude_result.rule if exclude_result.matched else "",
    )
