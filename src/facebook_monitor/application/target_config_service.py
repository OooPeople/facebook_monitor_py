"""Target config application service。

職責：管理 target-scoped config 與 upsert request merge。
"""

from __future__ import annotations

from facebook_monitor.application.target_config_merge import build_target_config_from_patch
from facebook_monitor.application.target_config_merge import merge_target_config_patch
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import TargetConfigRequest
from facebook_monitor.application.target_requests import UpdateTargetConfigRequest
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
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

        return build_target_config_from_patch(target_id, patch)

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

        return merge_target_config_patch(existing_config, patch)

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
