"""Target registry pure policy tests。"""

from __future__ import annotations

from dataclasses import replace

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
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetMetadataStatus


def test_group_posts_policy_preserves_metadata_status_without_new_metadata() -> None:
    """upsert 沒收到新 metadata 時不可清掉既有 failure 狀態與錯誤訊息。"""

    existing = replace(
        TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
            group_name="舊社團",
        ),
        metadata_status=TargetMetadataStatus.FAILED,
        metadata_error="metadata failed",
    )

    updated = build_upserted_group_posts_target(
        existing=existing,
        request=UpsertGroupPostsTargetRequest(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
        ),
    )

    assert updated.metadata_status == TargetMetadataStatus.FAILED
    assert updated.metadata_error == "metadata failed"
    assert updated.group_name == "舊社團"


def test_comments_policy_refreshes_generated_display_name() -> None:
    """comments target 自動顯示名會跟著新 group metadata 更新。"""

    existing = TargetDescriptor.for_comments(
        group_id="222518561920110",
        parent_post_id="111",
        canonical_url="https://www.facebook.com/groups/222518561920110/posts/111",
        group_name="舊社團",
    )

    updated = build_upserted_comments_target(
        existing=existing,
        request=UpsertCommentsTargetRequest(
            group_id="222518561920110",
            parent_post_id="111",
            canonical_url="https://www.facebook.com/groups/222518561920110/posts/111",
            group_name="新社團",
        ),
    )

    assert updated.name == "新社團 / post:111"
    assert updated.group_name == "新社團"
    assert updated.metadata_status == TargetMetadataStatus.RESOLVED
    assert updated.metadata_error == ""


def test_metadata_refresh_policy_clears_polluted_group_name_without_new_name() -> None:
    """metadata refresh 只有新封面時，不可保留既有錯誤頁 group name。"""

    existing = replace(
        TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
        ),
        group_name="Facebook | Error",
        group_cover_image_url="https://static.facebook.com/images/logos/facebook_2x.png",
    )

    updated = build_target_with_group_metadata_refresh(
        existing,
        group_name="",
        group_cover_image_url="https://scontent.xx.fbcdn.net/group-cover.jpg",
    )

    assert updated.name == existing.name
    assert updated.group_name == ""
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"
    assert updated.metadata_status == TargetMetadataStatus.RESOLVED
    assert updated.metadata_error == ""
