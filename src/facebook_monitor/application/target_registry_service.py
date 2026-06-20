"""Target registry application service。

職責：建立、更新、刪除 target descriptor，並維護 target kind/scope uniqueness。
"""

from __future__ import annotations

from dataclasses import replace

from facebook_monitor.application.target_config_service import TargetConfigService
from facebook_monitor.application.target_metadata_policy import normalize_metadata_error
from facebook_monitor.application.target_registry_policy import build_normalized_target_names
from facebook_monitor.application.target_registry_policy import build_target_with_custom_name
from facebook_monitor.application.target_registry_policy import (
    build_target_with_group_cover_image_refresh,
)
from facebook_monitor.application.target_registry_policy import (
    build_target_with_group_metadata_refresh,
)
from facebook_monitor.application.target_registry_policy import (
    build_upserted_comments_target,
)
from facebook_monitor.application.target_registry_policy import (
    build_upserted_group_posts_target,
)
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.application.target_runtime_service import TargetRuntimeService
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.repositories.targets import TargetRepository


class TargetRegistryService:
    """協調 target descriptor repository。"""

    def __init__(
        self,
        *,
        targets: TargetRepository,
        configs: TargetConfigService,
        runtime: TargetRuntimeService,
    ) -> None:
        self.targets = targets
        self.configs = configs
        self.runtime = runtime

    def normalize_target_names(self, target: TargetDescriptor) -> TargetDescriptor:
        """清理已保存 target 名稱並寫回，避免通知數前綴散到各輸出面。"""

        updated_target = build_normalized_target_names(target)
        if updated_target == target:
            return target
        self.targets.save(updated_target)
        return updated_target

    def delete_target(self, target_id: str) -> None:
        """刪除單一 target；target-scoped config 由 SQLite FK 一併清除。"""

        deleted = self.targets.delete(target_id)
        if not deleted:
            raise ValueError(f"Target not found: {target_id}")

    def refresh_target_group_metadata(
        self,
        target_id: str,
        *,
        group_name: str,
        group_cover_image_url: str = "",
        overwrite_name: bool = False,
    ) -> TargetDescriptor:
        """以 metadata refresh 結果補齊 target group name 與封面圖 URL。"""

        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        updated_target = build_target_with_group_metadata_refresh(
            target,
            group_name=group_name,
            group_cover_image_url=group_cover_image_url,
            overwrite_name=overwrite_name,
        )
        if updated_target == target:
            return target
        self.targets.save(updated_target)
        return updated_target

    def refresh_target_group_cover_image(
        self,
        target_id: str,
        group_cover_image_url: str,
    ) -> TargetDescriptor:
        """只刷新 target 封面圖 URL，不改名稱與名稱 metadata 狀態。"""

        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        updated_target = build_target_with_group_cover_image_refresh(
            target,
            group_cover_image_url,
        )
        if updated_target == target:
            return target
        self.targets.save(updated_target)
        return updated_target

    def mark_target_metadata_refresh_pending(self, target_id: str) -> TargetDescriptor:
        """標記 target 正等待 resident worker 補齊 Facebook metadata。"""

        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        updated_target = replace(
            target,
            metadata_status=TargetMetadataStatus.PENDING,
            metadata_error="",
            updated_at=utc_now(),
        )
        self.targets.save(updated_target)
        return updated_target

    def mark_target_metadata_refresh_failed(
        self,
        target_id: str,
        error: str,
    ) -> TargetDescriptor:
        """標記 target metadata 補齊失敗，讓 UI 顯示可手動改名的狀態。"""

        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        updated_target = replace(
            target,
            metadata_status=TargetMetadataStatus.FAILED,
            metadata_error=normalize_metadata_error(error),
            updated_at=utc_now(),
        )
        self.targets.save(updated_target)
        return updated_target

    def update_target_name(self, target_id: str, name: str) -> TargetDescriptor:
        """更新使用者自訂 target 顯示名稱，保留 Facebook metadata group name。"""

        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        updated_target = build_target_with_custom_name(
            target,
            name,
        )
        self.targets.save(updated_target)
        return updated_target

    def upsert_group_posts_target(
        self,
        request: UpsertGroupPostsTargetRequest,
    ) -> TargetDescriptor:
        """建立或更新 group posts target，供 capture script 可重複執行。"""

        existing = self.targets.find_by_kind_scope(TargetKind.POSTS, request.group_id)
        if existing:
            existing = self.normalize_target_names(existing)
        target = build_upserted_group_posts_target(
            existing=existing,
            request=request,
        )

        config = self.configs.build_or_merge_config_for_target(target, request.config)

        self.targets.save(target)
        self.configs.save_config_for_target(target, config)
        self.runtime.ensure_runtime_state(target.id)
        return target

    def upsert_comments_target(
        self,
        request: UpsertCommentsTargetRequest,
    ) -> TargetDescriptor:
        """建立或更新 comments target，打通 group_id / parent_post_id / scope_id。"""

        target_probe = TargetDescriptor.for_comments(
            group_id=request.group_id,
            parent_post_id=request.parent_post_id,
            canonical_url=request.canonical_url,
        )
        existing = self.targets.find_by_kind_scope(TargetKind.COMMENTS, target_probe.scope_id)
        if existing:
            existing = self.normalize_target_names(existing)
        target = build_upserted_comments_target(
            existing=existing,
            request=request,
        )

        config = self.configs.build_or_merge_config_for_target(target, request.config)

        self.targets.save(target)
        self.configs.save_config_for_target(target, config)
        self.runtime.ensure_runtime_state(target.id)
        return target
