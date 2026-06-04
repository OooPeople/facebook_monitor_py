"""Web / API input size limits。

職責：集中本機 Web UI 可接受的文字長度與關鍵字數量限制，避免 route
或 form model 各自定義不同上限。
"""

from __future__ import annotations

from facebook_monitor.core.keyword_text import parse_keywords_text


MAX_TARGET_URL_LENGTH = 2048
MAX_DISPLAY_NAME_LENGTH = 120
MAX_KEYWORD_TEXT_LENGTH = 4000
MAX_KEYWORDS_PER_FIELD = 100
MAX_KEYWORD_LENGTH = 120
MAX_NTFY_TOPIC_LENGTH = 128
MAX_NOTIFICATION_ENDPOINT_LENGTH = 2048
MAX_REQUEST_BODY_BYTES = 256 * 1024


def ensure_text_length(value: str, *, field_label: str, max_length: int) -> str:
    """確認文字長度未超過欄位上限，並回傳原字串。"""

    text = str(value or "")
    if len(text) > max_length:
        raise ValueError(f"{field_label} 不可超過 {max_length} 個字元")
    return text


def normalize_display_name(value: str) -> str:
    """整理使用者輸入的顯示名稱並套用長度限制。"""

    return ensure_text_length(
        str(value or "").strip(),
        field_label="顯示名稱",
        max_length=MAX_DISPLAY_NAME_LENGTH,
    )


def normalize_target_url(value: str) -> str:
    """整理 target URL 並套用長度限制。"""

    return ensure_text_length(
        str(value or "").strip(),
        field_label="Facebook URL",
        max_length=MAX_TARGET_URL_LENGTH,
    )


def normalize_ntfy_topic(value: str) -> str:
    """整理 ntfy topic 並套用長度限制。"""

    return ensure_text_length(
        str(value or "").strip(),
        field_label="ntfy topic",
        max_length=MAX_NTFY_TOPIC_LENGTH,
    )


def normalize_notification_endpoint(value: str, *, field_label: str) -> str:
    """整理通知 endpoint 並套用共用長度限制。"""

    return ensure_text_length(
        str(value or "").strip(),
        field_label=field_label,
        max_length=MAX_NOTIFICATION_ENDPOINT_LENGTH,
    )


def validate_keyword_text(value: str, *, field_label: str) -> str:
    """確認 raw keyword textarea 長度不會過大。"""

    return ensure_text_length(
        str(value or ""),
        field_label=field_label,
        max_length=MAX_KEYWORD_TEXT_LENGTH,
    )


def validate_keyword_values(values: tuple[str, ...], *, field_label: str) -> tuple[str, ...]:
    """確認解析後的 keyword 數量與單項長度。"""

    if len(values) > MAX_KEYWORDS_PER_FIELD:
        raise ValueError(f"{field_label} 最多 {MAX_KEYWORDS_PER_FIELD} 個")
    for value in values:
        if len(value) > MAX_KEYWORD_LENGTH:
            raise ValueError(f"{field_label} 單一項目不可超過 {MAX_KEYWORD_LENGTH} 個字元")
    return values


def parse_limited_keywords_text(value: str, *, field_label: str) -> tuple[str, ...]:
    """解析 keyword textarea，並套用共用 input limits。"""

    text = validate_keyword_text(value, field_label=field_label)
    return validate_keyword_values(parse_keywords_text(text), field_label=field_label)
