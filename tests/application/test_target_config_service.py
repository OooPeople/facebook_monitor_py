"""Application service tests。"""

from __future__ import annotations

from dataclasses import fields
from datetime import timedelta
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_config_merge import TARGET_CONFIG_PATCH_FIELDS
from facebook_monitor.application.target_config_merge import build_target_config_from_patch
from facebook_monitor.application.target_config_merge import merge_target_config_patch
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.application.target_requests import UpdateTargetConfigRequest
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.keyword_groups import keyword_group_slots
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import utc_now


def test_target_config_patch_fields_track_request_model() -> None:
    """config merge 欄位清單需能安全套用到 TargetConfig。"""

    assert TARGET_CONFIG_PATCH_FIELDS == tuple(field.name for field in fields(TargetConfigPatch))
    target_config_fields = {field.name for field in fields(TargetConfig)}
    assert set(TARGET_CONFIG_PATCH_FIELDS) <= target_config_fields


def test_build_target_config_from_patch_preserves_explicit_values_and_defaults() -> None:
    """建立新 target config 時，未提供欄位走正式預設，明確 false/空值要保留。"""

    config = build_target_config_from_patch(
        "target-1",
        TargetConfigPatch(
            include_keywords=(),
            exclude_keywords=(),
            enable_ntfy=False,
            ntfy_topic="",
            max_items_per_scan=30,
            auto_load_more=False,
        ),
    )

    assert config.target_id == "target-1"
    assert config.include_keywords == ()
    assert [group.keywords for group in config.include_keyword_groups] == [(), (), ()]
    assert config.exclude_keywords == ()
    assert config.exclude_ignore_phrases == (PYTHON_TARGET_CONFIG_DEFAULTS.exclude_ignore_phrases)
    assert config.enable_ntfy is False
    assert config.ntfy_topic == ""
    assert config.max_items_per_scan == 10
    assert config.auto_load_more is False


def test_merge_target_config_patch_preserves_omitted_values_and_clamps() -> None:
    """更新既有 target config 時，只改 patch 欄位，並保留 max items clamp。"""

    existing = TargetConfig(
        target_id="target-1",
        include_keywords=("old",),
        exclude_keywords=("old-exclude",),
        enable_ntfy=True,
        ntfy_topic="old-topic",
        max_items_per_scan=4,
        auto_load_more=True,
    )

    merged = merge_target_config_patch(
        existing,
        TargetConfigPatch(
            include_keywords=(),
            max_items_per_scan=30,
            auto_load_more=False,
        ),
    )

    assert merged.target_id == "target-1"
    assert merged.include_keywords == ()
    assert [group.keywords for group in merged.include_keyword_groups] == [(), (), ()]
    assert merged.exclude_keywords == ("old-exclude",)
    assert merged.enable_ntfy is True
    assert merged.ntfy_topic == "old-topic"
    assert merged.max_items_per_scan == 10
    assert merged.auto_load_more is False


def test_target_config_patch_syncs_include_keyword_groups_projection() -> None:
    """include groups 更新時同步維持 legacy flat include_keywords projection。"""

    groups = keyword_group_slots((("5/1;5/2",), ("108;109",)))

    legacy_config = build_target_config_from_patch(
        "target-1",
        TargetConfigPatch(include_keywords=("票", "交換")),
    )
    config = build_target_config_from_patch(
        "target-1",
        TargetConfigPatch(include_keyword_groups=groups),
    )
    merged = merge_target_config_patch(
        TargetConfig(target_id="target-1", include_keywords=("old",)),
        TargetConfigPatch(include_keyword_groups=groups),
    )

    assert [group.keywords for group in legacy_config.include_keyword_groups] == [
        ("票", "交換"),
        (),
        (),
    ]
    assert config.include_keywords == ("5/1;5/2", "108;109")
    assert merged.include_keywords == ("5/1;5/2", "108;109")
    assert [group.keywords for group in merged.include_keyword_groups] == [
        ("5/1;5/2",),
        ("108;109",),
        (),
    ]


def test_target_facade_exposes_display_next_due_update(tmp_path: Path) -> None:
    """resident main 走 targets facade 寫入 display-only next due。"""

    db_path = tmp_path / "app.db"
    due_at = utc_now() + timedelta(seconds=60)
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

        updated_state = app.services.targets.set_target_display_next_due_at(
            target.id,
            due_at,
        )
        loaded_state = app.repositories.runtime_states.get(target.id)

        assert updated_state is not None
        assert loaded_state is not None
        assert loaded_state.display_next_due_at == due_at


def test_update_target_config(tmp_path: Path) -> None:
    """application service 可更新 target config，供設定入口共用。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        initial_config = app.repositories.configs.get_for_target(target)

        assert initial_config is not None
        assert (
            initial_config.exclude_ignore_phrases
            == PYTHON_TARGET_CONFIG_DEFAULTS.exclude_ignore_phrases
        )

        config = app.services.targets.update_target_config(
            UpdateTargetConfigRequest(
                target_id=target.id,
                config=TargetConfigPatch(
                    include_keywords=("票", "交換"),
                    exclude_keywords=("售完",),
                    exclude_ignore_phrases=("全收;回收",),
                    fixed_refresh_sec=90,
                    max_items_per_scan=30,
                    auto_load_more=False,
                    auto_adjust_sort=True,
                    enable_desktop_notification=True,
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                    enable_discord_notification=True,
                    discord_webhook="https://discord.com/api/webhooks/example",
                ),
            )
        )
        loaded_config = app.repositories.configs.get_for_target(target)

        assert loaded_config == config
        assert config.include_keywords == ("票", "交換")
        assert config.exclude_keywords == ("售完",)
        assert config.exclude_ignore_phrases == ("全收;回收",)
        assert config.fixed_refresh_sec == 90
        assert config.max_items_per_scan == 10
        assert not config.auto_load_more
        assert config.auto_adjust_sort
        assert config.enable_desktop_notification
        assert config.enable_ntfy
        assert config.ntfy_topic == "phase0test"
        assert config.enable_discord_notification
        assert config.discord_webhook == "https://discord.com/api/webhooks/example"


def test_update_target_config_preserves_omitted_notification_channels(
    tmp_path: Path,
) -> None:
    """patch 未提供的通知欄位，更新一般設定時會保留原值。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    enable_desktop_notification=True,
                    enable_discord_notification=True,
                    discord_webhook="https://discord.com/api/webhooks/example",
                ),
            )
        )

        config = app.services.targets.update_target_config(
            UpdateTargetConfigRequest(
                target_id=target.id,
                config=TargetConfigPatch(
                    include_keywords=("票",),
                    exclude_keywords=(),
                    fixed_refresh_sec=60,
                    max_items_per_scan=5,
                    auto_load_more=True,
                    auto_adjust_sort=False,
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )

        assert config.enable_desktop_notification
        assert config.enable_discord_notification
        assert config.discord_webhook == "https://discord.com/api/webhooks/example"
