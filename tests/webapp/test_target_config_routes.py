"""FastAPI Web UI tests。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.input_limits import MAX_KEYWORD_TEXT_LENGTH
from tests.helpers.webapp import FakeSchedulerManager


from tests.webapp.app_test_helpers import create_app


def test_update_config_route_updates_target_config(tmp_path: Path) -> None:
    """設定表單送出後會更新 target config。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "include_keywords": "票,交換",
            "include_keywords_2": "5/1;5/2",
            "include_keywords_3": "108;109",
            "exclude_keywords": "售完",
            "exclude_ignore_phrases": "全收,回收",
            "refresh_mode": "fixed",
            "fixed_refresh_sec": "90",
            "max_items_per_scan": "30",
            "auto_adjust_sort": "on",
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "phase0test",
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/1234567890/example_token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.include_keywords == ("票", "交換", "5/1", "5/2", "108", "109")
    assert [group.keywords for group in config.include_keyword_groups] == [
        ("票", "交換"),
        ("5/1", "5/2"),
        ("108", "109"),
    ]
    assert config.exclude_keywords == ("售完",)
    assert config.exclude_ignore_phrases == ("全收", "回收")
    assert config.fixed_refresh_sec == 90
    assert config.max_items_per_scan == 10
    assert not config.auto_load_more
    assert config.auto_adjust_sort
    assert config.enable_desktop_notification
    assert config.enable_ntfy
    assert config.ntfy_topic == "phase0test"
    assert config.enable_discord_notification
    assert config.discord_webhook == "https://discord.com/api/webhooks/1234567890/example_token"


def test_update_config_route_rejects_invalid_discord_webhook_without_overwrite(
    tmp_path: Path,
) -> None:
    """target 設定表單不得保存非 Discord 官方 webhook URL。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    enable_discord_notification=True,
                    discord_webhook="https://discord.com/api/webhooks/1234567890/original",
                ),
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "refresh_mode": "floating",
            "fixed_refresh_sec": "60",
            "min_refresh_sec": "20",
            "max_refresh_sec": "40",
            "max_items_per_scan": "5",
            "enable_discord_notification": "on",
            "discord_webhook": "https://example.com/api/webhooks/123/token",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Discord webhook 必須是 Discord 官方 webhook URL" in response.text
    assert "https://example.com/api/webhooks/123/token" not in response.text
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.enable_discord_notification
    assert config.discord_webhook == "https://discord.com/api/webhooks/1234567890/original"


def test_update_config_route_preserves_or_clears_masked_notification_secrets(
    tmp_path: Path,
) -> None:
    """masked notification 欄位留空時可保留，明確清除時才刪除。"""

    db_path = tmp_path / "app.db"
    original_topic = "original-topic"
    original_webhook = "https://discord.com/api/webhooks/1234567890/original"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic=original_topic,
                    enable_discord_notification=True,
                    discord_webhook=original_webhook,
                ),
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    preserve_response = client.post(
        f"/targets/{target.id}/config",
        data={
            "refresh_mode": "floating",
            "fixed_refresh_sec": "60",
            "min_refresh_sec": "20",
            "max_refresh_sec": "40",
            "max_items_per_scan": "5",
            "enable_ntfy": "on",
            "ntfy_topic": "",
            "ntfy_topic_keep": "on",
            "enable_discord_notification": "on",
            "discord_webhook": "",
            "discord_webhook_keep": "on",
        },
        follow_redirects=False,
    )
    with SqliteApplicationContext(db_path) as app_context:
        preserved_config = app_context.repositories.configs.get_for_target(target)
    clear_response = client.post(
        f"/targets/{target.id}/config",
        data={
            "refresh_mode": "floating",
            "fixed_refresh_sec": "60",
            "min_refresh_sec": "20",
            "max_refresh_sec": "40",
            "max_items_per_scan": "5",
            "enable_ntfy": "on",
            "ntfy_topic": "",
            "ntfy_topic_keep": "on",
            "clear_ntfy_topic": "on",
            "enable_discord_notification": "on",
            "discord_webhook": "",
            "discord_webhook_keep": "on",
            "clear_discord_webhook": "on",
        },
        follow_redirects=False,
    )
    with SqliteApplicationContext(db_path) as app_context:
        cleared_config = app_context.repositories.configs.get_for_target(target)

    assert preserve_response.status_code == 303
    assert preserved_config is not None
    assert preserved_config.ntfy_topic == original_topic
    assert preserved_config.discord_webhook == original_webhook
    assert clear_response.status_code == 303
    assert cleared_config is not None
    assert cleared_config.ntfy_topic == ""
    assert cleared_config.discord_webhook == ""


def test_update_config_route_rejects_oversized_keyword_text_without_overwrite(
    tmp_path: Path,
) -> None:
    """target 設定表單要套用集中 keyword textarea 上限。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(include_keywords=("原本",)),
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "include_keywords": "x" * (MAX_KEYWORD_TEXT_LENGTH + 1),
            "refresh_mode": "floating",
            "fixed_refresh_sec": "60",
            "min_refresh_sec": "20",
            "max_refresh_sec": "40",
            "max_items_per_scan": "5",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert f"包含關鍵字 1 不可超過 {MAX_KEYWORD_TEXT_LENGTH} 個字元" in response.text
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.include_keywords == ("原本",)


