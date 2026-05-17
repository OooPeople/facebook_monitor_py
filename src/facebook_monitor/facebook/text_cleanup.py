"""Facebook 文字清理 helper。

職責：保存重複文字折疊語義，供 extractor 與通知 payload 共用，
避免同一段 DOM 文字被 Facebook 重複輸出時造成通知內容重複。
"""

from __future__ import annotations


def normalize_text(value: object) -> str:
    """將任意文字壓成單一空白分隔的穩定格式。"""

    return " ".join(str(value or "").split())


def collapse_repeated_adjacent_text(value: object) -> str:
    """移除整段相鄰重複文字，保留留言文字清理語義。"""

    text = normalize_text(value)
    if not text:
        return ""

    while True:
        tokens = text.split(" ")
        if len(tokens) > 1 and len(tokens) % 2 == 0:
            half_length = len(tokens) // 2
            left = " ".join(tokens[:half_length])
            right = " ".join(tokens[half_length:])
            if len(left) >= 8 and left == right:
                text = left
                continue

        if len(text) % 2 == 0:
            half_length = len(text) // 2
            left = text[:half_length]
            right = text[half_length:]
            if len(left) >= 8 and left == right:
                text = left
                continue

        return text
