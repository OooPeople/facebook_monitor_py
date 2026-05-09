"""Target config application service。

職責：管理 group-scoped target config、upsert request merge 與通知預設值套用。
"""

from __future__ import annotations

from dataclasses import replace

from facebook_monitor.application.target_requests import TargetConfigRequest
from facebook_monitor.application.target_requests import UpdateTargetConfigRequest
from facebook_monitor.application.target_requests import provided_or_default
from facebook_monitor.application.target_requests import provided_or_existing
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import GlobalNotificationSettings
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.facebook.collection_policy import clamp_target_post_count
from facebook_monitor.persistence.repositories.target_configs import TargetConfigRepository
from facebook_monitor.persistence.repositories.targets import TargetRepository


class TargetConfigService:
    """協調 target group config repository。"""

    def __init__(self, targets: TargetRepository, configs: TargetConfigRepository) -> None:
        self.targets = targets
        self.configs = configs

    def get_config_for_target(self, target: TargetDescriptor) -> TargetConfig:
        """讀取 target 所屬社團的 group-scoped config。"""

        return self.configs.get_for_target(target) or TargetConfig(group_id=target.group_id)

    def save_config_for_target(
        self,
        target: TargetDescriptor,
        config: TargetConfig,
    ) -> TargetConfig:
        """保存 target 所屬社團的 group-scoped config。"""

        return self.configs.save_for_target(target, config)

    def build_config_from_request(
        self,
        group_id: str,
        request: TargetConfigRequest,
    ) -> TargetConfig:
        """將 target 建立 request 轉成 group-scoped config。"""

        return TargetConfig(
            group_id=group_id,
            include_keywords=provided_or_default(request.include_keywords, ()),
            exclude_keywords=provided_or_default(request.exclude_keywords, ()),
            fixed_refresh_sec=provided_or_default(
                request.fixed_refresh_sec,
                PYTHON_TARGET_CONFIG_DEFAULTS.fixed_refresh_sec,
            ),
            min_refresh_sec=provided_or_default(
                request.min_refresh_sec,
                PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec,
            ),
            max_refresh_sec=provided_or_default(
                request.max_refresh_sec,
                PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec,
            ),
            jitter_enabled=provided_or_default(
                request.jitter_enabled,
                PYTHON_TARGET_CONFIG_DEFAULTS.jitter_enabled,
            ),
            max_items_per_scan=clamp_target_post_count(
                provided_or_default(
                    request.max_items_per_scan,
                    PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan,
                )
            ),
            auto_load_more=provided_or_default(
                request.auto_load_more,
                PYTHON_TARGET_CONFIG_DEFAULTS.auto_load_more,
            ),
            auto_adjust_sort=provided_or_default(
                request.auto_adjust_sort,
                PYTHON_TARGET_CONFIG_DEFAULTS.auto_adjust_sort,
            ),
            enable_ntfy=provided_or_default(
                request.enable_ntfy,
                PYTHON_TARGET_CONFIG_DEFAULTS.enable_ntfy,
            ),
            ntfy_topic=provided_or_default(
                request.ntfy_topic,
                PYTHON_TARGET_CONFIG_DEFAULTS.ntfy_topic,
            ),
            enable_desktop_notification=provided_or_default(
                request.enable_desktop_notification,
                PYTHON_TARGET_CONFIG_DEFAULTS.enable_desktop_notification,
            ),
            enable_discord_notification=provided_or_default(
                request.enable_discord_notification,
                PYTHON_TARGET_CONFIG_DEFAULTS.enable_discord_notification,
            ),
            discord_webhook=provided_or_default(
                request.discord_webhook,
                PYTHON_TARGET_CONFIG_DEFAULTS.discord_webhook,
            ),
        )

    def merge_config_request(
        self,
        existing_config: TargetConfig,
        request: TargetConfigRequest,
    ) -> TargetConfig:
        """將 upsert request 合併到既有 group-scoped config。"""

        return replace(
            existing_config,
            include_keywords=provided_or_existing(
                request.include_keywords,
                existing_config.include_keywords,
            ),
            exclude_keywords=provided_or_existing(
                request.exclude_keywords,
                existing_config.exclude_keywords,
            ),
            fixed_refresh_sec=provided_or_existing(
                request.fixed_refresh_sec,
                existing_config.fixed_refresh_sec,
            ),
            min_refresh_sec=provided_or_existing(
                request.min_refresh_sec,
                existing_config.min_refresh_sec,
            ),
            max_refresh_sec=provided_or_existing(
                request.max_refresh_sec,
                existing_config.max_refresh_sec,
            ),
            jitter_enabled=provided_or_existing(
                request.jitter_enabled,
                existing_config.jitter_enabled,
            ),
            max_items_per_scan=clamp_target_post_count(
                provided_or_existing(
                    request.max_items_per_scan,
                    existing_config.max_items_per_scan,
                )
            ),
            auto_load_more=provided_or_existing(
                request.auto_load_more,
                existing_config.auto_load_more,
            ),
            auto_adjust_sort=provided_or_existing(
                request.auto_adjust_sort,
                existing_config.auto_adjust_sort,
            ),
            enable_desktop_notification=provided_or_existing(
                request.enable_desktop_notification,
                existing_config.enable_desktop_notification,
            ),
            enable_ntfy=provided_or_existing(
                request.enable_ntfy,
                existing_config.enable_ntfy,
            ),
            ntfy_topic=provided_or_existing(
                request.ntfy_topic,
                existing_config.ntfy_topic,
            ),
            enable_discord_notification=provided_or_existing(
                request.enable_discord_notification,
                existing_config.enable_discord_notification,
            ),
            discord_webhook=provided_or_existing(
                request.discord_webhook,
                existing_config.discord_webhook,
            ),
        )

    def update_target_config(self, request: UpdateTargetConfigRequest) -> TargetConfig:
        """更新 target 所屬社團監視設定。"""

        target = self.targets.get(request.target_id)
        if target is None:
            raise ValueError(f"Target not found: {request.target_id}")

        existing_config = self.get_config_for_target(target)
        config = replace(
            existing_config,
            include_keywords=request.include_keywords,
            exclude_keywords=request.exclude_keywords,
            fixed_refresh_sec=request.fixed_refresh_sec,
            min_refresh_sec=request.min_refresh_sec,
            max_refresh_sec=request.max_refresh_sec,
            jitter_enabled=request.jitter_enabled,
            max_items_per_scan=clamp_target_post_count(request.max_items_per_scan),
            auto_load_more=request.auto_load_more,
            auto_adjust_sort=request.auto_adjust_sort,
            enable_ntfy=request.enable_ntfy,
            ntfy_topic=request.ntfy_topic,
            enable_desktop_notification=(
                existing_config.enable_desktop_notification
                if request.enable_desktop_notification is None
                else request.enable_desktop_notification
            ),
            enable_discord_notification=(
                existing_config.enable_discord_notification
                if request.enable_discord_notification is None
                else request.enable_discord_notification
            ),
            discord_webhook=(
                existing_config.discord_webhook
                if request.discord_webhook is None
                else request.discord_webhook
            ),
        )
        self.save_config_for_target(target, config)
        return config

    def apply_global_notification_settings(
        self,
        settings: GlobalNotificationSettings,
    ) -> int:
        """將通知預設值套用到所有既有 group config。"""

        count = 0
        applied_group_ids: set[str] = set()
        for target in self.targets.list_all():
            if target.group_id in applied_group_ids:
                continue
            current = self.get_config_for_target(target)
            self.save_config_for_target(
                target,
                replace(
                    current,
                    enable_desktop_notification=settings.enable_desktop_notification,
                    enable_ntfy=settings.enable_ntfy,
                    ntfy_topic=settings.ntfy_topic,
                    enable_discord_notification=settings.enable_discord_notification,
                    discord_webhook=settings.discord_webhook,
                ),
            )
            applied_group_ids.add(target.group_id)
            count += 1
        return count
