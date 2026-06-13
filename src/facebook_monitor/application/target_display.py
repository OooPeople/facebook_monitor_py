"""Target 顯示名稱整理。

職責：集中定義使用者會看到的 target 名稱，供 Web UI 與 notification 共用。
"""

from __future__ import annotations

from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import generated_group_comments_display_name
from facebook_monitor.core.models import is_generated_group_comments_name
from facebook_monitor.core.models import is_generated_group_posts_name
from facebook_monitor.facebook.group_metadata_validation import is_invalid_facebook_group_name
from facebook_monitor.facebook.route_detection import clean_facebook_page_title


def clean_target_display_name(value: str) -> str:
    """清理 Facebook target 名稱中的 title noise。"""

    return clean_facebook_page_title(value)


def format_target_display_name(
    target: TargetDescriptor,
    *,
    generated_fallback: str = "",
) -> str:
    """回傳目前應顯示給使用者的 target 名稱。"""

    raw_target_name = str(target.name or "").strip()
    target_name = clean_target_display_name(raw_target_name)
    group_name = clean_target_display_name(target.group_name)
    if is_invalid_facebook_group_name(target_name):
        target_name = ""
    if is_invalid_facebook_group_name(group_name):
        group_name = ""
    if target.target_kind == TargetKind.COMMENTS:
        return _format_comments_target_display_name(
            target,
            raw_target_name=raw_target_name,
            target_name=target_name,
            group_name=group_name,
            generated_fallback=generated_fallback,
        )
    return _format_posts_target_display_name(
        target,
        target_name=target_name,
        group_name=group_name,
        generated_fallback=generated_fallback,
    )


def _format_comments_target_display_name(
    target: TargetDescriptor,
    *,
    raw_target_name: str,
    target_name: str,
    group_name: str,
    generated_fallback: str,
) -> str:
    """整理 comments target 顯示名稱。"""

    has_custom_name = target_name and not is_generated_group_comments_name(
        target_name,
        target.group_id,
        target.parent_post_id,
    )
    if has_custom_name and not is_generated_group_comments_display_name(
        raw_target_name,
        parent_post_id=target.parent_post_id,
    ):
        return target_name
    if group_name:
        return clean_target_display_name(
            generated_group_comments_display_name(
                group_name,
                target.parent_post_id,
            )
        )
    return generated_fallback or target_name or target.scope_id or target.id


def _format_posts_target_display_name(
    target: TargetDescriptor,
    *,
    target_name: str,
    group_name: str,
    generated_fallback: str,
) -> str:
    """整理 posts target 顯示名稱。"""

    if target_name and not is_generated_group_posts_name(target_name, target.group_id):
        return target_name
    if group_name:
        return group_name
    return generated_fallback or target_name or target.group_id or target.id


def is_generated_group_comments_display_name(
    value: str,
    *,
    parent_post_id: str,
) -> bool:
    """判斷 comments 顯示名是否為 metadata 自動組出的 post-scoped 名稱。"""

    suffix = f" / post:{str(parent_post_id or '').strip()}"
    if not value or not suffix.strip() or not value.endswith(suffix):
        return False
    base_name = clean_target_display_name(value[: -len(suffix)])
    return bool(base_name)
