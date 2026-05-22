"""Input limit policy tests。"""

from __future__ import annotations

from facebook_monitor.core.input_limits import MAX_KEYWORDS_PER_FIELD
from facebook_monitor.core.input_limits import MAX_KEYWORD_LENGTH
from facebook_monitor.core.input_limits import MAX_KEYWORD_TEXT_LENGTH
from facebook_monitor.core.input_limits import normalize_display_name
from facebook_monitor.core.input_limits import parse_limited_keywords_text


def test_parse_limited_keywords_text_accepts_normal_keywords() -> None:
    """正常 keyword text 維持既有 trim / dedupe 語義。"""

    assert parse_limited_keywords_text(" 票,交換,票 ", field_label="關鍵字") == (
        "票",
        "交換",
    )


def test_parse_limited_keywords_text_rejects_oversized_text() -> None:
    """過大的 textarea 不進入後續 config 寫入。"""

    try:
        parse_limited_keywords_text("x" * (MAX_KEYWORD_TEXT_LENGTH + 1), field_label="關鍵字")
    except ValueError as exc:
        assert str(exc) == f"關鍵字 不可超過 {MAX_KEYWORD_TEXT_LENGTH} 個字元"
    else:
        raise AssertionError("expected keyword text length limit")


def test_parse_limited_keywords_text_rejects_too_many_keywords() -> None:
    """keyword 數量需有明確上限。"""

    text = ";".join(f"k{i}" for i in range(MAX_KEYWORDS_PER_FIELD + 1))

    try:
        parse_limited_keywords_text(text, field_label="關鍵字")
    except ValueError as exc:
        assert str(exc) == f"關鍵字 最多 {MAX_KEYWORDS_PER_FIELD} 個"
    else:
        raise AssertionError("expected keyword count limit")


def test_parse_limited_keywords_text_rejects_single_long_keyword() -> None:
    """單一 keyword 過長時應拒絕。"""

    try:
        parse_limited_keywords_text("x" * (MAX_KEYWORD_LENGTH + 1), field_label="關鍵字")
    except ValueError as exc:
        assert str(exc) == f"關鍵字 單一項目不可超過 {MAX_KEYWORD_LENGTH} 個字元"
    else:
        raise AssertionError("expected keyword item length limit")


def test_normalize_display_name_rejects_long_name() -> None:
    """顯示名稱需要套用上限。"""

    try:
        normalize_display_name("x" * 121)
    except ValueError as exc:
        assert str(exc) == "顯示名稱 不可超過 120 個字元"
    else:
        raise AssertionError("expected display name length limit")
