"""Keyword text parsing helpers。"""

from __future__ import annotations


def parse_keywords_text(text: str) -> tuple[str, ...]:
    """將逗號或換行分隔的 keyword 文字轉成去重 tuple。"""

    keywords: list[str] = []
    normalized_text = text.replace("\n", ",")
    for raw_item in normalized_text.split(","):
        keyword = raw_item.strip()
        if keyword:
            keywords.append(keyword)
    return tuple(dict.fromkeys(keywords))


__all__ = ["parse_keywords_text"]
