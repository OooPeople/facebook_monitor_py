"""Dashboard target settings presenter 測試。"""

from __future__ import annotations

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetKind
from facebook_monitor.webapp.dashboard_settings_presenters import TargetSettingsPresenter


def test_comments_refresh_summary_shows_requested_effective_and_floor_reason() -> None:
    """comments floating 設定應同時呈現 requested/effective 與安全下限理由。"""

    presenter = TargetSettingsPresenter(
        config=TargetConfig(
            target_id="comments-target",
            fixed_refresh_sec=None,
            min_refresh_sec=120,
            max_refresh_sec=240,
            jitter_enabled=True,
        ),
        target_kind=TargetKind.COMMENTS,
    )
    refresh_line = presenter.settings_summary.lines[0]

    assert presenter.requested_refresh_interval_label == "浮動 120-240 秒"
    assert presenter.effective_refresh_interval_label == "浮動 180-240 秒"
    assert presenter.effective_refresh_reason_label == "套用留言模式安全下限 180 秒"
    assert refresh_line.value == "要求 浮動 120-240 秒 · 有效 浮動 180-240 秒"
    assert refresh_line.details == ("原因：套用留言模式安全下限 180 秒",)


def test_comments_refresh_summary_explains_when_requested_range_already_safe() -> None:
    """comments requested 已高於 floor 時仍清楚顯示相同 effective 與理由。"""

    presenter = TargetSettingsPresenter(
        config=TargetConfig(
            target_id="comments-target",
            fixed_refresh_sec=None,
            min_refresh_sec=300,
            max_refresh_sec=420,
            jitter_enabled=True,
        ),
        target_kind=TargetKind.COMMENTS,
    )

    assert presenter.requested_refresh_interval_label == "浮動 300-420 秒"
    assert presenter.effective_refresh_interval_label == "浮動 300-420 秒"
    assert (
        presenter.effective_refresh_reason_label
        == "設定已符合留言模式安全下限 180 秒"
    )
    assert (
        presenter.refresh_summary_label
        == "要求 浮動 300-420 秒 · 有效 浮動 300-420 秒"
    )


def test_posts_refresh_summary_keeps_existing_single_interval_label() -> None:
    """comments floor UI 不增加 posts 設定摘要噪音。"""

    presenter = TargetSettingsPresenter(
        config=TargetConfig(
            target_id="posts-target",
            fixed_refresh_sec=60,
            jitter_enabled=False,
        ),
        target_kind=TargetKind.POSTS,
    )
    refresh_line = presenter.settings_summary.lines[0]

    assert presenter.requested_refresh_interval_label == "固定 60 秒"
    assert presenter.effective_refresh_interval_label == "固定 60 秒"
    assert presenter.effective_refresh_reason_label == ""
    assert refresh_line.value == "固定 60 秒"
    assert refresh_line.details == ()
