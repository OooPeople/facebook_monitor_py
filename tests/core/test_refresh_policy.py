"""Refresh policy pure logic tests。"""

from __future__ import annotations

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.refresh_policy import MIN_REFRESH_SECONDS
from facebook_monitor.core.refresh_policy import normalize_refresh_range
from facebook_monitor.core.refresh_policy import resolve_refresh_interval_seconds


def test_resolve_refresh_interval_prefers_fixed_seconds() -> None:
    """Python 版目前固定秒數設定優先於 jitter 範圍。"""

    config = TargetConfig(
        target_id="target-1",
        fixed_refresh_sec=30,
        min_refresh_sec=300,
        max_refresh_sec=600,
        jitter_enabled=True,
    )

    assert resolve_refresh_interval_seconds(
        config=config,
        default_interval_seconds=60,
        target_id="target-1",
    ) == 30


def test_resolve_refresh_interval_uses_deterministic_jitter_range() -> None:
    """未設定固定秒數時，jitter 會在 min/max 之間穩定取值。"""

    latest_finished_at = utc_now()
    config = TargetConfig(
        target_id="target-1",
        fixed_refresh_sec=None,
        min_refresh_sec=25,
        max_refresh_sec=35,
        jitter_enabled=True,
    )

    first = resolve_refresh_interval_seconds(
        config=config,
        default_interval_seconds=60,
        target_id="target-1",
        latest_finished_at=latest_finished_at,
    )
    second = resolve_refresh_interval_seconds(
        config=config,
        default_interval_seconds=60,
        target_id="target-1",
        latest_finished_at=latest_finished_at,
    )

    assert 25 <= first <= 35
    assert first == second


def test_normalize_refresh_range_swaps_bounds_and_clamps_minimum() -> None:
    """jitter 範圍會自動校正大小順序與最低秒數。"""

    config = TargetConfig(
        target_id="target-1",
        fixed_refresh_sec=None,
        min_refresh_sec=2,
        max_refresh_sec=1,
    )

    assert normalize_refresh_range(config, default_interval_seconds=60) == (
        MIN_REFRESH_SECONDS,
        MIN_REFRESH_SECONDS,
    )
