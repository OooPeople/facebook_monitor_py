"""掃描刷新週期計算。

職責：集中處理固定秒數與 jitter 範圍，讓 one-shot scheduler 與 resident
worker 使用同一套到期判斷。
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from facebook_monitor.core.models import TargetConfig


MIN_REFRESH_SECONDS = 5


def _to_float(value: object, fallback: float) -> float:
    """安全轉換 refresh 秒數，無法轉換時回到 fallback。"""

    if not isinstance(value, str | bytes | bytearray | int | float):
        return float(fallback)
    try:
        return float(value)
    except ValueError:
        return float(fallback)


def clamp_refresh_seconds(value: object, fallback: float) -> float:
    """將 refresh 秒數限制在最低安全值以上。"""

    return max(_to_float(value, fallback), MIN_REFRESH_SECONDS)


def normalize_refresh_range(config: TargetConfig, default_interval_seconds: float) -> tuple[int, int]:
    """整理 jitter 使用的最小與最大秒數範圍。"""

    min_seconds = int(clamp_refresh_seconds(config.min_refresh_sec, default_interval_seconds))
    max_seconds = int(clamp_refresh_seconds(config.max_refresh_sec, default_interval_seconds))
    return min(min_seconds, max_seconds), max(min_seconds, max_seconds)


def choose_deterministic_jitter_seconds(
    *,
    target_id: str,
    latest_finished_at: datetime | None,
    min_seconds: int,
    max_seconds: int,
) -> int:
    """用 target 與上一輪完成時間穩定選出 jitter 秒數。"""

    if max_seconds <= min_seconds:
        return min_seconds
    seed = f"{target_id}|{latest_finished_at.isoformat() if latest_finished_at else ''}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    offset = int(digest[:8], 16) % (max_seconds - min_seconds + 1)
    return min_seconds + offset


def resolve_refresh_interval_seconds(
    *,
    config: TargetConfig | None,
    default_interval_seconds: float,
    target_id: str = "",
    latest_finished_at: datetime | None = None,
) -> float:
    """依監視設定回傳本輪到期判斷使用的 refresh 秒數。"""

    if config is None:
        return clamp_refresh_seconds(default_interval_seconds, default_interval_seconds)
    if config.fixed_refresh_sec:
        return clamp_refresh_seconds(config.fixed_refresh_sec, default_interval_seconds)
    if not config.jitter_enabled:
        return clamp_refresh_seconds(default_interval_seconds, default_interval_seconds)

    min_seconds, max_seconds = normalize_refresh_range(config, default_interval_seconds)
    return float(
        choose_deterministic_jitter_seconds(
            target_id=target_id,
            latest_finished_at=latest_finished_at,
            min_seconds=min_seconds,
            max_seconds=max_seconds,
        )
    )
