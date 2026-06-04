"""Facebook 監視關鍵字規則。

職責：集中保存 include / exclude 比對語義。
分號代表 OR，空白代表 AND；未設定 include 規則時不命中。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

import ahocorasick  # type: ignore[import-not-found]

from facebook_monitor.core.keyword_groups import effective_include_keyword_groups
from facebook_monitor.core.keyword_groups import keyword_group_match_rules
from facebook_monitor.core.models import IncludeKeywordGroup
from facebook_monitor.core.models import KeywordGroupMatch


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
    rules: tuple[str, ...] = ()
    group_results: tuple["KeywordGroupMatchResult", ...] = ()
    group_matches: tuple[KeywordGroupMatch, ...] = ()


@dataclass(frozen=True)
class KeywordGroupMatchResult:
    """保存 include keyword 單一分組的比對結果。"""

    group_id: str
    group_label: str
    matched: bool
    rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class KeywordEvaluation:
    """保存 include / exclude 合併判斷後的結果。"""

    eligible: bool
    include_rule: str = ""
    exclude_rule: str = ""
    include_rules: tuple[str, ...] = ()
    exclude_rules: tuple[str, ...] = ()
    include_group_results: tuple[KeywordGroupMatchResult, ...] = ()
    include_group_matches: tuple[KeywordGroupMatch, ...] = ()

    @property
    def display_rule(self) -> str:
        """回傳適合 UI 與通知使用的命中規則名稱。"""

        if not self.eligible:
            return ""
        return self.include_rule


def normalize_text(value: object) -> str:
    """移除零寬字元、壓縮空白並去頭尾空白。"""

    text = ZERO_WIDTH_PATTERN.sub("", str(value or ""))
    return WHITESPACE_PATTERN.sub(" ", text).strip()


def normalize_for_match(value: object) -> str:
    """轉成 NFKC 與小寫後的 keyword 比對文字。"""

    normalized = unicodedata.normalize("NFKC", normalize_text(value))
    return normalize_text(normalized).lower()


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


def format_keyword_rules(rules: Iterable[str]) -> str:
    """將多組命中規則整理成既有儲存欄位可承載的顯示文字。"""

    return ";".join(dedupe_keyword_rules(rules))


def split_keyword_rule_text(value: str) -> tuple[str, ...]:
    """將既有字串欄位中的多組 keyword rule 拆回顯示清單。"""

    return dedupe_keyword_rules(rule.raw for rule in parse_keyword_input(value))


def dedupe_keyword_rules(rules: Iterable[str]) -> tuple[str, ...]:
    """依出現順序去除空白與重複 keyword rule。"""

    return tuple(rule for rule in dict.fromkeys(rules) if rule)


def matches_keyword_rule(rule: KeywordRule | None, normalized_text: str) -> bool:
    """檢查單一 keyword 規則是否命中指定文字。"""

    return bool(rule and all(term in normalized_text for term in rule.terms))


def match_rules(rules: Iterable[KeywordRule], normalized_text: str) -> KeywordMatchResult:
    """比對全部規則並回傳所有命中的規則。"""

    return KeywordRuleSetMatcher(rules).match(normalized_text)


class KeywordRuleSetMatcher:
    """以 Aho-Corasick automaton 一次掃描多組 keyword terms。"""

    def __init__(self, rules: Iterable[KeywordRule]) -> None:
        self.rules = tuple(rules)
        terms = tuple(
            dict.fromkeys(term for rule in self.rules for term in rule.terms if term)
        )
        self._automaton = _build_automaton(terms)

    def match(self, normalized_text: str) -> KeywordMatchResult:
        """回傳全部成立的 OR 規則，單一規則內 terms 維持 AND 語義。"""

        if not self.rules:
            return KeywordMatchResult(matched=False, rule="", rules=())
        found_terms = set(_iter_automaton_values(self._automaton, normalized_text))
        matched_rules = dedupe_keyword_rules(
            rule.raw for rule in self.rules if all(term in found_terms for term in rule.terms)
        )
        return KeywordMatchResult(
            matched=bool(matched_rules),
            rule=matched_rules[0] if matched_rules else "",
            rules=matched_rules,
        )


class GroupedKeywordRuleSetMatcher:
    """以固定 include groups 套用組內 OR、組間 AND 的 matcher。"""

    def __init__(
        self,
        *,
        include_keywords: Iterable[object],
        include_keyword_groups: Iterable[IncludeKeywordGroup] = (),
    ) -> None:
        groups = effective_include_keyword_groups(
            include_keywords=tuple(str(value) for value in include_keywords),
            include_keyword_groups=include_keyword_groups,
        )
        self.group_rules = tuple(
            (group, parse_keyword_values(group.keywords))
            for group in groups
            if group.keywords
        )
        terms = tuple(
            dict.fromkeys(
                term
                for _group, rules in self.group_rules
                for rule in rules
                for term in rule.terms
                if term
            )
        )
        self._automaton = _build_automaton(terms)

    def match(self, normalized_text: str) -> KeywordMatchResult:
        """回傳所有 include groups 是否成立與各組命中的 rules。"""

        if not self.group_rules:
            return KeywordMatchResult(matched=False, rule="", rules=())
        found_terms = set(_iter_automaton_values(self._automaton, normalized_text))
        group_results: list[KeywordGroupMatchResult] = []
        group_matches: list[KeywordGroupMatch] = []
        for group, group_rules in self.group_rules:
            matched_rules = dedupe_keyword_rules(
                rule.raw
                for rule in group_rules
                if all(term in found_terms for term in rule.terms)
            )
            group_results.append(
                KeywordGroupMatchResult(
                    group_id=group.group_id,
                    group_label=group.label,
                    matched=bool(matched_rules),
                    rules=matched_rules,
                )
            )
            group_matches.extend(
                KeywordGroupMatch(
                    group_id=group.group_id,
                    group_label=group.label,
                    rule=rule,
                )
                for rule in matched_rules
            )
        matched = all(result.matched for result in group_results)
        matched_group_rules = keyword_group_match_rules(group_matches) if matched else ()
        return KeywordMatchResult(
            matched=matched,
            rule=format_keyword_rules(matched_group_rules),
            rules=matched_group_rules,
            group_results=tuple(group_results),
            group_matches=tuple(group_matches) if matched else (),
        )


class PhraseRangeMatcher:
    """以 Aho-Corasick automaton 尋找多組片語 range。"""

    def __init__(self, phrases: Iterable[str]) -> None:
        self.phrases = tuple(dict.fromkeys(phrase for phrase in phrases if phrase))
        self._automaton = _build_automaton(self.phrases)

    def ranges(self, normalized_text: str) -> tuple[tuple[int, int], ...]:
        """回傳 normalized text 中所有片語命中 range。"""

        ranges: list[tuple[int, int]] = []
        for end_index, phrase in _iter_automaton_matches(self._automaton, normalized_text):
            ranges.append((end_index - len(phrase) + 1, end_index + 1))
        return _merge_ranges(ranges)


class CompiledKeywordMatcher:
    """保存 target config 編譯後的 keyword matcher，可重複評估多篇內容。"""

    def __init__(
        self,
        *,
        include_keywords: Iterable[object],
        include_keyword_groups: Iterable[IncludeKeywordGroup] = (),
        exclude_keywords: Iterable[object] = (),
        exclude_ignore_phrases: Iterable[object] = (),
    ) -> None:
        self.include_matcher = GroupedKeywordRuleSetMatcher(
            include_keywords=include_keywords,
            include_keyword_groups=include_keyword_groups,
        )
        self.exclude_matcher = KeywordRuleSetMatcher(parse_keyword_values(exclude_keywords))
        self.exclude_ignore_matcher = PhraseRangeMatcher(
            normalize_for_match(rule.raw)
            for rule in parse_keyword_values(exclude_ignore_phrases)
        )

    def evaluate(self, text: object) -> KeywordEvaluation:
        """依 include / exclude 規則判斷單段文字是否符合監視條件。"""

        normalized_text = normalize_for_match(text)
        include_result = self.include_matcher.match(normalized_text)
        exclude_text = _mask_ranges(normalized_text, self.exclude_ignore_matcher.ranges(normalized_text))
        exclude_result = self.exclude_matcher.match(exclude_text)
        include_rules = include_result.rules
        exclude_rules = exclude_result.rules if exclude_result.matched else ()
        return KeywordEvaluation(
            eligible=include_result.matched and not exclude_result.matched,
            include_rule=format_keyword_rules(include_rules),
            exclude_rule=format_keyword_rules(exclude_rules),
            include_rules=include_rules,
            exclude_rules=exclude_rules,
            include_group_results=include_result.group_results,
            include_group_matches=include_result.group_matches,
        )


def compile_keyword_matcher(
    *,
    include_keywords: Iterable[object],
    include_keyword_groups: Iterable[IncludeKeywordGroup] = (),
    exclude_keywords: Iterable[object] = (),
    exclude_ignore_phrases: Iterable[object] = (),
) -> CompiledKeywordMatcher:
    """編譯 target keyword 設定，供單輪掃描重複使用。"""

    return CompiledKeywordMatcher(
        include_keywords=include_keywords,
        include_keyword_groups=include_keyword_groups,
        exclude_keywords=exclude_keywords,
        exclude_ignore_phrases=exclude_ignore_phrases,
    )


def mask_exclude_ignore_phrases(
    normalized_text: str,
    exclude_ignore_phrases: Iterable[object],
) -> str:
    """在 normalized text 上遮罩排除字忽略片語的命中範圍。"""

    matcher = PhraseRangeMatcher(
        normalize_for_match(rule.raw) for rule in parse_keyword_values(exclude_ignore_phrases)
    )
    return _mask_ranges(normalized_text, matcher.ranges(normalized_text))


def evaluate_keyword_rules(
    text: object,
    include_keywords: Iterable[object],
    include_keyword_groups: Iterable[IncludeKeywordGroup] = (),
    exclude_keywords: Iterable[object] = (),
    exclude_ignore_phrases: Iterable[object] = (),
) -> KeywordEvaluation:
    """依 include / exclude 規則判斷單段文字是否符合監視條件。"""

    return compile_keyword_matcher(
        include_keywords=include_keywords,
        include_keyword_groups=include_keyword_groups,
        exclude_keywords=exclude_keywords,
        exclude_ignore_phrases=exclude_ignore_phrases,
    ).evaluate(text)


def _build_automaton(values: Iterable[str]) -> ahocorasick.Automaton | None:
    """建立 Aho-Corasick automaton；空集合回傳 None。"""

    unique_values = tuple(dict.fromkeys(value for value in values if value))
    if not unique_values:
        return None
    automaton = ahocorasick.Automaton()
    for value in unique_values:
        automaton.add_word(value, value)
    automaton.make_automaton()
    return automaton


def _iter_automaton_matches(
    automaton: ahocorasick.Automaton | None,
    text: str,
) -> Iterable[tuple[int, str]]:
    """走訪 automaton 命中結果。"""

    if automaton is None:
        return ()
    return automaton.iter(text)


def _iter_automaton_values(automaton: ahocorasick.Automaton | None, text: str) -> Iterable[str]:
    """走訪 automaton 命中的 value。"""

    return (value for _end_index, value in _iter_automaton_matches(automaton, text))


def _mask_ranges(normalized_text: str, ranges: Iterable[tuple[int, int]]) -> str:
    """將 normalized text 指定 ranges 以空白遮罩。"""

    merged_ranges = _merge_ranges(ranges)
    if not merged_ranges:
        return normalized_text
    chars = list(normalized_text)
    for start, end in merged_ranges:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _merge_ranges(ranges: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """合併重疊或相鄰 range。"""

    merged_ranges: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if start >= end:
            continue
        if not merged_ranges or start > merged_ranges[-1][1]:
            merged_ranges.append((start, end))
            continue
        previous_start, previous_end = merged_ranges[-1]
        merged_ranges[-1] = (previous_start, max(previous_end, end))
    return tuple(merged_ranges)
