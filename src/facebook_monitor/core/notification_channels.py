"""Notification channel metadata 與設定欄位規則。

職責：集中通知通道順序、表單欄位、endpoint 欄位、UI label 與設定複製規則，
避免 runtime、Web UI 與 persistence 各自維護一套通道欄位。
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from typing import Any
from typing import Callable
from typing import cast
from typing import TypeVar

from facebook_monitor.core.models import NotificationChannel


@dataclass(frozen=True)
class NotificationChannelDefinition:
    """描述單一通知通道的設定欄位、endpoint 與顯示 metadata。"""

    channel: NotificationChannel
    enabled_field: str
    endpoint_field: str = ""
    skipped_message: str = ""
    ui_label: str = ""
    use_compact_message: bool = False


NOTIFICATION_CHANNEL_DEFINITIONS: tuple[NotificationChannelDefinition, ...] = (
    NotificationChannelDefinition(
        channel=NotificationChannel.DESKTOP,
        enabled_field="enable_desktop_notification",
        skipped_message="desktop_skipped",
        ui_label="桌面",
        use_compact_message=True,
    ),
    NotificationChannelDefinition(
        channel=NotificationChannel.NTFY,
        enabled_field="enable_ntfy",
        endpoint_field="ntfy_topic",
        skipped_message="ntfy_skipped",
        ui_label="ntfy",
    ),
    NotificationChannelDefinition(
        channel=NotificationChannel.DISCORD,
        enabled_field="enable_discord_notification",
        endpoint_field="discord_webhook",
        skipped_message="discord_skipped",
        ui_label="Discord",
    ),
)
NOTIFICATION_CHANNEL_ORDER = {
    definition.channel: index
    for index, definition in enumerate(NOTIFICATION_CHANNEL_DEFINITIONS)
}

NOTIFICATION_SETTING_FIELDS = tuple(
    field
    for definition in NOTIFICATION_CHANNEL_DEFINITIONS
    for field in (definition.enabled_field, definition.endpoint_field)
    if field
)
NOTIFICATION_ENDPOINT_FIELDS = tuple(
    definition.endpoint_field
    for definition in NOTIFICATION_CHANNEL_DEFINITIONS
    if definition.endpoint_field
)

T = TypeVar("T")


def get_channel_definition(channel: NotificationChannel) -> NotificationChannelDefinition:
    """依 channel 取得定義。"""

    for definition in NOTIFICATION_CHANNEL_DEFINITIONS:
        if definition.channel == channel:
            return definition
    raise ValueError(f"Unsupported notification channel: {channel}")


def format_notification_channel_label(channel: NotificationChannel) -> str:
    """回傳通知通道 UI label。"""

    return get_channel_definition(channel).ui_label or channel.value


def notification_channel_sort_key(channel: NotificationChannel) -> int:
    """回傳通道顯示排序值，供 UI 與 diagnostics 共用。"""

    return NOTIFICATION_CHANNEL_ORDER.get(channel, len(NOTIFICATION_CHANNEL_ORDER))


def notification_settings_kwargs(source: object) -> dict[str, object]:
    """從任意設定物件取出 notification 欄位 kwargs。"""

    return {
        field: getattr(source, field)
        for field in NOTIFICATION_SETTING_FIELDS
    }


def copy_notification_settings(target: T, source: object) -> T:
    """將 source 的 notification 設定欄位複製到 target。"""

    return cast(T, replace(cast(Any, target), **notification_settings_kwargs(source)))


def transform_notification_endpoints(
    value: T,
    transform: Callable[[str], str],
) -> T:
    """套用 endpoint 欄位轉換，供 repository 加解密共用。"""

    return cast(
        T,
        replace(
            cast(Any, value),
            **{
                field: transform(str(getattr(value, field, "") or ""))
                for field in NOTIFICATION_ENDPOINT_FIELDS
            },
        ),
    )
