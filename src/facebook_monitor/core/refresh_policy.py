"""掃描刷新週期計算。

職責：集中處理固定秒數與 jitter 範圍，讓 one-shot scheduler 與 resident
worker 使用同一套到期判斷。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from facebook_monitor.core.defaults import PYTHON_FACEBOOK_AUTOMATION_DEFAULTS
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetKind


MIN_REFRESH_SECONDS = 5
COMMENTS_EFFECTIVE_REFRESH_FLOOR_REASON = "comments_effective_refresh_floor"


@dataclass(frozen=True)
class RefreshIntervalBounds:
    """保存使用者要求與 target policy 實際採用的 refresh 範圍。"""

    requested_min_seconds: int
    requested_max_seconds: int
    effective_min_seconds: int
    effective_max_seconds: int
    adjustment_reason: str = ""

    @property
    def adjusted(self) -> bool:
        """回傳 effective 範圍是否因安全 policy 高於 requested。"""

        return (
            self.requested_min_seconds != self.effective_min_seconds
            or self.requested_max_seconds != self.effective_max_seconds
        )


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


def resolve_refresh_interval_bounds(
    *,
    config: TargetConfig | None,
    default_interval_seconds: float,
    target_kind: TargetKind | None = None,
) -> RefreshIntervalBounds:
    """回傳 requested/effective refresh 範圍與安全調整原因。"""

    if config is None:
        requested_min = requested_max = int(
            clamp_refresh_seconds(default_interval_seconds, default_interval_seconds)
        )
    elif config.fixed_refresh_sec:
        requested_min = requested_max = int(
            clamp_refresh_seconds(config.fixed_refresh_sec, default_interval_seconds)
        )
    elif not config.jitter_enabled:
        requested_min = requested_max = int(
            clamp_refresh_seconds(default_interval_seconds, default_interval_seconds)
        )
    else:
        requested_min, requested_max = normalize_refresh_range(
            config,
            default_interval_seconds,
        )

    effective_min = requested_min
    effective_max = requested_max
    adjustment_reason = ""
    if target_kind == TargetKind.COMMENTS:
        floor_seconds = int(
            PYTHON_FACEBOOK_AUTOMATION_DEFAULTS.comments_effective_refresh_floor_seconds
        )
        effective_min = max(requested_min, floor_seconds)
        effective_max = max(requested_max, floor_seconds)
        if effective_min != requested_min or effective_max != requested_max:
            adjustment_reason = COMMENTS_EFFECTIVE_REFRESH_FLOOR_REASON
    return RefreshIntervalBounds(
        requested_min_seconds=requested_min,
        requested_max_seconds=requested_max,
        effective_min_seconds=effective_min,
        effective_max_seconds=effective_max,
        adjustment_reason=adjustment_reason,
    )


def resolve_refresh_interval_seconds(
    *,
    config: TargetConfig | None,
    default_interval_seconds: float,
    target_id: str = "",
    latest_finished_at: datetime | None = None,
    target_kind: TargetKind | None = None,
) -> float:
    """依監視設定回傳本輪到期判斷使用的 refresh 秒數。"""

    if config is None:
        resolved = clamp_refresh_seconds(default_interval_seconds, default_interval_seconds)
    elif config.fixed_refresh_sec:
        resolved = clamp_refresh_seconds(config.fixed_refresh_sec, default_interval_seconds)
    elif not config.jitter_enabled:
        resolved = clamp_refresh_seconds(default_interval_seconds, default_interval_seconds)
    else:
        min_seconds, max_seconds = normalize_refresh_range(config, default_interval_seconds)
        resolved = float(
            choose_deterministic_jitter_seconds(
                target_id=target_id,
                latest_finished_at=latest_finished_at,
                min_seconds=min_seconds,
                max_seconds=max_seconds,
            )
        )
    if target_kind == TargetKind.COMMENTS:
        return max(
            resolved,
            float(
                PYTHON_FACEBOOK_AUTOMATION_DEFAULTS.comments_effective_refresh_floor_seconds
            ),
        )
    return resolved