def test_update_config_route_clears_unchecked_flags_and_notification_fields(
    tmp_path: Path,
) -> None:
    """HTML checkbox 缺欄時應清成 false，通知 endpoint 可被清空。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    auto_load_more=True,
                    auto_adjust_sort=True,
                    enable_desktop_notification=True,
                    enable_ntfy=True,
                    ntfy_topic="old-topic",
                    enable_discord_notification=True,
                    discord_webhook="https://discord.example/old",
                ),
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "include_keywords": "票",
            "exclude_keywords": "",
            "exclude_ignore_phrases": "",
            "refresh_mode": "floating",
            "fixed_refresh_sec": "60",
            "min_refresh_sec": "20",
            "max_refresh_sec": "40",
            "max_items_per_scan": "5",
            "ntfy_topic": "",
            "discord_webhook": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert not config.auto_load_more
    assert not config.auto_adjust_sort
    assert not config.enable_desktop_notification
    assert not config.enable_ntfy
    assert config.ntfy_topic == ""
    assert not config.enable_discord_notification
    assert config.discord_webhook == ""


def test_update_config_route_supports_fixed_and_floating_refresh_modes(
    tmp_path: Path,
) -> None:
    """Web UI 設定表單可保存固定與浮動刷新模式。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    floating_response = client.post(
        f"/targets/{target.id}/config",
        data={
            "refresh_mode": "floating",
            "fixed_refresh_sec": "90",
            "min_refresh_sec": "25",
            "max_refresh_sec": "35",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )
    index_response = client.get("/")
    with SqliteApplicationContext(db_path) as app_context:
        floating_config = app_context.repositories.configs.get_for_target(target)
    fixed_response = client.post(
        f"/targets/{target.id}/config",
        data={
            "refresh_mode": "fixed",
            "fixed_refresh_sec": "120",
            "min_refresh_sec": "20",
            "max_refresh_sec": "40",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert floating_response.status_code == 303
    assert "浮動 25-35 秒" in index_response.text
    assert floating_config is not None
    assert floating_config.fixed_refresh_sec is None
    assert floating_config.jitter_enabled
    assert floating_config.min_refresh_sec == 25
    assert floating_config.max_refresh_sec == 35
    assert fixed_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        fixed_config = app_context.repositories.configs.get_for_target(target)
    assert fixed_config is not None
    assert fixed_config.fixed_refresh_sec == 120
    assert not fixed_config.jitter_enabled
    assert fixed_config.min_refresh_sec == 20
    assert fixed_config.max_refresh_sec == 40


def test_update_config_route_rejects_invalid_floating_refresh_range(
    tmp_path: Path,
) -> None:
    """浮動刷新最小秒數大於最大秒數時，Web UI 會拒絕保存。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "refresh_mode": "floating",
            "fixed_refresh_sec": "60",
            "min_refresh_sec": "35",
            "max_refresh_sec": "25",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.fixed_refresh_sec is None
    assert config.jitter_enabled


def test_update_config_route_preserves_existing_config_after_invalid_range(
    tmp_path: Path,
) -> None:
    """浮動刷新 range 驗證失敗時，不得寫入部分設定。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    include_keywords=("原本",),
                    fixed_refresh_sec=120,
                    min_refresh_sec=15,
                    max_refresh_sec=45,
                    auto_load_more=True,
                    enable_ntfy=True,
                    ntfy_topic="keep-topic",
                ),
            )
        )
        before = app_context.repositories.configs.get_for_target(target)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "include_keywords": "改掉",
            "refresh_mode": "floating",
            "fixed_refresh_sec": "60",
            "min_refresh_sec": "35",
            "max_refresh_sec": "25",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    with SqliteApplicationContext(db_path) as app_context:
        after = app_context.repositories.configs.get_for_target(target)
    assert after == before


def test_update_config_route_rejects_unknown_refresh_mode_without_mutation(
    tmp_path: Path,
) -> None:
    """未知 refresh mode 不可默默 fallback 並覆蓋既有 config。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(include_keywords=("原本",)),
            )
        )
        before = app_context.repositories.configs.get_for_target(target)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "include_keywords": "改掉",
            "refresh_mode": "unexpected",
            "fixed_refresh_sec": "60",
            "min_refresh_sec": "20",
            "max_refresh_sec": "40",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    with SqliteApplicationContext(db_path) as app_context:
        after = app_context.repositories.configs.get_for_target(target)
    assert after == before


def test_update_config_route_does_not_touch_scheduler_runtime(
    tmp_path: Path,
) -> None:
    """設定更新只寫 target config，不喚醒 scheduler 或排下一輪掃描。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        before_runtime = app_context.services.targets.ensure_runtime_state(target.id)

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "include_keywords": "票",
            "refresh_mode": "fixed",
            "fixed_refresh_sec": "90",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert scheduler_manager.started_count == 0
    assert scheduler_manager.stopped_count == 0
    assert scheduler_manager.woken_count == 0
    with SqliteApplicationContext(db_path) as app_context:
        after_runtime = app_context.repositories.runtime_states.get(target.id)
    assert after_runtime == before_runtime
