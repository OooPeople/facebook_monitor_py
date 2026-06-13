"""FastAPI Web UI tests。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import TargetConfig


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


def test_flat_sidebar_order_api_is_removed(tmp_path: Path) -> None:
    """舊平面 target order API 已移除；正式保存只走 layout API。"""

    db_path = tmp_path / "app.db"
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post("/api/sidebar/order", json={"target_ids": []})

    assert response.status_code == 404


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
    duplicate_layout = client.post(
        "/api/sidebar/layout",
        json={
            "group_ids": [group.id],
            "groups": [
                {"group_id": group.id, "target_ids": [first.id]},
                {"group_id": group.id, "target_ids": [second.id]},
            ],
        },
    )
    missing_group = client.patch(
        "/api/sidebar/groups/missing",
        json={"name": "新名稱"},
    )

    assert invalid_json.status_code == 400
    assert invalid_json.json()["detail"] == "JSON 格式不正確"
    assert duplicate_layout.status_code == 400
    assert duplicate_layout.json()["detail"] == "排序資料不可包含重複群組區塊"
    assert missing_group.status_code == 404
    assert missing_group.json()["detail"] == "找不到指定的 sidebar 群組"


def test_sidebar_group_update_requires_json_boolean_collapsed(tmp_path: Path) -> None:
    """sidebar collapsed 只接受 JSON boolean，避免字串 false 被當成 true。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        group = app_context.services.sidebar_layout.create_group("可收合")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    true_response = client.patch(
        f"/api/sidebar/groups/{group.id}",
        json={"collapsed": True},
    )
    false_response = client.patch(
        f"/api/sidebar/groups/{group.id}",
        json={"collapsed": False},
    )
    string_response = client.patch(
        f"/api/sidebar/groups/{group.id}",
        json={"collapsed": "false"},
    )
    numeric_response = client.patch(
        f"/api/sidebar/groups/{group.id}",
        json={"collapsed": 0},
    )
    null_response = client.patch(
        f"/api/sidebar/groups/{group.id}",
        json={"collapsed": None},
    )

    assert true_response.status_code == 200
    assert true_response.json()["collapsed"] is True
    assert false_response.status_code == 200
    assert false_response.json()["collapsed"] is False
    assert string_response.status_code == 400
    assert string_response.json()["detail"] == "collapsed 必須是布林值"
    assert numeric_response.status_code == 400
    assert numeric_response.json()["detail"] == "collapsed 必須是布林值"
    assert null_response.status_code == 400
    assert null_response.json()["detail"] == "collapsed 必須是布林值"
    with SqliteApplicationContext(db_path) as app_context:
        saved_group = app_context.repositories.sidebar_layout.get_group(group.id)
    assert saved_group is not None
    assert saved_group.collapsed is False


def test_sidebar_group_update_rejects_mixed_invalid_payload_without_partial_write(
    tmp_path: Path,
) -> None:
    """name 與 collapsed 同時更新時，型別錯誤不得先寫入部分欄位。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        group = app_context.services.sidebar_layout.create_group("原本群組")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.patch(
        f"/api/sidebar/groups/{group.id}",
        json={"name": "不應寫入", "collapsed": "false"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "collapsed 必須是布林值"
    with SqliteApplicationContext(db_path) as app_context:
        saved_group = app_context.repositories.sidebar_layout.get_group(group.id)
    assert saved_group is not None
    assert saved_group.name == "原本群組"
    assert saved_group.collapsed is False


def test_sidebar_group_update_rejects_invalid_name_without_partial_collapsed_write(
    tmp_path: Path,
) -> None:
    """name 驗證失敗時，不得先寫入同一 payload 的 collapsed。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        group = app_context.services.sidebar_layout.create_group("原本群組")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.patch(
        f"/api/sidebar/groups/{group.id}",
        json={"name": "  ", "collapsed": True},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "群組名稱不可空白"
    with SqliteApplicationContext(db_path) as app_context:
        saved_group = app_context.repositories.sidebar_layout.get_group(group.id)
    assert saved_group is not None
    assert saved_group.name == "原本群組"
    assert saved_group.collapsed is False


def test_sidebar_group_update_accepts_valid_mixed_payload_and_preserves_omissions(
    tmp_path: Path,
) -> None:
    """valid mixed PATCH 可同時更新，未帶 collapsed 時不改原值。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        group = app_context.services.sidebar_layout.create_group("原本群組")
        app_context.services.sidebar_layout.set_group_collapsed(group.id, True)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    mixed_response = client.patch(
        f"/api/sidebar/groups/{group.id}",
        json={"name": "新群組", "collapsed": False},
    )
    name_only_response = client.patch(
        f"/api/sidebar/groups/{group.id}",
        json={"name": "只改名稱"},
    )

    assert mixed_response.status_code == 200
    assert mixed_response.json()["name"] == "新群組"
    assert mixed_response.json()["collapsed"] is False
    assert name_only_response.status_code == 200
    assert name_only_response.json()["name"] == "只改名稱"
    assert name_only_response.json()["collapsed"] is False


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


def test_sidebar_group_template_apply_rejects_empty_sections_without_overwrite(
    tmp_path: Path,
) -> None:
    """template apply 的空 sections 必須拒絕，不可 fallback 成套用 all。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        group = app_context.services.sidebar_layout.create_group("模板群組")
        app_context.services.sidebar_layout.save_placements([(group.id, [target.id])])
        app_context.repositories.configs.save_for_target_id(
            target.id,
            TargetConfig(
                target_id=target.id,
                include_keywords=("原本",),
                auto_load_more=False,
                auto_adjust_sort=False,
            ),
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    valid_response = client.put(
        f"/api/sidebar/groups/{group.id}/template",
        json={
            "include_keywords": "改掉",
            "auto_load_more": True,
            "auto_adjust_sort": True,
        },
    )
    with SqliteApplicationContext(db_path) as app_context:
        before = app_context.repositories.configs.get_for_target_id(target.id)
    invalid_response = client.post(
        f"/api/sidebar/groups/{group.id}/template/apply",
        json={"sections": []},
    )

    assert valid_response.status_code == 200
    assert invalid_response.status_code == 400
    assert invalid_response.json()["detail"] == "至少需要選擇一個套用區段"
    with SqliteApplicationContext(db_path) as app_context:
        after = app_context.repositories.configs.get_for_target_id(target.id)
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
