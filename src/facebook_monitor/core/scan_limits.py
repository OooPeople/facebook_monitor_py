"""Target scan limit policy shared by config and Facebook collection helpers."""

from __future__ import annotations

MIN_TARGET_POSTS = 1
MAX_TARGET_POSTS = 10


def clamp_target_post_count(value: int | float | str | None, default: int = 5) -> int:
    """限制單輪目標項目數在安全範圍內。"""

    try:
        numeric_value = int(float(value)) if value is not None else int(default)
    except (TypeError, ValueError):
        numeric_value = int(default)
    return min(MAX_TARGET_POSTS, max(MIN_TARGET_POSTS, numeric_value))
