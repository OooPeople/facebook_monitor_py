"""Sidebar layout models。

職責：保存 Web UI sidebar 分組、target 顯示位置與群組設定模板資料。
這些 model 只描述 UI layout 與批次套用模板，不是 target config owner。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import new_id
from facebook_monitor.core.models import utc_now


@dataclass(frozen=True)
class SidebarGroup:
    """保存使用者建立的 sidebar UI 分組。"""

    id: str
    name: str
    sort_order: int
    collapsed: bool = False
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @classmethod
    def create(cls, *, name: str, sort_order: int) -> "SidebarGroup":
        """建立新的 sidebar group。"""

        return cls(
            id=new_id(),
            name=name.strip(),
            sort_order=sort_order,
        )


@dataclass(frozen=True)
class SidebarTargetPlacement:
    """保存 target 在 sidebar UI 中的分組與排序位置。"""

    target_id: str
    sidebar_group_id: str | None
    sort_order: int
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class SidebarGroupConfigTemplate:
    """保存 sidebar group 的設定模板；只有明確套用時才複製到 target config。"""

    sidebar_group_id: str
    include_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    exclude_ignore_phrases: tuple[str, ...] = ()
    min_refresh_sec: int = PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec
    max_refresh_sec: int = PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec
    jitter_enabled: bool = PYTHON_TARGET_CONFIG_DEFAULTS.jitter_enabled
    fixed_refresh_sec: int | None = PYTHON_TARGET_CONFIG_DEFAULTS.fixed_refresh_sec
    max_items_per_scan: int = PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan
    auto_load_more: bool = PYTHON_TARGET_CONFIG_DEFAULTS.auto_load_more
    auto_adjust_sort: bool = PYTHON_TARGET_CONFIG_DEFAULTS.auto_adjust_sort
    enable_desktop_notification: bool = PYTHON_TARGET_CONFIG_DEFAULTS.enable_desktop_notification
    enable_ntfy: bool = PYTHON_TARGET_CONFIG_DEFAULTS.enable_ntfy
    ntfy_topic: str = PYTHON_TARGET_CONFIG_DEFAULTS.ntfy_topic
    enable_discord_notification: bool = PYTHON_TARGET_CONFIG_DEFAULTS.enable_discord_notification
    discord_webhook: str = PYTHON_TARGET_CONFIG_DEFAULTS.discord_webhook
    updated_at: datetime = field(default_factory=utc_now)

    def to_target_config(self, *, target_id: str) -> TargetConfig:
        """將模板內容複製成 target-scoped config。"""

        return TargetConfig(
            target_id=target_id,
            include_keywords=self.include_keywords,
            exclude_keywords=self.exclude_keywords,
            exclude_ignore_phrases=self.exclude_ignore_phrases,
            min_refresh_sec=self.min_refresh_sec,
            max_refresh_sec=self.max_refresh_sec,
            jitter_enabled=self.jitter_enabled,
            fixed_refresh_sec=self.fixed_refresh_sec,
            max_items_per_scan=self.max_items_per_scan,
            auto_load_more=self.auto_load_more,
            auto_adjust_sort=self.auto_adjust_sort,
            enable_desktop_notification=self.enable_desktop_notification,
            enable_ntfy=self.enable_ntfy,
            ntfy_topic=self.ntfy_topic,
            enable_discord_notification=self.enable_discord_notification,
            discord_webhook=self.discord_webhook,
        )
