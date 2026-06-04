"""Facebook 文字清理 helper。

職責：保存重複文字折疊語義，供 extractor 與通知 payload 共用，
避免同一段 DOM 文字被 Facebook 重複輸出時造成通知內容重複。
"""

from __future__ import annotations

FACEBOOK_EXPAND_MORE_LABELS = (
    "顯示更多",
    "查看更多",
    "See more",
)
FACEBOOK_COLLAPSE_LESS_LABELS = (
    "顯示較少",
    "顯示更少",
    "See less",
)
FACEBOOK_EXPAND_COLLAPSE_LABELS = (
    *FACEBOOK_EXPAND_MORE_LABELS,
    *FACEBOOK_COLLAPSE_LESS_LABELS,
)
FACEBOOK_ZERO_WIDTH_CHARS = "\u200b\u200c\u200d\ufeff"
FACEBOOK_ZERO_WIDTH_TRANSLATION = str.maketrans("", "", FACEBOOK_ZERO_WIDTH_CHARS)


def normalize_text(value: object) -> str:
    """將任意文字壓成單一空白分隔的穩定格式。"""

    return " ".join(str(value or "").translate(FACEBOOK_ZERO_WIDTH_TRANSLATION).split())


def normalize_multiline_text(value: object) -> str:
    """整理顯示用文字，保留行分隔但壓縮每行內空白。"""

    raw_text = (
        str(value or "")
        .translate(FACEBOOK_ZERO_WIDTH_TRANSLATION)
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    lines = [" ".join(line.split()) for line in raw_text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()

    compacted_lines: list[str] = []
    previous_was_empty = False
    for line in lines:
        if not line:
            if previous_was_empty:
                continue
            previous_was_empty = True
            compacted_lines.append("")
            continue
        previous_was_empty = False
        compacted_lines.append(line)
    return "\n".join(compacted_lines)


def strip_facebook_expand_collapse_labels(value: object) -> str:
    """移除 Facebook 展開/收合按鈕文字，避免 UI label 污染掃描內容。"""

    text = normalize_text(value)
    if not text:
        return ""
    while text:
        original_text = text
        for label in FACEBOOK_EXPAND_COLLAPSE_LABELS:
            if text == label:
                return ""
            if text.endswith(f" {label}"):
                text = normalize_text(text[: -len(label)])
        if text == original_text:
            break
    return normalize_text(text)


def strip_facebook_expand_collapse_labels_multiline(value: object) -> str:
    """逐行移除 Facebook 展開/收合按鈕文字，保留內容換行。"""

    text = normalize_multiline_text(value)
    if not text:
        return ""
    cleaned_lines = [
        strip_facebook_expand_collapse_labels(line) for line in text.split("\n")
    ]
    while cleaned_lines and not cleaned_lines[0]:
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1]:
        cleaned_lines.pop()
    return normalize_multiline_text("\n".join(cleaned_lines))


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


def collapse_repeated_adjacent_multiline_text(value: object) -> str:
    """折疊相鄰重複的顯示文字，同時保留未重複內容的換行。"""

    text = normalize_multiline_text(value)
    if not text:
        return ""
    if "\n" not in text:
        return collapse_repeated_adjacent_text(text)
    text = normalize_multiline_text(
        "\n".join(collapse_repeated_adjacent_text(line) for line in text.split("\n"))
    )

    while True:
        lines = text.split("\n")
        if len(lines) > 1 and len(lines) % 2 == 0:
            half_length = len(lines) // 2
            left_lines = lines[:half_length]
            right_lines = lines[half_length:]
            left = "\n".join(left_lines)
            if len(left) >= 8 and left_lines == right_lines:
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


def clean_facebook_text(value: object) -> str:
    """套用 extractor 共用文字清理，供 Python runtime 與通知保底使用。"""

    return collapse_repeated_adjacent_text(
        strip_facebook_expand_collapse_labels(collapse_repeated_adjacent_text(value))
    )


def clean_facebook_multiline_text(value: object) -> str:
    """清理顯示用 Facebook 文字，保留原始可用換行資訊。"""

    text = collapse_repeated_adjacent_multiline_text(value)
    text = strip_facebook_expand_collapse_labels_multiline(text)
    return collapse_repeated_adjacent_multiline_text(text)
