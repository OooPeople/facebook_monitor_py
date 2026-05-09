"""Target application request models。

職責：集中 target upsert、config update 與 monitoring status request，
讓 registry/config/runtime/command services 可共享同一組正式輸入模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS


DEFAULT_WEBUI_FIXED_REFRESH_SECONDS = PYTHON_TARGET_CONFIG_DEFAULTS.fixed_refresh_sec


class UnsetConfigValue:
    """標記 upsert request 未提供某個 config 欄位。"""


UNSET_CONFIG_VALUE = UnsetConfigValue()
ConfigFieldValue = TypeVar("ConfigFieldValue")


def provided_or_default(
    value: ConfigFieldValue | UnsetConfigValue,
    default: ConfigFieldValue,
) -> ConfigFieldValue:
    """合併 upsert request：未提供欄位時使用新 target 預設值。"""

    if isinstance(value, UnsetConfigValue):
        return default
    return value


def provided_or_existing(
    value: ConfigFieldValue | UnsetConfigValue,
    existing: ConfigFieldValue,
) -> ConfigFieldValue:
    """合併 upsert request：未提供欄位時保留既有 config。"""

    if isinstance(value, UnsetConfigValue):
        return existing
    return value


@dataclass(frozen=True)
class UpsertGroupPostsTargetRequest:
    """建立或更新 group posts target 所需輸入。"""

    group_id: str
    canonical_url: str
    group_name: str = ""
    name: str = ""
    include_keywords: tuple[str, ...] | UnsetConfigValue = UNSET_CONFIG_VALUE
    exclude_keywords: tuple[str, ...] | UnsetConfigValue = UNSET_CONFIG_VALUE
    fixed_refresh_sec: int | None | UnsetConfigValue = UNSET_CONFIG_VALUE
    min_refresh_sec: int | UnsetConfigValue = UNSET_CONFIG_VALUE
    max_refresh_sec: int | UnsetConfigValue = UNSET_CONFIG_VALUE
    jitter_enabled: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    max_items_per_scan: int | UnsetConfigValue = UNSET_CONFIG_VALUE
    auto_load_more: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    auto_adjust_sort: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    enable_desktop_notification: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    enable_ntfy: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    ntfy_topic: str | UnsetConfigValue = UNSET_CONFIG_VALUE
    enable_discord_notification: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    discord_webhook: str | UnsetConfigValue = UNSET_CONFIG_VALUE


@dataclass(frozen=True)
class UpsertCommentsTargetRequest:
    """建立或更新 group post comments target 所需輸入。"""

    group_id: str
    parent_post_id: str
    canonical_url: str
    group_name: str = ""
    name: str = ""
    include_keywords: tuple[str, ...] | UnsetConfigValue = UNSET_CONFIG_VALUE
    exclude_keywords: tuple[str, ...] | UnsetConfigValue = UNSET_CONFIG_VALUE
    fixed_refresh_sec: int | None | UnsetConfigValue = UNSET_CONFIG_VALUE
    min_refresh_sec: int | UnsetConfigValue = UNSET_CONFIG_VALUE
    max_refresh_sec: int | UnsetConfigValue = UNSET_CONFIG_VALUE
    jitter_enabled: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    max_items_per_scan: int | UnsetConfigValue = UNSET_CONFIG_VALUE
    auto_load_more: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    auto_adjust_sort: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    enable_desktop_notification: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    enable_ntfy: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    ntfy_topic: str | UnsetConfigValue = UNSET_CONFIG_VALUE
    enable_discord_notification: bool | UnsetConfigValue = UNSET_CONFIG_VALUE
    discord_webhook: str | UnsetConfigValue = UNSET_CONFIG_VALUE


TargetConfigRequest = UpsertGroupPostsTargetRequest | UpsertCommentsTargetRequest


@dataclass(frozen=True)
class UpdateTargetConfigRequest:
    """更新 target 所屬 group config 所需輸入。"""

    target_id: str
    include_keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    fixed_refresh_sec: int | None
    max_items_per_scan: int
    auto_load_more: bool
    auto_adjust_sort: bool
    min_refresh_sec: int = PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec
    max_refresh_sec: int = PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec
    jitter_enabled: bool = PYTHON_TARGET_CONFIG_DEFAULTS.jitter_enabled
    enable_ntfy: bool = False
    ntfy_topic: str = ""
    enable_desktop_notification: bool | None = None
    enable_discord_notification: bool | None = None
    discord_webhook: str | None = None


@dataclass(frozen=True)
class UpdateTargetStatusRequest:
    """更新 target 啟停狀態所需輸入。"""

    target_id: str
    enabled: bool
    paused: bool
