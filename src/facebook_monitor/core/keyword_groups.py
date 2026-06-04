"""Include keyword group helpers。

職責：集中 include keyword 分組的固定 slot、legacy fallback 與 flat
projection 規則，避免 Web UI、repository 與 worker 各自推導。
"""

from __future__ import annotations

from collections.abc import Iterable

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import IncludeKeywordGroup
from facebook_monitor.core.models import KeywordGroupMatch

INCLUDE_KEYWORD_GROUP_COUNT = PYTHON_TARGET_CONFIG_DEFAULTS.include_keyword_group_count


def include_keyword_group_label(group_id: object) -> str:
    """回傳 include keyword group 的使用者可見標籤。"""

    normalized_id = str(group_id or "").strip()
    return f"關鍵字 {normalized_id}" if normalized_id else "關鍵字"


def keyword_group_slots(
    values: Iterable[Iterable[str]] = (),
    *,
    group_count: int = INCLUDE_KEYWORD_GROUP_COUNT,
) -> tuple[IncludeKeywordGroup, ...]:
    """將多組 keyword iterable 正規化成固定數量的 group slots。"""

    raw_groups = list(values)
    slots: list[IncludeKeywordGroup] = []
    for index in range(max(int(group_count), 0)):
        group_id = str(index + 1)
        raw_keywords = raw_groups[index] if index < len(raw_groups) else ()
        slots.append(
            IncludeKeywordGroup(
                group_id=group_id,
                label=include_keyword_group_label(group_id),
                keywords=_normalize_keyword_values(raw_keywords),
            )
        )
    return tuple(slots)


def legacy_include_keyword_groups(
    include_keywords: Iterable[str],
    *,
    fill_empty_slots: bool = False,
) -> tuple[IncludeKeywordGroup, ...]:
    """將既有 flat include keywords 轉成第 1 組。"""

    keywords = _normalize_keyword_values(include_keywords)
    if fill_empty_slots:
        return keyword_group_slots((keywords,))
    if not keywords:
        return ()
    return (
        IncludeKeywordGroup(
            group_id="1",
            label=include_keyword_group_label("1"),
            keywords=keywords,
        ),
    )


def normalize_include_keyword_groups(
    groups: Iterable[IncludeKeywordGroup],
    *,
    fill_empty_slots: bool = False,
) -> tuple[IncludeKeywordGroup, ...]:
    """整理 include keyword groups，保留固定 slot 與 keyword 順序。"""

    allowed_group_ids = {str(index + 1) for index in range(INCLUDE_KEYWORD_GROUP_COUNT)}
    normalized_by_id: dict[str, IncludeKeywordGroup] = {}
    overflow: list[IncludeKeywordGroup] = []
    for group in groups:
        group_id = str(group.group_id or "").strip()
        if group_id not in allowed_group_ids:
            overflow.append(group)
            continue
        normalized_by_id[group_id] = _normalized_group(group, group_id=group_id)

    missing_group_ids = [
        str(index + 1)
        for index in range(INCLUDE_KEYWORD_GROUP_COUNT)
        if str(index + 1) not in normalized_by_id
    ]
    for group, group_id in zip(overflow, missing_group_ids, strict=False):
        normalized = _normalized_group(group, group_id=group_id)
        if normalized.keywords or fill_empty_slots:
            normalized_by_id[group_id] = normalized

    ordered: list[IncludeKeywordGroup] = []
    for index in range(INCLUDE_KEYWORD_GROUP_COUNT):
        group_id = str(index + 1)
        existing_group = normalized_by_id.get(group_id)
        if existing_group is None:
            if fill_empty_slots:
                ordered.append(
                    IncludeKeywordGroup(
                        group_id=group_id,
                        label=include_keyword_group_label(group_id),
                        keywords=(),
                    )
                )
            continue
        if existing_group.keywords or fill_empty_slots:
            ordered.append(existing_group)
    return tuple(ordered[:INCLUDE_KEYWORD_GROUP_COUNT])


def effective_include_keyword_groups(
    *,
    include_keywords: Iterable[str],
    include_keyword_groups: Iterable[IncludeKeywordGroup],
) -> tuple[IncludeKeywordGroup, ...]:
    """回傳 runtime 應使用的 include groups，支援 legacy flat fallback。"""

    groups = normalize_include_keyword_groups(include_keyword_groups)
    if any(group.keywords for group in groups):
        return tuple(group for group in groups if group.keywords)
    return legacy_include_keyword_groups(include_keywords)


def flatten_include_keyword_groups(
    groups: Iterable[IncludeKeywordGroup],
) -> tuple[str, ...]:
    """將分組 keyword 轉成 legacy flat projection。"""

    return tuple(
        dict.fromkeys(
            keyword
            for group in normalize_include_keyword_groups(groups, fill_empty_slots=True)
            for keyword in group.keywords
            if keyword
        )
    )


def keyword_group_match_rules(
    matches: Iterable[KeywordGroupMatch],
) -> tuple[str, ...]:
    """回傳 group matches 的 legacy rule projection。"""

    return tuple(dict.fromkeys(match.rule for match in matches if match.rule))


def _normalized_group(group: IncludeKeywordGroup, *, group_id: str) -> IncludeKeywordGroup:
    """整理單一 group 的 id、label 與 keyword tuple。"""

    label = str(group.label or "").strip() or include_keyword_group_label(group_id)
    keywords = _normalize_keyword_values(group.keywords)
    return IncludeKeywordGroup(group_id=group_id, label=label, keywords=keywords)


def _normalize_keyword_values(values: Iterable[str]) -> tuple[str, ...]:
    """整理 keyword values，保留順序並去除空白與重複。"""

    keywords: list[str] = []
    for value in values:
        keyword = str(value).strip()
        if keyword:
            keywords.append(keyword)
    return tuple(dict.fromkeys(keywords))
