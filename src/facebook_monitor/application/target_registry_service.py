"""Target registry application service。

職責：建立、更新、刪除 target descriptor，並維護 target kind/scope uniqueness。
"""

from __future__ import annotations

from dataclasses import replace

from facebook_monitor.application.target_config_service import TargetConfigService
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.application.target_runtime_service import TargetRuntimeService
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import is_generated_group_comments_name
from facebook_monitor.core.models import is_generated_group_posts_name
from facebook_monitor.core.models import utc_now
from facebook_monitor.facebook.route_detection import clean_facebook_page_title
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

        normalized_name = clean_facebook_group_name(target.name)
        normalized_group_name = clean_facebook_group_name(target.group_name)
        if normalized_name == target.name and normalized_group_name == target.group_name:
            return target
        updated_target = replace(
            target,
            name=normalized_name,
            group_name=normalized_group_name,
            updated_at=utc_now(),
        )
        self.targets.save(updated_target)
        return updated_target

    def delete_target(self, target_id: str) -> None:
        """刪除單一 target 與其關聯設定，不影響其他 target。"""

        deleted = self.targets.delete(target_id)
        if not deleted:
            raise ValueError(f"Target not found: {target_id}")

    def upsert_group_posts_target(
        self,
        request: UpsertGroupPostsTargetRequest,
    ) -> TargetDescriptor:
        """建立或更新 group posts target，供 capture script 可重複執行。"""

        existing = self.targets.find_by_kind_scope(TargetKind.POSTS, request.group_id)
        request_name = clean_facebook_group_name(request.name)
        request_group_name = clean_facebook_group_name(request.group_name)
        if existing:
            existing = self.normalize_target_names(existing)
            existing_name = clean_facebook_group_name(existing.name)
            existing_group_name = clean_facebook_group_name(existing.group_name)
            next_name = request_name or existing_name
            if (
                not request_name
                and request_group_name
                and is_generated_group_posts_name(existing.name, existing.group_id)
            ):
                next_name = request_group_name
            target = replace(
                existing,
                name=next_name,
                group_name=request_group_name or existing_group_name,
                canonical_url=request.canonical_url,
                updated_at=utc_now(),
            )
        else:
            target = TargetDescriptor.for_group_posts(
                group_id=request.group_id,
                canonical_url=request.canonical_url,
                name=request_name,
                group_name=request_group_name,
            )

        existing_config = self.configs.configs.get_for_target(target)
        if existing_config:
            config = self.configs.merge_config_request(existing_config, request)
        else:
            config = self.configs.build_config_from_request(target.group_id, request)

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
        request_name = clean_facebook_group_name(request.name)
        request_group_name = clean_facebook_group_name(request.group_name)
        if existing:
            existing = self.normalize_target_names(existing)
            existing_name = clean_facebook_group_name(existing.name)
            existing_group_name = clean_facebook_group_name(existing.group_name)
            next_name = request_name or existing_name
            if (
                not request_name
                and request_group_name
                and is_generated_group_comments_name(
                    existing.name,
                    existing.group_id,
                    existing.parent_post_id,
                )
            ):
                next_name = request_group_name
            target = replace(
                existing,
                name=next_name,
                group_name=request_group_name or existing_group_name,
                canonical_url=request.canonical_url,
                updated_at=utc_now(),
            )
        else:
            target = TargetDescriptor.for_comments(
                group_id=request.group_id,
                parent_post_id=request.parent_post_id,
                canonical_url=request.canonical_url,
                name=request_name,
                group_name=request_group_name,
            )

        existing_config = self.configs.configs.get_for_target(target)
        if existing_config:
            config = self.configs.merge_config_request(existing_config, request)
        else:
            config = self.configs.build_config_from_request(target.group_id, request)

        self.targets.save(target)
        self.configs.save_config_for_target(target, config)
        self.runtime.ensure_runtime_state(target.id)
        return target


def clean_facebook_group_name(value: str) -> str:
    """清理準備保存的 Facebook 社團名稱，對齊 userscript 取得名稱階段。"""

    return clean_facebook_page_title(value)
