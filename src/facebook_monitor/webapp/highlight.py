"""Keyword highlight helpers for Web UI presenters.

職責：把外部內容切成安全 text segments，讓 Jinja/DOM render 時不需要使用
innerHTML，也能支援 NFKC 後的 keyword 命中位置。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from facebook_monitor.core.keyword_rules import ZERO_WIDTH_PATTERN
from facebook_monitor.core.keyword_rules import build_keyword_rule


WHITESPACE_CHAR_PATTERN = re.compile(r"\s")


@dataclass(frozen=True)
class HighlightSegment:
    """保存一段可安全 render 的文字與是否高亮。"""

    text: str
    highlighted: bool = False

    def to_dict(self) -> dict[str, Any]:
        """轉成 API response 使用的純 dict。"""

        return {"text": self.text, "highlighted": self.highlighted}


def build_highlight_segments(text: str, matched_rule: str) -> tuple[HighlightSegment, ...]:
    """依 matched keyword rule 回傳原文 segments。"""

    if not text:
        return ()
    rule = build_keyword_rule(matched_rule)
    if rule is None:
        return (HighlightSegment(text=text),)

    normalized_text, normalized_to_original = _normalize_with_original_map(text)
    ranges: list[tuple[int, int]] = []
    for term in rule.terms:
        start = 0
        while True:
            index = normalized_text.find(term, start)
            if index < 0:
                break
            original_indexes = normalized_to_original[index : index + len(term)]
            ranges.append((min(original_indexes), max(original_indexes) + 1))
            start = index + 1
    merged_ranges = _merge_ranges(ranges)
    if not merged_ranges:
        return (HighlightSegment(text=text),)

    segments: list[HighlightSegment] = []
    cursor = 0
    for start, end in merged_ranges:
        if cursor < start:
            segments.append(HighlightSegment(text=text[cursor:start]))
        segments.append(HighlightSegment(text=text[start:end], highlighted=True))
        cursor = end
    if cursor < len(text):
        segments.append(HighlightSegment(text=text[cursor:]))
    return tuple(segment for segment in segments if segment.text)


def build_highlight_segment_dicts(text: str, matched_rule: str) -> list[dict[str, Any]]:
    """回傳 API response 可序列化的 highlight segments。"""

    return [segment.to_dict() for segment in build_highlight_segments(text, matched_rule)]


def _normalize_with_original_map(text: str) -> tuple[str, list[int]]:
    """產生 normalized text 與 normalized index 到原文 index 的 mapping。"""

    normalized_chars: list[str] = []
    normalized_to_original: list[int] = []
    last_was_space = False
    for original_index, raw_char in enumerate(text):
        if ZERO_WIDTH_PATTERN.fullmatch(raw_char):
            continue
        normalized_piece = unicodedata.normalize("NFKC", raw_char).lower()
        for char in normalized_piece:
            if WHITESPACE_CHAR_PATTERN.fullmatch(char):
                if not last_was_space and normalized_chars:
                    normalized_chars.append(" ")
                    normalized_to_original.append(original_index)
                    last_was_space = True
                continue
            normalized_chars.append(char)
            normalized_to_original.append(original_index)
            last_was_space = False
    if normalized_chars and normalized_chars[-1] == " ":
        normalized_chars.pop()
        normalized_to_original.pop()
    return "".join(normalized_chars), normalized_to_original


def _merge_ranges(ranges: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """合併重疊或相鄰 range。"""

    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if start >= end:
            continue
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return tuple(merged)
