"""Target registry descriptor building policy。

職責：集中 target descriptor 的純決策與建構，避免 application service
同時承擔 repository orchestration 與欄位更新規則。
"""

from __future__ import annotations

from dataclasses import replace

from facebook_monitor.application.target_display import (
    is_generated_group_comments_display_name,
)
from facebook_monitor.application.target_metadata_policy import clean_persisted_target_name
from facebook_monitor.application.target_metadata_policy import (
    existing_cover_image_url_or_empty,
)
from facebook_monitor.application.target_metadata_policy import next_metadata_cover_image_url
from facebook_monitor.application.target_metadata_policy import normalize_group_metadata_name
from facebook_monitor.application.target_metadata_policy import normalize_metadata_url
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import generated_group_comments_display_name
from facebook_monitor.core.models import is_generated_group_comments_name
from facebook_monitor.core.models import is_generated_group_posts_name
from facebook_monitor.core.models import utc_now
from facebook_monitor.facebook.group_metadata_validation import (
    has_polluted_group_cover_image_url,
)
from facebook_monitor.facebook.group_metadata_validation import is_invalid_facebook_group_name


def build_normalized_target_names(target: TargetDescriptor) -> TargetDescriptor:
    """回傳已清理持久化名稱的 target；若不需修改則回傳原物件。"""

    normalized_name = clean_persisted_target_name(target.name)
    normalized_group_name = clean_persisted_target_name(target.group_name)
    if normalized_name == target.name and normalized_group_name == target.group_name:
        return target
    return replace(
        target,
        name=normalized_name,
        group_name=normalized_group_name,
        updated_at=utc_now(),
    )


def build_target_with_group_metadata_refresh(
    target: TargetDescriptor,
    *,
    group_name: str,
    group_cover_image_url: str = "",
    overwrite_name: bool = False,
) -> TargetDescriptor:
    """依 Facebook metadata refresh 結果建立下一版 target descriptor。"""

    request_group_name = normalize_group_metadata_name(group_name, strict=True)
    request_cover_image_url = normalize_metadata_url(
        group_cover_image_url,
        strict=True,
    )
    if not request_group_name and not request_cover_image_url:
        return target

    next_name = _metadata_refresh_target_name(
        target,
        request_group_name=request_group_name,
        overwrite_name=overwrite_name,
    )
    return replace(
        target,
        name=next_name,
        group_name=request_group_name or normalize_group_metadata_name(target.group_name),
        group_cover_image_url=next_metadata_cover_image_url(
            target,
            request_cover_image_url,
        ),
        metadata_status=TargetMetadataStatus.RESOLVED,
        metadata_error="",
        updated_at=utc_now(),
    )


def build_target_with_group_cover_image_refresh(
    target: TargetDescriptor,
    group_cover_image_url: str,
) -> TargetDescriptor:
    """建立 image-only cover refresh 後的 target，不改名稱與 metadata 狀態。"""

    request_cover_image_url = normalize_metadata_url(group_cover_image_url, strict=True)
    if not request_cover_image_url:
        if has_polluted_group_cover_image_url(target.group_cover_image_url):
            return replace(
                target,
                group_cover_image_url="",
                updated_at=utc_now(),
            )
        return target
    return replace(
        target,
        group_cover_image_url=request_cover_image_url,
        updated_at=utc_now(),
    )


def build_target_with_custom_name(
    target: TargetDescriptor,
    name: str,
) -> TargetDescriptor:
    """建立使用者自訂名稱更新後的 target descriptor。"""

    request_name = clean_persisted_target_name(name)
    if not request_name:
        raise ValueError("target name must not be empty")
    return replace(
        target,
        name=request_name,
        group_name=clean_persisted_target_name(target.group_name),
        metadata_status=TargetMetadataStatus.RESOLVED,
        metadata_error="",
        updated_at=utc_now(),
    )


def build_upserted_group_posts_target(
    *,
    existing: TargetDescriptor | None,
    request: UpsertGroupPostsTargetRequest,
) -> TargetDescriptor:
    """建立或更新 group posts target descriptor，不執行 repository 副作用。"""

    request_name = clean_persisted_target_name(request.name)
    request_group_name = normalize_group_metadata_name(request.group_name)
    request_cover_image_url = normalize_metadata_url(request.group_cover_image_url)
    if existing is None:
        return TargetDescriptor.for_group_posts(
            group_id=request.group_id,
            canonical_url=request.canonical_url,
            name=request_name,
            group_name=request_group_name,
            group_cover_image_url=request_cover_image_url,
        )

    existing_name = clean_persisted_target_name(existing.name)
    existing_group_name = normalize_group_metadata_name(existing.group_name)
    existing_cover_image_url = existing_cover_image_url_or_empty(existing)
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
    return replace(
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


def build_upserted_comments_target(
    *,
    existing: TargetDescriptor | None,
    request: UpsertCommentsTargetRequest,
) -> TargetDescriptor:
    """建立或更新 comments target descriptor，不執行 repository 副作用。"""

    request_name = clean_persisted_target_name(request.name)
    request_group_name = normalize_group_metadata_name(request.group_name)
    request_cover_image_url = normalize_metadata_url(request.group_cover_image_url)
    if existing is None:
        return TargetDescriptor.for_comments(
            group_id=request.group_id,
            parent_post_id=request.parent_post_id,
            canonical_url=request.canonical_url,
            name=request_name,
            group_name=request_group_name,
            group_cover_image_url=request_cover_image_url,
        )

    existing_name = clean_persisted_target_name(existing.name)
    existing_group_name = normalize_group_metadata_name(existing.group_name)
    existing_cover_image_url = existing_cover_image_url_or_empty(existing)
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
    return replace(
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


def _metadata_refresh_target_name(
    target: TargetDescriptor,
    *,
    request_group_name: str,
    overwrite_name: bool,
) -> str:
    """決定 metadata refresh 是否可覆蓋 target 顯示名稱。"""

    if not request_group_name:
        return target.name
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
    if not should_update_name:
        return target.name
    if overwrite_name or target.target_kind == TargetKind.POSTS:
        return request_group_name
    return generated_group_comments_display_name(
        request_group_name,
        target.parent_post_id,
    )


__all__ = [
    "build_normalized_target_names",
    "build_target_with_custom_name",
    "build_target_with_group_cover_image_refresh",
    "build_target_with_group_metadata_refresh",
    "build_upserted_comments_target",
    "build_upserted_group_posts_target",
]
