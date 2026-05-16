"""Target config application service。

職責：管理 target-scoped config、upsert request merge 與通知預設值套用。
"""

from __future__ import annotations

from dataclasses import replace

from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import TargetConfigRequest
from facebook_monitor.application.target_requests import UpdateTargetConfigRequest
from facebook_monitor.application.target_requests import provided_or_default
from facebook_monitor.application.target_requests import provided_or_existing
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import GlobalNotificationSettings
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.scan_limits import clamp_target_post_count
from facebook_monitor.persistence.repositories.target_configs import TargetConfigRepository
from facebook_monitor.persistence.repositories.targets import TargetRepository


class TargetConfigService:
    """協調 target config repository。"""

    def __init__(self, targets: TargetRepository, configs: TargetConfigRepository) -> None:
        self.targets = targets
        self.configs = configs

    def get_config_for_target(self, target: TargetDescriptor) -> TargetConfig:
        """讀取單一 target config，不存在時回傳 target-scoped 預設值。"""

        return self.configs.get_for_target(target) or TargetConfig(target_id=target.id)

    def get_existing_config_for_target(self, target: TargetDescriptor) -> TargetConfig | None:
        """讀取既有 target-scoped config；不存在時不補預設值。"""

        return self.configs.get_for_target(target)

    def save_config_for_target(
        self,
        target: TargetDescriptor,
        config: TargetConfig,
    ) -> TargetConfig:
        """保存單一 target config。"""

        return self.configs.save_for_target(target, config)

    def build_config_from_patch(
        self,
        target_id: str,
        patch: TargetConfigPatch,
    ) -> TargetConfig:
        """將 target config patch 轉成 target-scoped config。"""

        return TargetConfig(
            target_id=target_id,
            include_keywords=provided_or_default(patch.include_keywords, ()),
            exclude_keywords=provided_or_default(
                patch.exclude_keywords,
                PYTHON_TARGET_CONFIG_DEFAULTS.exclude_keywords,
            ),
            exclude_ignore_phrases=provided_or_default(
                patch.exclude_ignore_phrases,
                PYTHON_TARGET_CONFIG_DEFAULTS.exclude_ignore_phrases,
            ),
            fixed_refresh_sec=provided_or_default(
                patch.fixed_refresh_sec,
                PYTHON_TARGET_CONFIG_DEFAULTS.fixed_refresh_sec,
            ),
            min_refresh_sec=provided_or_default(
                patch.min_refresh_sec,
                PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec,
            ),
            max_refresh_sec=provided_or_default(
                patch.max_refresh_sec,
                PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec,
            ),
            jitter_enabled=provided_or_default(
                patch.jitter_enabled,
                PYTHON_TARGET_CONFIG_DEFAULTS.jitter_enabled,
            ),
            max_items_per_scan=clamp_target_post_count(
                provided_or_default(
                    patch.max_items_per_scan,
                    PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan,
                )
            ),
            auto_load_more=provided_or_default(
                patch.auto_load_more,
                PYTHON_TARGET_CONFIG_DEFAULTS.auto_load_more,
            ),
            auto_adjust_sort=provided_or_default(
                patch.auto_adjust_sort,
                PYTHON_TARGET_CONFIG_DEFAULTS.auto_adjust_sort,
            ),
            enable_ntfy=provided_or_default(
                patch.enable_ntfy,
                PYTHON_TARGET_CONFIG_DEFAULTS.enable_ntfy,
            ),
            ntfy_topic=provided_or_default(
                patch.ntfy_topic,
                PYTHON_TARGET_CONFIG_DEFAULTS.ntfy_topic,
            ),
            enable_desktop_notification=provided_or_default(
                patch.enable_desktop_notification,
                PYTHON_TARGET_CONFIG_DEFAULTS.enable_desktop_notification,
            ),
            enable_discord_notification=provided_or_default(
                patch.enable_discord_notification,
                PYTHON_TARGET_CONFIG_DEFAULTS.enable_discord_notification,
            ),
            discord_webhook=provided_or_default(
                patch.discord_webhook,
                PYTHON_TARGET_CONFIG_DEFAULTS.discord_webhook,
            ),
        )

    def build_config_from_request(
        self,
        target_id: str,
        request: TargetConfigRequest,
    ) -> TargetConfig:
        """將 target 建立 request 轉成 target-scoped config。"""

        return self.build_config_from_patch(target_id, request.config)

    def merge_config_patch(
        self,
        existing_config: TargetConfig,
        patch: TargetConfigPatch,
    ) -> TargetConfig:
        """將 config patch 合併到既有 target-scoped config。"""

        return replace(
            existing_config,
            include_keywords=provided_or_existing(
                patch.include_keywords,
                existing_config.include_keywords,
            ),
            exclude_keywords=provided_or_existing(
                patch.exclude_keywords,
                existing_config.exclude_keywords,
            ),
            exclude_ignore_phrases=provided_or_existing(
                patch.exclude_ignore_phrases,
                existing_config.exclude_ignore_phrases,
            ),
            fixed_refresh_sec=provided_or_existing(
                patch.fixed_refresh_sec,
                existing_config.fixed_refresh_sec,
            ),
            min_refresh_sec=provided_or_existing(
                patch.min_refresh_sec,
                existing_config.min_refresh_sec,
            ),
            max_refresh_sec=provided_or_existing(
                patch.max_refresh_sec,
                existing_config.max_refresh_sec,
            ),
            jitter_enabled=provided_or_existing(
                patch.jitter_enabled,
                existing_config.jitter_enabled,
            ),
            max_items_per_scan=clamp_target_post_count(
                provided_or_existing(
                    patch.max_items_per_scan,
                    existing_config.max_items_per_scan,
                )
            ),
            auto_load_more=provided_or_existing(
                patch.auto_load_more,
                existing_config.auto_load_more,
            ),
            auto_adjust_sort=provided_or_existing(
                patch.auto_adjust_sort,
                existing_config.auto_adjust_sort,
            ),
            enable_desktop_notification=provided_or_existing(
                patch.enable_desktop_notification,
                existing_config.enable_desktop_notification,
            ),
            enable_ntfy=provided_or_existing(
                patch.enable_ntfy,
                existing_config.enable_ntfy,
            ),
            ntfy_topic=provided_or_existing(
                patch.ntfy_topic,
                existing_config.ntfy_topic,
            ),
            enable_discord_notification=provided_or_existing(
                patch.enable_discord_notification,
                existing_config.enable_discord_notification,
            ),
            discord_webhook=provided_or_existing(
                patch.discord_webhook,
                existing_config.discord_webhook,
            ),
        )

    def merge_config_request(
        self,
        existing_config: TargetConfig,
        request: TargetConfigRequest,
    ) -> TargetConfig:
        """將 upsert request 合併到既有 target-scoped config。"""

        return self.merge_config_patch(existing_config, request.config)

    def build_or_merge_config_for_target(
        self,
        target: TargetDescriptor,
        patch: TargetConfigPatch,
    ) -> TargetConfig:
        """依 target 既有狀態建立或合併 target-scoped config。"""

        existing_config = self.get_existing_config_for_target(target)
        if existing_config:
            return self.merge_config_patch(existing_config, patch)
        return self.build_config_from_patch(target.id, patch)

    def update_target_config(self, request: UpdateTargetConfigRequest) -> TargetConfig:
        """更新單一 target 監視設定。"""

        target = self.targets.get(request.target_id)
        if target is None:
            raise ValueError(f"Target not found: {request.target_id}")

        existing_config = self.get_config_for_target(target)
        config = self.merge_config_patch(existing_config, request.config)
        self.save_config_for_target(target, config)
        return config

    def apply_global_notification_settings(
        self,
        settings: GlobalNotificationSettings,
    ) -> int:
        """將通知預設值套用到所有既有 target config。"""

        count = 0
        for target in self.targets.list_all():
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
            count += 1
        return count
