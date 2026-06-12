"""Web UI refresh interval form helpers。"""

from __future__ import annotations

from facebook_monitor.core.refresh_policy import MIN_REFRESH_SECONDS


FIXED_REFRESH_MODE = "fixed"
FLOATING_REFRESH_MODE = "floating"

def normalize_refresh_seconds(value: int, fallback: int) -> int:
    """整理 refresh 秒數，套用至少 5 秒的保護。"""

    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = int(fallback)
    return max(seconds, MIN_REFRESH_SECONDS)


__all__ = [
    "FIXED_REFRESH_MODE",
    "FLOATING_REFRESH_MODE",
    "normalize_refresh_seconds",
]
