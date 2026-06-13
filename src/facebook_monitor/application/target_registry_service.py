"""Target registry application service。

職責：建立、更新、刪除 target descriptor，並維護 target kind/scope uniqueness。
"""

from __future__ import annotations

from dataclasses import replace

from facebook_monitor.application.target_config_service import TargetConfigService
from facebook_monitor.application.target_display import clean_target_display_name
from facebook_monitor.application.target_display import is_generated_group_comments_display_name
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.application.target_runtime_service import TargetRuntimeService
from facebook_monitor.core.external_url_policy import sanitize_facebook_group_cover_image_url
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import generated_group_comments_display_name
from facebook_monitor.core.models import is_generated_group_comments_name
from facebook_monitor.core.models import is_generated_group_posts_name
from facebook_monitor.core.models import utc_now
from facebook_monitor.facebook.group_metadata_validation import has_polluted_group_cover_image_url
from facebook_monitor.facebook.group_metadata_validation import is_invalid_facebook_group_name
from facebook_monitor.persistence.repositories.targets import TargetRepository


class InvalidTargetMetadataError(ValueError):
    """表示 Facebook-derived metadata 不應寫入 target。"""


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

        normalized_name = _clean_persisted_target_name(target.name)
        normalized_group_name = _clean_persisted_target_name(target.group_name)
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
        request_group_name = _normalize_group_metadata_name(group_name, strict=True)
        request_cover_image_url = _normalize_metadata_url(group_cover_image_url, strict=True)
        if not request_group_name and not request_cover_image_url:
            return target

        should_update_name = False
        if request_group_name:
            should_update_name = overwrite_name or (
                target.target_kind == TargetKind.POSTS
                and (
                    is_generated_group_posts_name(target.name, target.group_id)
                    or is_invalid_facebook_group_name(target.name)
                )
            )
            should_update_name = should_update_name or (
                target.target_kind == TargetKind.COMMENTS
                and (
                    overwrite_name
                    or is_generated_group_comments_name(
                        target.name,
                        target.group_id,
                        target.parent_post_id,
                    )
                    or is_generated_group_comments_display_name(
                        target.name,
                        parent_post_id=target.parent_post_id,
                    )
                    or is_invalid_facebook_group_name(target.name)
                )
            )
        next_name = target.name
        if should_update_name:
            next_name = (
                request_group_name
                if overwrite_name or target.target_kind == TargetKind.POSTS
                else generated_group_comments_display_name(
                    request_group_name,
                    target.parent_post_id,
                )
            )
        updated_target = replace(
            target,
            name=next_name,
            group_name=request_group_name or _normalize_group_metadata_name(target.group_name),
            group_cover_image_url=_next_metadata_cover_image_url(
                target,
                request_cover_image_url,
            ),
            metadata_status=TargetMetadataStatus.RESOLVED,
            metadata_error="",
            updated_at=utc_now(),
        )
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
        request_cover_image_url = _normalize_metadata_url(group_cover_image_url, strict=True)
        if not request_cover_image_url:
            if has_polluted_group_cover_image_url(target.group_cover_image_url):
                updated_target = replace(
                    target,
                    group_cover_image_url="",
                    updated_at=utc_now(),
                )
                self.targets.save(updated_target)
                return updated_target
            return target
        updated_target = replace(
            target,
            group_cover_image_url=request_cover_image_url,
            updated_at=utc_now(),
        )
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
            metadata_error=_normalize_metadata_error(error),
            updated_at=utc_now(),
        )
        self.targets.save(updated_target)
        return updated_target

    def update_target_name(self, target_id: str, name: str) -> TargetDescriptor:
        """更新使用者自訂 target 顯示名稱，保留 Facebook metadata group name。"""

        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        request_name = _clean_persisted_target_name(name)
        if not request_name:
            raise ValueError("target name must not be empty")
        updated_target = replace(
            target,
            name=request_name,
            group_name=_clean_persisted_target_name(target.group_name),
            metadata_status=TargetMetadataStatus.RESOLVED,
            metadata_error="",
            updated_at=utc_now(),
        )
        self.targets.save(updated_target)
        return updated_target

    def upsert_group_posts_target(
        self,
        request: UpsertGroupPostsTargetRequest,
    ) -> TargetDescriptor:
        """建立或更新 group posts target，供 capture script 可重複執行。"""

        existing = self.targets.find_by_kind_scope(TargetKind.POSTS, request.group_id)
        request_name = _clean_persisted_target_name(request.name)
        request_group_name = _normalize_group_metadata_name(request.group_name)
        request_cover_image_url = _normalize_metadata_url(request.group_cover_image_url)
        if existing:
            existing = self.normalize_target_names(existing)
            existing_name = _clean_persisted_target_name(existing.name)
            existing_group_name = _normalize_group_metadata_name(existing.group_name)
            existing_cover_image_url = _existing_cover_image_url_or_empty(existing)
            next_name = request_name or existing_name
            if (
                not request_name
                and request_group_name
                and (
                    is_generated_group_posts_name(existing.name, existing.group_id)
                    or is_invalid_facebook_group_name(existing.name)
                )
            ):
                next_name = request_group_name
            target = replace(
                existing,
                name=next_name,
                group_name=request_group_name or existing_group_name,
                group_cover_image_url=request_cover_image_url or existing_cover_image_url,
                canonical_url=request.canonical_url,
                metadata_status=(
                    TargetMetadataStatus.RESOLVED
                    if request_name or request_group_name or request_cover_image_url
                    else existing.metadata_status
                ),
                metadata_error=(
                    ""
                    if request_name or request_group_name or request_cover_image_url
                    else existing.metadata_error
                ),
                updated_at=utc_now(),
            )
        else:
            target = TargetDescriptor.for_group_posts(
                group_id=request.group_id,
                canonical_url=request.canonical_url,
                name=request_name,
                group_name=request_group_name,
                group_cover_image_url=request_cover_image_url,
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
        request_name = _clean_persisted_target_name(request.name)
        request_group_name = _normalize_group_metadata_name(request.group_name)
        request_cover_image_url = _normalize_metadata_url(request.group_cover_image_url)
        if existing:
            existing = self.normalize_target_names(existing)
            existing_name = _clean_persisted_target_name(existing.name)
            existing_group_name = _normalize_group_metadata_name(existing.group_name)
            existing_cover_image_url = _existing_cover_image_url_or_empty(existing)
            next_name = request_name or existing_name
            if (
                not request_name
                and request_group_name
                and (
                    is_generated_group_comments_name(
                        existing.name,
                        existing.group_id,
                        existing.parent_post_id,
                    )
                    or is_generated_group_comments_display_name(
                        existing.name,
                        parent_post_id=existing.parent_post_id,
                    )
                    or is_invalid_facebook_group_name(existing.name)
                )
            ):
                next_name = generated_group_comments_display_name(
                    request_group_name,
                    existing.parent_post_id,
                )
            target = replace(
                existing,
                name=next_name,
                group_name=request_group_name or existing_group_name,
                group_cover_image_url=request_cover_image_url or existing_cover_image_url,
                canonical_url=request.canonical_url,
                metadata_status=(
                    TargetMetadataStatus.RESOLVED
                    if request_name or request_group_name or request_cover_image_url
                    else existing.metadata_status
                ),
                metadata_error=(
                    ""
                    if request_name or request_group_name or request_cover_image_url
                    else existing.metadata_error
                ),
                updated_at=utc_now(),
            )
        else:
            target = TargetDescriptor.for_comments(
                group_id=request.group_id,
                parent_post_id=request.parent_post_id,
                canonical_url=request.canonical_url,
                name=request_name,
                group_name=request_group_name,
                group_cover_image_url=request_cover_image_url,
            )

        config = self.configs.build_or_merge_config_for_target(target, request.config)

        self.targets.save(target)
        self.configs.save_config_for_target(target, config)
        self.runtime.ensure_runtime_state(target.id)
        return target


def _clean_persisted_target_name(value: str) -> str:
    """清理準備保存的 Facebook target 名稱。"""

    return clean_target_display_name(value)


def _normalize_metadata_error(value: str) -> str:
    """把 metadata refresh 錯誤壓成可保存的短訊息。"""

    normalized = " ".join(str(value or "").split())
    if not normalized:
        return "metadata refresh failed"
    return normalized[:500]


def _normalize_group_metadata_name(value: str, *, strict: bool = False) -> str:
    """整理 Facebook-derived group name；錯誤頁名稱不得進入 metadata。"""

    normalized = _clean_persisted_target_name(value)
    if normalized and is_invalid_facebook_group_name(normalized):
        if strict:
            raise InvalidTargetMetadataError("Facebook 回傳錯誤頁，未更新 target metadata")
        return ""
    return normalized


def _normalize_metadata_url(value: str, *, strict: bool = False) -> str:
    """整理 Facebook metadata URL，避免空白與控制字元進 DB。"""

    raw = str(value or "").strip()
    result = sanitize_facebook_group_cover_image_url(raw)
    if raw and not result.ok and strict:
        raise InvalidTargetMetadataError(
            "Facebook 回傳不可作為社團封面的圖片，未更新 target metadata"
        )
    return result.url if result.ok else ""


def _existing_cover_image_url_or_empty(target: TargetDescriptor) -> str:
    """回傳既有封面 URL；已知錯誤頁通用圖視為空值。"""

    if has_polluted_group_cover_image_url(target.group_cover_image_url):
        return ""
    return target.group_cover_image_url


def _next_metadata_cover_image_url(
    target: TargetDescriptor,
    request_cover_image_url: str,
) -> str:
    """決定 metadata refresh 後要保存的封面 URL，避免保留通用錯誤圖。"""

    if request_cover_image_url:
        return request_cover_image_url
    return _existing_cover_image_url_or_empty(target)
