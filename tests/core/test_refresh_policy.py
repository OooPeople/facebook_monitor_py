"""Refresh policy pure logic tests。"""

from __future__ import annotations

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.refresh_policy import COMMENTS_EFFECTIVE_REFRESH_FLOOR_REASON
from facebook_monitor.core.refresh_policy import MIN_REFRESH_SECONDS
from facebook_monitor.core.refresh_policy import normalize_refresh_range
from facebook_monitor.core.refresh_policy import resolve_refresh_interval_bounds
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


def test_target_config_default_uses_floating_refresh_mode() -> None:
    """新增 target config 預設不寫固定秒數，讓 scheduler 使用浮動刷新。"""

    config = TargetConfig(target_id="target-1")

    assert config.fixed_refresh_sec is None
    assert config.jitter_enabled


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


def test_comments_fixed_refresh_uses_effective_safety_floor() -> None:
    """comments 固定 requested 值低於安全 floor 時，scheduler 與 UI policy 應一致。"""

    config = TargetConfig(
        target_id="comments-target",
        fixed_refresh_sec=60,
        jitter_enabled=False,
    )
    bounds = resolve_refresh_interval_bounds(
        config=config,
        default_interval_seconds=60,
        target_kind=TargetKind.COMMENTS,
    )

    assert bounds.requested_min_seconds == 60
    assert bounds.requested_max_seconds == 60
    assert bounds.effective_min_seconds == 180
    assert bounds.effective_max_seconds == 180
    assert bounds.adjustment_reason == COMMENTS_EFFECTIVE_REFRESH_FLOOR_REASON
    assert bounds.adjusted
    assert resolve_refresh_interval_seconds(
        config=config,
        default_interval_seconds=60,
        target_id="comments-target",
        target_kind=TargetKind.COMMENTS,
    ) == 180


def test_comments_floating_refresh_floor_preserves_upper_bound() -> None:
    """comments floating 範圍只抬高低於 floor 的部分，不覆蓋較保守上限。"""

    config = TargetConfig(
        target_id="comments-target",
        fixed_refresh_sec=None,
        min_refresh_sec=120,
        max_refresh_sec=240,
        jitter_enabled=True,
    )
    bounds = resolve_refresh_interval_bounds(
        config=config,
        default_interval_seconds=60,
        target_kind=TargetKind.COMMENTS,
    )

    assert (bounds.requested_min_seconds, bounds.requested_max_seconds) == (120, 240)
    assert (bounds.effective_min_seconds, bounds.effective_max_seconds) == (180, 240)
    assert bounds.adjustment_reason == COMMENTS_EFFECTIVE_REFRESH_FLOOR_REASON


def test_posts_refresh_bounds_are_not_affected_by_comments_floor() -> None:
    """comments floor 不可改變 posts requested/effective refresh 契約。"""

    config = TargetConfig(
        target_id="posts-target",
        fixed_refresh_sec=60,
        jitter_enabled=False,
    )
    bounds = resolve_refresh_interval_bounds(
        config=config,
        default_interval_seconds=60,
        target_kind=TargetKind.POSTS,
    )

    assert (bounds.requested_min_seconds, bounds.requested_max_seconds) == (60, 60)
    assert (bounds.effective_min_seconds, bounds.effective_max_seconds) == (60, 60)
    assert bounds.adjustment_reason == ""
    assert not bounds.adjusted
