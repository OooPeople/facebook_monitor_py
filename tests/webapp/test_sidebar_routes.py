"""FastAPI Web UI tests。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS


from tests.webapp.app_test_helpers import create_app


def test_sidebar_layout_api_saves_group_order_and_placements_atomically(tmp_path: Path) -> None:
    """sidebar layout API 以單一請求保存 group order 與 placements。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        first_group = app_context.services.sidebar_layout.create_group("第一群")
        second_group = app_context.services.sidebar_layout.create_group("第二群")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        "/api/sidebar/layout",
        json={
            "group_ids": [second_group.id, first_group.id],
            "groups": [
                {"group_id": second_group.id, "target_ids": [second.id]},
                {"group_id": first_group.id, "target_ids": [first.id]},
                {"group_id": None, "target_ids": []},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "updated_count": 2}
    with SqliteApplicationContext(db_path) as app_context:
        groups = app_context.repositories.sidebar_layout.list_groups()
        placements = app_context.repositories.sidebar_layout.list_placements()
    assert [group.id for group in groups] == [second_group.id, first_group.id]
    assert placements[first.id].sidebar_group_id == first_group.id
    assert placements[second.id].sidebar_group_id == second_group.id


def test_sidebar_group_order_api_rejects_duplicate_group_ids(tmp_path: Path) -> None:
    """sidebar group order API 不接受重複 group id。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first_group = app_context.services.sidebar_layout.create_group("第一群")
        second_group = app_context.services.sidebar_layout.create_group("第二群")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        "/api/sidebar/groups/order",
        json={"group_ids": [first_group.id, second_group.id, first_group.id]},
    )

    assert response.status_code == 400
    assert "重複群組" in response.json()["detail"]
    with SqliteApplicationContext(db_path) as app_context:
        groups = app_context.repositories.sidebar_layout.list_groups()
    assert [group.id for group in groups] == [first_group.id, second_group.id]


def test_sidebar_layout_api_rejects_duplicate_group_sections(tmp_path: Path) -> None:
    """sidebar layout API 不接受重複 group section。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        group = app_context.services.sidebar_layout.create_group("重複群組")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        "/api/sidebar/layout",
        json={
            "group_ids": [group.id],
            "groups": [
                {"group_id": group.id, "target_ids": [first.id]},
                {"group_id": group.id, "target_ids": [second.id]},
                {"group_id": None, "target_ids": []},
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "排序資料不可包含重複群組區塊"


def test_sidebar_placements_api_rejects_duplicate_ungrouped_sections(tmp_path: Path) -> None:
    """sidebar placements API 不接受多個未分組 section。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        "/api/sidebar/placements",
        json={
            "groups": [
                {"group_id": None, "target_ids": [first.id]},
                {"group_id": None, "target_ids": [second.id]},
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "排序資料不可包含重複群組區塊"


def test_flat_sidebar_order_api_rejects_when_targets_are_grouped(tmp_path: Path) -> None:
    """舊平面排序 API 不可在已有 group placement 時打平 sidebar 狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        group = app_context.services.sidebar_layout.create_group("已分組")
        app_context.services.sidebar_layout.save_placements(
            [(group.id, [first.id]), (None, [second.id])]
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        "/api/sidebar/order",
        json={"target_ids": [second.id, first.id]},
    )

    assert response.status_code == 400
    assert "已有群組排序狀態" in response.json()["detail"]
    with SqliteApplicationContext(db_path) as app_context:
        placements = app_context.repositories.sidebar_layout.list_placements()
    assert placements[first.id].sidebar_group_id == group.id


def test_sidebar_api_errors_use_safe_traditional_chinese_messages(
    tmp_path: Path,
) -> None:
    """sidebar API 錯誤回應不得暴露英文內部錯誤或 repository 細節。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        group = app_context.services.sidebar_layout.create_group("已分組")
        app_context.services.sidebar_layout.save_placements(
            [(group.id, [first.id]), (None, [second.id])]
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    invalid_json = client.post(
        "/api/sidebar/groups",
        content="{",
        headers={"content-type": "application/json"},
    )
    grouped_order = client.post(
        "/api/sidebar/order",
        json={"target_ids": [second.id, first.id]},
    )
    missing_group = client.patch(
        "/api/sidebar/groups/missing",
        json={"name": "新名稱"},
    )

    assert invalid_json.status_code == 400
    assert invalid_json.json()["detail"] == "JSON 格式不正確"
    assert grouped_order.status_code == 400
    assert grouped_order.json()["detail"] == "已有群組排序狀態，請使用調整順序後的確認保存"
    assert missing_group.status_code == 404
    assert missing_group.json()["detail"] == "找不到指定的 sidebar 群組"


def test_sidebar_group_template_route_saves_json_config_payload(
    tmp_path: Path,
) -> None:
    """sidebar template JSON route 要沿用 TargetConfigForm 的設定語義。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        group = app_context.services.sidebar_layout.create_group("模板群組")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.put(
        f"/api/sidebar/groups/{group.id}/template",
        json={
            "include_keywords": "票,交換",
            "include_keywords_2": "5/1;5/2",
            "include_keywords_3": "108;109",
            "exclude_keywords": "售完",
            "exclude_ignore_phrases": "全收,回收",
            "refresh_mode": "fixed",
            "fixed_refresh_sec": "90",
            "min_refresh_sec": "20",
            "max_refresh_sec": "40",
            "max_items_per_scan": "30",
            "auto_load_more": True,
            "auto_adjust_sort": False,
            "enable_desktop_notification": True,
            "enable_ntfy": True,
            "ntfy_topic": "topic",
            "enable_discord_notification": True,
            "discord_webhook": "https://discord.com/api/webhooks/1234567890/sidebar_token",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "group_id": group.id}
    with SqliteApplicationContext(db_path) as app_context:
        template = app_context.repositories.sidebar_layout.get_template(group.id)
    assert template is not None
    assert template.include_keywords == ("票", "交換", "5/1", "5/2", "108", "109")
    assert [group.keywords for group in template.include_keyword_groups] == [
        ("票", "交換"),
        ("5/1", "5/2"),
        ("108", "109"),
    ]
    assert template.exclude_keywords == ("售完",)
    assert template.exclude_ignore_phrases == ("全收", "回收")
    assert template.fixed_refresh_sec == 90
    assert not template.jitter_enabled
    assert template.min_refresh_sec == 20
    assert template.max_refresh_sec == 40
    assert template.max_items_per_scan == 10
    assert template.auto_load_more
    assert not template.auto_adjust_sort
    assert template.enable_desktop_notification
    assert template.enable_ntfy
    assert template.ntfy_topic == "topic"
    assert template.enable_discord_notification
    assert template.discord_webhook == "https://discord.com/api/webhooks/1234567890/sidebar_token"


def test_sidebar_group_template_route_rejects_invalid_discord_webhook_without_overwrite(
    tmp_path: Path,
) -> None:
    """sidebar template JSON 也要套用 Discord webhook allowlist。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        group = app_context.services.sidebar_layout.create_group("模板群組")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    valid_response = client.put(
        f"/api/sidebar/groups/{group.id}/template",
        json={
            "enable_discord_notification": True,
            "discord_webhook": "https://discord.com/api/webhooks/1234567890/sidebar_token",
        },
    )
    with SqliteApplicationContext(db_path) as app_context:
        before = app_context.repositories.sidebar_layout.get_template(group.id)
    invalid_response = client.put(
        f"/api/sidebar/groups/{group.id}/template",
        json={
            "enable_discord_notification": True,
            "discord_webhook": "https://example.com/api/webhooks/123/token",
        },
    )

    assert valid_response.status_code == 200
    assert invalid_response.status_code == 400
    assert invalid_response.json()["detail"] == "Discord webhook 必須是 Discord 官方 webhook URL"
    with SqliteApplicationContext(db_path) as app_context:
        after = app_context.repositories.sidebar_layout.get_template(group.id)
    assert after == before


def test_sidebar_group_template_route_preserves_json_coercion_defaults(
    tmp_path: Path,
) -> None:
    """JSON template parser 的既有 fallback 與 truthy checkbox 行為不得漂移。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        group = app_context.services.sidebar_layout.create_group("模板群組")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.put(
        f"/api/sidebar/groups/{group.id}/template",
        json={
            "fixed_refresh_sec": "not-an-int",
            "min_refresh_sec": "bad",
            "max_refresh_sec": None,
            "max_items_per_scan": "bad",
            "auto_load_more": "false",
        },
    )

    assert response.status_code == 200
    with SqliteApplicationContext(db_path) as app_context:
        template = app_context.repositories.sidebar_layout.get_template(group.id)
    assert template is not None
    assert template.fixed_refresh_sec is None
    assert template.jitter_enabled
    assert template.min_refresh_sec == PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec
    assert template.max_refresh_sec == PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec
    assert template.max_items_per_scan == PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan
    assert template.auto_load_more
    assert not template.auto_adjust_sort
    assert not template.enable_desktop_notification
    assert not template.enable_ntfy
    assert template.ntfy_topic == ""
    assert not template.enable_discord_notification
    assert template.discord_webhook == ""


def test_sidebar_group_template_route_rejects_invalid_range_without_overwrite(
    tmp_path: Path,
) -> None:
    """sidebar template range 驗證失敗時，不得覆蓋既有模板。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        group = app_context.services.sidebar_layout.create_group("模板群組")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    valid_response = client.put(
        f"/api/sidebar/groups/{group.id}/template",
        json={
            "include_keywords": "原本",
            "refresh_mode": "fixed",
            "fixed_refresh_sec": "90",
            "max_items_per_scan": "5",
        },
    )
    with SqliteApplicationContext(db_path) as app_context:
        before = app_context.repositories.sidebar_layout.get_template(group.id)
    invalid_response = client.put(
        f"/api/sidebar/groups/{group.id}/template",
        json={
            "include_keywords": "改掉",
            "refresh_mode": "floating",
            "min_refresh_sec": "50",
            "max_refresh_sec": "20",
            "max_items_per_scan": "5",
        },
    )

    assert valid_response.status_code == 200
    assert invalid_response.status_code == 400
    with SqliteApplicationContext(db_path) as app_context:
        after = app_context.repositories.sidebar_layout.get_template(group.id)
    assert after == before


def test_sidebar_group_template_route_rejects_unknown_refresh_mode_without_overwrite(
    tmp_path: Path,
) -> None:
    """sidebar template 未知 refresh mode 不得 fallback 後覆蓋既有模板。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        group = app_context.services.sidebar_layout.create_group("模板群組")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    valid_response = client.put(
        f"/api/sidebar/groups/{group.id}/template",
        json={
            "include_keywords": "原本",
            "refresh_mode": "fixed",
            "fixed_refresh_sec": "90",
            "max_items_per_scan": "5",
        },
    )
    with SqliteApplicationContext(db_path) as app_context:
        before = app_context.repositories.sidebar_layout.get_template(group.id)
    invalid_response = client.put(
        f"/api/sidebar/groups/{group.id}/template",
        json={
            "include_keywords": "改掉",
            "refresh_mode": "unexpected",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
    )

    assert valid_response.status_code == 200
    assert invalid_response.status_code == 400
    with SqliteApplicationContext(db_path) as app_context:
        after = app_context.repositories.sidebar_layout.get_template(group.id)
    assert after == before
