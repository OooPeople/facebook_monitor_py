"""Target create use case tests。"""

from __future__ import annotations

from pathlib import Path

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_create_service import build_create_target_plan
from facebook_monitor.application.target_create_service import create_or_update_target_from_plan
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.facebook.route_detection import RouteDetectionError


def test_create_target_use_case_adds_group_posts_target_with_pending_metadata(
    tmp_path: Path,
) -> None:
    """無自訂名稱時先保存 posts target/config，再交由 resident 補 metadata。"""

    db_path = tmp_path / "app.db"
    plan = build_create_target_plan(
        group_url="https://www.facebook.com/groups/222518561920110/",
        display_name="",
    )

    with SqliteApplicationContext(db_path) as app_context:
        result = create_or_update_target_from_plan(
            app_context.services.targets,
            plan=plan,
            config=TargetConfigPatch(exclude_keywords=("售完",)),
        )
        target = app_context.repositories.targets.get(result.target.id)
        config = app_context.repositories.configs.get_for_target(result.target)

    assert target is not None
    assert result.metadata_refresh_target_id == target.id
    assert result.metadata_refresh_pending
    assert target.target_kind == TargetKind.POSTS
    assert target.group_id == "222518561920110"
    assert target.name == "group:222518561920110:posts"
    assert target.group_name == ""
    assert target.group_cover_image_url == ""
    assert target.metadata_status == TargetMetadataStatus.PENDING
    assert config is not None
    assert config.exclude_keywords == ("售完",)


def test_create_target_use_case_adds_comments_target(tmp_path: Path) -> None:
    """use case 會依 permalink URL 建立 comments target。"""

    db_path = tmp_path / "app.db"
    plan = build_create_target_plan(
        group_url="https://www.facebook.com/groups/204808657039646/permalink/2155501991970293",
        display_name="自訂留言",
    )

    with SqliteApplicationContext(db_path) as app_context:
        result = create_or_update_target_from_plan(
            app_context.services.targets,
            plan=plan,
            config=TargetConfigPatch(),
        )
        target = app_context.repositories.targets.get(result.target.id)

    assert result.metadata_refresh_target_id == ""
    assert not result.metadata_refresh_pending
    assert target is not None
    assert target.target_kind == TargetKind.COMMENTS
    assert target.group_id == "204808657039646"
    assert target.parent_post_id == "2155501991970293"
    assert target.canonical_url == (
        "https://www.facebook.com/groups/204808657039646/posts/2155501991970293"
    )
    assert target.name == "自訂留言"


def test_create_target_use_case_marks_metadata_refresh_pending_without_custom_name(
    tmp_path: Path,
) -> None:
    """metadata refresh 決策只看有無自訂名稱，不依賴 scheduler snapshot。"""

    db_path = tmp_path / "app.db"
    plan = build_create_target_plan(
        group_url="https://www.facebook.com/groups/222518561920110/",
        display_name="",
    )

    with SqliteApplicationContext(db_path) as app_context:
        result = create_or_update_target_from_plan(
            app_context.services.targets,
            plan=plan,
            config=TargetConfigPatch(),
        )
        target = app_context.repositories.targets.get(result.target.id)

    assert target is not None
    assert result.metadata_refresh_target_id == target.id
    assert target.metadata_status == TargetMetadataStatus.PENDING
    assert target.group_name == ""


def test_create_target_use_case_preserves_custom_name_without_metadata_refresh(
    tmp_path: Path,
) -> None:
    """使用者自訂名稱維持 resolved，且不交給 resident 覆寫。"""

    db_path = tmp_path / "app.db"
    plan = build_create_target_plan(
        group_url="https://www.facebook.com/groups/222518561920110/",
        display_name="我的票券社團",
    )

    with SqliteApplicationContext(db_path) as app_context:
        result = create_or_update_target_from_plan(
            app_context.services.targets,
            plan=plan,
            config=TargetConfigPatch(),
        )
        target = app_context.repositories.targets.get(result.target.id)

    assert target is not None
    assert result.metadata_refresh_target_id == ""
    assert not result.metadata_refresh_pending
    assert target.name == "我的票券社團"
    assert target.metadata_status == TargetMetadataStatus.RESOLVED
    assert target.group_name == ""


def test_create_target_plan_rejects_invalid_url_without_db_write(tmp_path: Path) -> None:
    """invalid URL 會在 plan 階段失敗，不需要開 DB transaction。"""

    db_path = tmp_path / "app.db"

    with pytest.raises(RouteDetectionError):
        build_create_target_plan(
            group_url="https://example.com/not-facebook",
            display_name="",
        )

    assert not db_path.exists()
