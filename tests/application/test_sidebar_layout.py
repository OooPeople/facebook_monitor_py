"""Sidebar layout service tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import TargetConfigPatch
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.core.sidebar_models import SidebarGroupConfigTemplate
from facebook_monitor.persistence.repositories.app_settings import TargetKeywordDefaultSettings
from facebook_monitor.webapp.query_service import get_dashboard_view


def test_dashboard_order_uses_sidebar_placement_without_changing_target_repository(
    tmp_path: Path,
) -> None:
    """dashboard 依 sidebar placement 排序，但 TargetRepository.list_all 維持 created_at 語義。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="第一個",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
                group_name="第二個",
            )
        )

    get_dashboard_view(db_path)
    with SqliteApplicationContext(db_path) as app:
        assert app.repositories.sidebar_layout.list_placements() == {}

    with SqliteApplicationContext(db_path) as app:
        app.services.sidebar_layout.save_target_order([second.id, first.id])
        repository_order = [target.id for target in app.repositories.targets.list_all()]

    dashboard = get_dashboard_view(db_path)

    assert repository_order == [first.id, second.id]
    assert [row.target_id for row in dashboard.rows] == [second.id, first.id]
    assert [item.target_id for item in dashboard.sidebar_items] == [second.id, first.id]


def test_sidebar_group_placement_does_not_change_target_config(tmp_path: Path) -> None:
    """target 移入 sidebar group 只改 UI placement，不改 target-scoped config。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                config=TargetConfigPatch(include_keywords=("第一",)),
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
                config=TargetConfigPatch(include_keywords=("第二",)),
            )
        )
        group = app.services.sidebar_layout.create_group("工作")
        app.services.sidebar_layout.save_placements(
            [
                (group.id, [second.id]),
                (None, [first.id]),
            ]
        )
        first_config = app.repositories.configs.get_for_target(first)
        second_config = app.repositories.configs.get_for_target(second)

    dashboard = get_dashboard_view(db_path)
    work_section = next(section for section in dashboard.sidebar_groups if section.group_id == group.id)
    ungrouped_section = next(section for section in dashboard.sidebar_groups if section.group_id is None)

    assert first_config is not None
    assert second_config is not None
    assert first_config.include_keywords == ("第一",)
    assert second_config.include_keywords == ("第二",)
    assert [item.target_id for item in work_section.items] == [second.id]
    assert [item.target_id for item in ungrouped_section.items] == [first.id]


def test_save_layout_updates_group_order_and_placements_together(tmp_path: Path) -> None:
    """sidebar 排序保存用單一 service 入口同時更新 group order 與 placements。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        first_group = app.services.sidebar_layout.create_group("第一群")
        second_group = app.services.sidebar_layout.create_group("第二群")

        updated_count = app.services.sidebar_layout.save_layout(
            group_ids=[second_group.id, first_group.id],
            grouped_target_ids=[
                (second_group.id, [second.id]),
                (first_group.id, [first.id]),
                (None, []),
            ],
        )
        groups = app.repositories.sidebar_layout.list_groups()
        placements = app.repositories.sidebar_layout.list_placements()

    assert updated_count == 2
    assert [group.id for group in groups] == [second_group.id, first_group.id]
    assert placements[first.id].sidebar_group_id == first_group.id
    assert placements[second.id].sidebar_group_id == second_group.id


def test_save_layout_validates_before_writing_partial_group_order(tmp_path: Path) -> None:
    """placement payload 不合法時，不可先寫入 group order 造成半套狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        first_group = app.services.sidebar_layout.create_group("第一群")
        second_group = app.services.sidebar_layout.create_group("第二群")

        try:
            app.services.sidebar_layout.save_layout(
                group_ids=[second_group.id, first_group.id],
                grouped_target_ids=[
                    (second_group.id, [target.id, "missing-target"]),
                    (None, []),
                ],
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid layout payload should be rejected")

        groups = app.repositories.sidebar_layout.list_groups()
        placements = app.repositories.sidebar_layout.list_placements()

    assert [group.id for group in groups] == [first_group.id, second_group.id]
    assert placements == {}


def test_flat_target_order_rejects_existing_grouped_placements(tmp_path: Path) -> None:
    """已有 grouped placement 時不可使用舊平面排序 API 打平所有 targets。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        group = app.services.sidebar_layout.create_group("已分組")
        app.services.sidebar_layout.save_placements([(group.id, [first.id]), (None, [second.id])])

        try:
            app.services.sidebar_layout.save_target_order([second.id, first.id])
        except ValueError as exc:
            error = str(exc)
        else:
            raise AssertionError("flat target order should reject grouped placements")

    assert "grouped placement" in error


def test_group_template_apply_updates_only_group_target_configs(tmp_path: Path) -> None:
    """group template 明確套用時只覆蓋該 sidebar group 內 targets。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                config=TargetConfigPatch(include_keywords=("原本一",)),
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
                config=TargetConfigPatch(include_keywords=("原本二",)),
            )
        )
        outside = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="333",
                canonical_url="https://www.facebook.com/groups/333",
                config=TargetConfigPatch(include_keywords=("外部",)),
            )
        )
        group = app.services.sidebar_layout.create_group("批次套用")
        app.services.sidebar_layout.save_placements(
            [
                (group.id, [first.id, second.id]),
                (None, [outside.id]),
            ]
        )
        app.services.sidebar_layout.save_template(
            SidebarGroupConfigTemplate(
                sidebar_group_id=group.id,
                include_keywords=("模板",),
                exclude_keywords=("售完",),
                exclude_ignore_phrases=("全收;回收",),
                fixed_refresh_sec=45,
                max_items_per_scan=8,
                auto_load_more=False,
                auto_adjust_sort=True,
                enable_ntfy=True,
                ntfy_topic="group-topic",
            )
        )

        updated_count = app.services.sidebar_layout.apply_template(group.id, ["all"])
        first_config = app.repositories.configs.get_for_target(first)
        second_config = app.repositories.configs.get_for_target(second)
        outside_config = app.repositories.configs.get_for_target(outside)

    assert updated_count == 2
    for config in (first_config, second_config):
        assert config is not None
        assert config.include_keywords == ("模板",)
        assert config.exclude_keywords == ("售完",)
        assert config.exclude_ignore_phrases == ("全收;回收",)
        assert config.fixed_refresh_sec == 45
        assert config.max_items_per_scan == 8
        assert not config.auto_load_more
        assert config.auto_adjust_sort
        assert config.enable_ntfy
        assert config.ntfy_topic == "group-topic"
    assert outside_config is not None
    assert outside_config.include_keywords == ("外部",)
    assert not outside_config.enable_ntfy


def test_create_group_snapshots_current_global_keyword_defaults(tmp_path: Path) -> None:
    """新增 group 時複製當下全域關鍵字預設值，之後不跟著全域設定變動。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        app.repositories.app_settings.save_target_keyword_defaults(
            TargetKeywordDefaultSettings(
                exclude_keywords_text="徵;收;已售",
                exclude_ignore_phrases_text="全收;回收",
            )
        )
        group = app.services.sidebar_layout.create_group("新群組")
        app.repositories.app_settings.save_target_keyword_defaults(
            TargetKeywordDefaultSettings(
                exclude_keywords_text="售完",
                exclude_ignore_phrases_text="回覆",
            )
        )
        template = app.repositories.sidebar_layout.get_template(group.id)

    assert template is not None
    assert template.exclude_keywords == ("徵", "收", "已售")
    assert template.exclude_ignore_phrases == ("全收", "回收")
