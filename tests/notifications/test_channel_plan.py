"""Notification channel plan tests。"""

from __future__ import annotations

from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.notification_channels import get_channel_definition
from facebook_monitor.notifications.channel_plan import build_enabled_channel_plans
from facebook_monitor.notifications.channel_plan import get_channel_endpoint
from facebook_monitor.notifications.channel_plan import is_channel_enabled_by_config


def test_build_enabled_channel_plans_preserves_order_and_channel_semantics() -> None:
    """通道計畫需維持定義順序、endpoint 與 desktop compact message 語義。"""

    config = TargetConfig(
        target_id="target-1",
        enable_desktop_notification=True,
        enable_ntfy=True,
        ntfy_topic="topic",
        enable_discord_notification=True,
        discord_webhook="https://discord.example/webhook",
    )

    plans = build_enabled_channel_plans(config)

    assert [plan.channel for plan in plans] == [
        NotificationChannel.DESKTOP,
        NotificationChannel.NTFY,
        NotificationChannel.DISCORD,
    ]
    assert [plan.endpoint for plan in plans] == [
        "",
        "topic",
        "https://discord.example/webhook",
    ]
    assert [plan.use_compact_message for plan in plans] == [True, False, False]


def test_build_enabled_channel_plans_omits_disabled_channels() -> None:
    """未啟用的通知通道不可建立 outbox/manual delivery 計畫。"""

    config = TargetConfig(
        target_id="target-1",
        enable_ntfy=True,
        ntfy_topic="topic",
    )

    plans = build_enabled_channel_plans(config)

    assert [plan.channel for plan in plans] == [NotificationChannel.NTFY]
    assert plans[0].endpoint == "topic"


def test_enabled_blank_endpoint_still_creates_plan_for_skipped_result() -> None:
    """啟用但 endpoint 空白時仍要留給 dispatch 寫入 skipped 診斷。"""

    config = TargetConfig(target_id="target-1", enable_ntfy=True, ntfy_topic="")

    plans = build_enabled_channel_plans(config)

    assert [plan.channel for plan in plans] == [NotificationChannel.NTFY]
    assert plans[0].endpoint == ""
    assert get_channel_definition(plans[0].channel).skipped_message == "ntfy_skipped"


def test_channel_plan_helpers_read_definition_fields() -> None:
    """共用 helper 必須直接依 channel definition 讀取啟用欄位與 endpoint。"""

    config = TargetConfig(
        target_id="target-1",
        enable_discord_notification=True,
        discord_webhook="https://discord.example/webhook",
    )
    definition = get_channel_definition(NotificationChannel.DISCORD)

    assert is_channel_enabled_by_config(config, definition)
    assert get_channel_endpoint(config, definition) == "https://discord.example/webhook"
