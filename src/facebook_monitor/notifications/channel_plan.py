"""Notification channel planning helpers。

職責：依 TargetConfig 與 channel definition 建立啟用通道計畫，讓正式
outbox 與手動測試通知共享通道啟用、endpoint 與 desktop 短訊息語義。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.notification_channels import NOTIFICATION_CHANNEL_DEFINITIONS
from facebook_monitor.core.notification_channels import NotificationChannelDefinition
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import TargetConfig


@dataclass(frozen=True)
class NotificationChannelPlan:
    """保存單一已啟用通知通道的 delivery 計畫。"""

    definition: NotificationChannelDefinition
    endpoint: str = ""
    use_compact_message: bool = False

    @property
    def channel(self) -> NotificationChannel:
        """回傳通知通道 enum。"""

        return self.definition.channel


def is_channel_enabled_by_config(
    config: TargetConfig,
    definition: NotificationChannelDefinition,
) -> bool:
    """判斷 target config 是否啟用指定通知通道。"""

    return bool(getattr(config, definition.enabled_field))


def get_channel_endpoint(
    config: TargetConfig,
    definition: NotificationChannelDefinition,
) -> str:
    """讀取指定通道的 endpoint 欄位；desktop 等無 endpoint 通道回空字串。"""

    if not definition.endpoint_field:
        return ""
    return str(getattr(config, definition.endpoint_field, "") or "")


def build_enabled_channel_plans(config: TargetConfig) -> tuple[NotificationChannelPlan, ...]:
    """依 target config 建立已啟用通知通道計畫。"""

    plans: list[NotificationChannelPlan] = []
    for definition in NOTIFICATION_CHANNEL_DEFINITIONS:
        if not is_channel_enabled_by_config(config, definition):
            continue
        plans.append(
            NotificationChannelPlan(
                definition=definition,
                endpoint=get_channel_endpoint(config, definition),
                use_compact_message=definition.use_compact_message,
            )
        )
    return tuple(plans)
