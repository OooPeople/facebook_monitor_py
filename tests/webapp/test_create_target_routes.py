"""FastAPI Web UI tests。"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from pytest import MonkeyPatch
from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.input_limits import MAX_DISPLAY_NAME_LENGTH
from facebook_monitor.core.input_limits import MAX_NOTIFICATION_ENDPOINT_LENGTH
from facebook_monitor.core.input_limits import MAX_NTFY_TOPIC_LENGTH
from facebook_monitor.core.input_limits import MAX_TARGET_URL_LENGTH
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.facebook.group_metadata import GroupMetadata
from facebook_monitor.facebook.group_metadata import GroupMetadataError
from facebook_monitor.webapp.app import create_app as create_production_app
from facebook_monitor.webapp import dependencies as web_dependencies
from facebook_monitor.webapp.routes import target_create as target_create_routes
from facebook_monitor.webapp.scheduler_session import BackgroundSchedulerManager
from facebook_monitor.webapp.scheduler_session import SchedulerSessionOptions
from facebook_monitor.webapp.scheduler_session import SchedulerSessionState
from facebook_monitor.webapp.profile_session import ProfileSessionError
from tests.helpers.webapp import FakeSchedulerManager


from tests.webapp.app_test_helpers import create_app


def _input_tag(html: str, field_name: str) -> str:
    """取出測試頁面中指定欄位的 input tag。"""

    match = re.search(
        rf'<input\b(?=[^>]*name="{re.escape(field_name)}")[^>]*>',
        html,
        re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def test_create_target_route_uses_saved_keyword_defaults_when_fields_are_omitted(
    tmp_path: Path,
) -> None:
    """新增 target 表單沒有送關鍵字欄位時，route 會讀 DB 預設值。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=lambda _profile_dir, _url: "測試社團",
        )
    )
    client.post(
        "/settings/target-keywords",
        data={
            "exclude_keywords": "售完,暫停",
            "exclude_ignore_phrases": "全收,回收",
        },
        follow_redirects=False,
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "include_keywords": "票",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
            "auto_load_more": "on",
            "auto_adjust_sort": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
        assert target is not None
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.exclude_keywords == ("售完", "暫停")
    assert config.exclude_ignore_phrases == ("全收", "回收")


def test_create_target_route_stores_and_renders_group_cover_thumbnail(tmp_path: Path) -> None:
    """新增 target 時 metadata resolver 會一併保存並顯示社團封面縮圖。"""

    db_path = tmp_path / "app.db"
    cover_url = "https://scontent.xx.fbcdn.net/group-cover.jpg"
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=lambda _profile_dir, _url: GroupMetadata(
                group_name="測試社團",
                group_cover_image_url=cover_url,
            ),
        )
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )
    index_response = client.get("/")

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
    assert target is not None
    assert target.group_cover_image_url == cover_url
    assert index_response.status_code == 200
    assert f'<img src="{cover_url}" alt=""' in index_response.text
    assert 'referrerpolicy="no-referrer"' in index_response.text


def test_dashboard_does_not_render_unsafe_group_cover_thumbnail(tmp_path: Path) -> None:
    """舊 DB 或污染資料中的任意圖片 URL 不可出現在 dashboard HTML。"""

    db_path = tmp_path / "app.db"
    unsafe_url = "https://example.com/group-cover.jpg"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110/",
            )
        )
        app_context.repositories.targets.save(replace(target, group_cover_image_url=unsafe_url))

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert unsafe_url not in response.text


def test_dashboard_does_not_render_generic_facebook_logo_thumbnail(
    tmp_path: Path,
) -> None:
    """舊 DB 中的 Facebook 通用 logo 不可被當作社團縮圖顯示。"""

    db_path = tmp_path / "app.db"
    generic_logo = "https://static.facebook.com/images/logos/facebook_2x.png"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110/",
            )
        )
        app_context.repositories.targets.save(
            replace(target, group_cover_image_url=generic_logo)
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert generic_logo not in response.text


def test_dashboard_does_not_render_polluted_facebook_error_name(tmp_path: Path) -> None:
    """舊 DB 中的 Facebook 錯誤頁名稱不可出現在 dashboard HTML。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110/",
            )
        )
        app_context.repositories.targets.save(
            replace(
                target,
                name="Facebook | Error",
                group_name="Facebook | Error",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "Facebook | Error" not in response.text
    assert "222518561920110" in response.text


def test_create_target_route_adds_group_posts_target(tmp_path: Path) -> None:
    """Web UI 會依 Facebook group URL 自動建立 posts target 並補社團名稱。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=lambda _profile_dir, _url: "測試社團",
        )
    )

    form_response = client.get("/targets/new")
    create_response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "include_keywords": "票",
            "exclude_keywords": "售完",
            "fixed_refresh_sec": "75",
            "max_items_per_scan": "25",
            "auto_load_more": "on",
            "auto_adjust_sort": "on",
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "phase0test",
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/1234567890/example_token",
        },
        follow_redirects=False,
    )

    assert form_response.status_code == 200
    assert "Facebook group URL" in form_response.text
    assert (
        "https://www.facebook.com/groups/123456789 或 "
        "https://www.facebook.com/groups/123456789/posts/987654321"
    ) in form_response.text
    assert "自訂顯示名稱" in form_response.text
    assert "可留空，系統會嘗試使用社團名稱" in form_response.text
    assert 'class="new-target-advanced" data-new-target-advanced' in form_response.text
    assert 'class="new-target-advanced-summary"' in form_response.text
    assert "data-new-target-advanced-toggle" in form_response.text
    assert 'aria-controls="new-target-advanced-body"' in form_response.text
    assert (
        'class="collapse-toggle new-target-advanced-toggle-icon" aria-hidden="true"'
        in form_response.text
    )
    assert 'class="collapse-toggle-icon new-target-advanced-chevron"' in form_response.text
    assert 'id="new-target-advanced-body"' in form_response.text
    assert "data-new-target-advanced-body" in form_response.text
    assert "進階設定" in form_response.text
    assert "掃描設定" in form_response.text
    assert "刷新設定" in form_response.text
    assert "通知設定" in form_response.text
    assert "data-new-target-form" in form_response.text
    assert 'data-loading-text="建立中..."' in form_response.text
    assert "data-secret-input" in form_response.text
    assert f'maxlength="{MAX_TARGET_URL_LENGTH}"' in form_response.text
    assert f'maxlength="{MAX_DISPLAY_NAME_LENGTH}"' in form_response.text
    assert 'name="ntfy_topic" type="text"' in form_response.text
    assert f'maxlength="{MAX_NTFY_TOPIC_LENGTH}"' in form_response.text
    assert 'name="discord_webhook" type="text"' in form_response.text
    assert f'maxlength="{MAX_NOTIFICATION_ENDPOINT_LENGTH}"' in form_response.text
    assert form_response.text.index(
        'name="refresh_mode" type="radio" value="floating"'
    ) < form_response.text.index('name="refresh_mode" type="radio" value="fixed"')
    assert re.search(
        r'name="refresh_mode" type="radio" value="floating"[^>]*checked',
        form_response.text,
    )
    assert f'value="{PYTHON_TARGET_CONFIG_DEFAULTS.default_fixed_refresh_sec}"' in (
        form_response.text
    )
    assert f'value="{PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan}"' in form_response.text
    assert form_response.text.count('name="max_items_per_scan"') == 1
    assert form_response.text.count('name="auto_load_more"') == 1
    assert form_response.text.count('name="auto_adjust_sort"') == 1
    assert 'name="max_items_per_scan" type="hidden"' not in form_response.text
    assert 'name="auto_load_more" type="hidden"' not in form_response.text
    assert 'name="auto_adjust_sort" type="hidden"' not in form_response.text
    assert 'type="checkbox"' in _input_tag(form_response.text, "auto_load_more")
    assert "checked" in _input_tag(form_response.text, "auto_load_more")
    assert 'type="checkbox"' in _input_tag(form_response.text, "auto_adjust_sort")
    assert "checked" in _input_tag(form_response.text, "auto_adjust_sort")
    assert "Target kind" not in form_response.text
    assert create_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
        assert target is not None
        config = app_context.repositories.configs.get_for_target(target)
    assert target.group_name == "測試社團"
    assert target.name == "測試社團"
    assert config is not None
    assert config.include_keywords == ("票",)
    assert config.exclude_keywords == ("售完",)
    assert config.exclude_ignore_phrases == PYTHON_TARGET_CONFIG_DEFAULTS.exclude_ignore_phrases
    assert config.fixed_refresh_sec is None
    assert config.jitter_enabled
    assert config.min_refresh_sec == PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec
    assert config.max_refresh_sec == PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec
    assert config.max_items_per_scan == 10
    assert config.auto_load_more
    assert config.auto_adjust_sort
    assert config.enable_desktop_notification
    assert config.enable_ntfy
    assert config.ntfy_topic == "phase0test"
    assert config.enable_discord_notification
    assert config.discord_webhook == "https://discord.com/api/webhooks/1234567890/example_token"


def test_create_target_route_allows_disabling_default_scan_options(
    tmp_path: Path,
) -> None:
    """新增 target 進階掃描設定取消勾選時會明確保存關閉。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=lambda _profile_dir, _url: "測試社團",
        )
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "refresh_mode": "floating",
            "fixed_refresh_sec": "60",
            "min_refresh_sec": "20",
            "max_refresh_sec": "40",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
        assert target is not None
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.max_items_per_scan == 5
    assert not config.auto_load_more
    assert not config.auto_adjust_sort


def test_create_target_route_uses_blank_notification_defaults(
    tmp_path: Path,
) -> None:
    """新增 target 只使用表單與 target-scoped defaults，不讀任何全域通知設定。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=lambda _profile_dir, _url: "測試社團",
        )
    )

    form_response = client.get("/targets/new")
    create_response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "refresh_mode": "floating",
            "fixed_refresh_sec": "60",
            "min_refresh_sec": "20",
            "max_refresh_sec": "40",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert form_response.status_code == 200
    assert 'name="ntfy_topic_keep" type="hidden" value="on"' not in form_response.text
    assert "已設定；留空代表不變更" not in form_response.text
    assert create_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
        assert target is not None
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert not config.enable_ntfy
    assert config.ntfy_topic == ""
    assert not config.enable_discord_notification
    assert config.discord_webhook == ""


def test_create_target_route_supports_fixed_refresh_mode(tmp_path: Path) -> None:
    """新增 target 時 fixed refresh mode 也要走同一個 config form 語義。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=lambda _profile_dir, _url: "測試社團",
        )
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "include_keywords": "票",
            "refresh_mode": "fixed",
            "fixed_refresh_sec": "95",
            "min_refresh_sec": "20",
            "max_refresh_sec": "40",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
        assert target is not None
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.fixed_refresh_sec == 95
    assert not config.jitter_enabled
    assert config.min_refresh_sec == 20
    assert config.max_refresh_sec == 40


def test_create_target_route_rejects_invalid_floating_refresh_range_without_creating_target(
    tmp_path: Path,
) -> None:
    """新增 target 時 refresh range 驗證失敗不得留下半套 target。"""

    db_path = tmp_path / "app.db"
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "display_name": "測試 target",
            "refresh_mode": "floating",
            "fixed_refresh_sec": "60",
            "min_refresh_sec": "50",
            "max_refresh_sec": "20",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    with SqliteApplicationContext(db_path) as app_context:
        assert app_context.repositories.targets.list_all() == []


def test_create_target_route_rejects_oversized_url_without_creating_target(
    tmp_path: Path,
) -> None:
    """新增 target 時 URL 欄位不可無上限進入 route detection。"""

    db_path = tmp_path / "app.db"
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/" + ("1" * MAX_TARGET_URL_LENGTH),
            "display_name": "測試 target",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert f"Facebook URL 不可超過 {MAX_TARGET_URL_LENGTH} 個字元" in response.text
    with SqliteApplicationContext(db_path) as app_context:
        assert app_context.repositories.targets.list_all() == []


def test_create_target_route_preserves_form_body_after_csrf_validation(
    tmp_path: Path,
) -> None:
    """production CSRF middleware 讀 token 後，route 仍要讀得到 group_url。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_production_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            csrf_token="known-token",
            group_name_resolver=lambda _profile_dir, _url: "測試社團",
        )
    )

    response = client.post(
        "/targets",
        data={
            "csrf_token": "known-token",
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "display_name": "測試 target",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
            "auto_load_more": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" not in response.headers["location"]
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
    assert target is not None


def test_create_target_route_uses_custom_display_name_without_resolver(tmp_path: Path) -> None:
    """有填自訂顯示名稱時不需要自動解析 Facebook title。"""

    db_path = tmp_path / "app.db"

    def failing_resolver(_profile_dir: Path, _url: str) -> str:
        raise AssertionError("resolver should not be called")

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=failing_resolver,
        )
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "display_name": "我的票券社團",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "20",
            "auto_load_more": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
    assert target is not None
    assert target.name == "我的票券社團"
    assert target.group_name == ""
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.exclude_keywords == PYTHON_TARGET_CONFIG_DEFAULTS.exclude_keywords


def test_create_target_custom_display_name_while_scheduler_running_does_not_refresh_metadata(
    tmp_path: Path,
) -> None:
    """自訂名稱已完整承載顯示語義時，即使 scheduler running 也不排 metadata refresh。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    scheduler_manager.running = True

    def failing_resolver(_profile_dir: Path, _url: str) -> str:
        raise AssertionError("resolver should not run for custom display name")

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            group_name_resolver=failing_resolver,
        )
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "display_name": "我的自訂社團",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
    assert target is not None
    assert target.name == "我的自訂社團"
    assert target.metadata_status == TargetMetadataStatus.RESOLVED
    assert scheduler_manager.metadata_refresh_target_ids == []


def test_create_target_route_skips_name_resolver_when_scheduler_running(tmp_path: Path) -> None:
    """scheduler 正在跑時，新增 target 不應為了解析名稱而停止 scheduler。"""

    db_path = tmp_path / "app.db"
    resolver_calls: list[str] = []

    def failing_resolver(_profile_dir: Path, url: str) -> str:
        resolver_calls.append(url)
        raise AssertionError("resolver should not run while scheduler is running")

    scheduler_manager = BackgroundSchedulerManager(
        resident_main_runner=lambda _options, stop_event, _on_cycle, _sleep_fn=None: (
            stop_event.wait(timeout=2)
        )
    )
    scheduler_manager.start(
        SchedulerSessionOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
        )
    )
    try:
        client = TestClient(
            create_app(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                group_name_resolver=failing_resolver,
                scheduler_manager=scheduler_manager,
            )
        )

        response = client.post(
            "/targets",
            data={
                "group_url": "https://www.facebook.com/groups/222518561920110/",
                "fixed_refresh_sec": "60",
                "max_items_per_scan": "20",
                "auto_load_more": "on",
            },
            follow_redirects=False,
        )
    finally:
        scheduler_manager.stop(timeout_seconds=2)

    assert response.status_code == 303
    assert resolver_calls == []
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
    assert target is not None
    assert target.group_name == ""
    assert target.name == "group:222518561920110:posts"
    assert target.metadata_status == TargetMetadataStatus.PENDING
    assert target.metadata_error == ""
    assert scheduler_manager.take_metadata_refresh_requests() == (target.id,)


def test_create_permalink_comments_target_while_scheduler_running(tmp_path: Path) -> None:
    """scheduler 執行中仍可用 group permalink URL 建立 comments target。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = BackgroundSchedulerManager(
        resident_main_runner=lambda _options, stop_event, _on_cycle, _sleep_fn=None: (
            stop_event.wait(timeout=2)
        )
    )
    scheduler_manager.start(
        SchedulerSessionOptions(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
        )
    )
    try:
        client = TestClient(
            create_app(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                scheduler_manager=scheduler_manager,
            )
        )

        response = client.post(
            "/targets",
            data={
                "group_url": (
                    "https://www.facebook.com/groups/204808657039646/permalink/2155501991970293"
                ),
                "fixed_refresh_sec": "60",
                "max_items_per_scan": "5",
                "auto_load_more": "on",
            },
            follow_redirects=False,
        )
    finally:
        scheduler_manager.stop(timeout_seconds=2)

    assert response.status_code == 303
    assert "error=" not in response.headers["location"]
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.COMMENTS,
            scope_id="204808657039646:post:2155501991970293:comments",
        )
    assert target is not None
    assert target.canonical_url == (
        "https://www.facebook.com/groups/204808657039646/posts/2155501991970293"
    )
    assert target.metadata_status == TargetMetadataStatus.PENDING
    assert scheduler_manager.take_metadata_refresh_requests() == (target.id,)


def test_create_target_route_adds_comments_target_and_resolves_group_name(
    tmp_path: Path,
) -> None:
    """Web UI 會依單篇貼文 URL 自動建立 comments target 並補社團名稱。"""

    db_path = tmp_path / "app.db"
    resolver_calls: list[str] = []

    def fake_resolver(_profile_dir: Path, url: str) -> str:
        resolver_calls.append(url)
        return "留言測試社團"

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=fake_resolver,
        )
    )

    form_response = client.get("/targets/new")
    create_response = client.post(
        "/targets",
        data={
            "group_url": (
                "https://www.facebook.com/groups/222518561920110/posts/2187454285426518/"
                "?comment_id=123456789"
            ),
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
            "auto_load_more": "on",
        },
        follow_redirects=False,
    )

    assert form_response.status_code == 200
    assert "Target kind" not in form_response.text
    assert create_response.status_code == 303
    assert resolver_calls == ["https://www.facebook.com/groups/222518561920110"]
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.COMMENTS,
            scope_id="222518561920110:post:2187454285426518:comments",
        )
        assert target is not None
        config = app_context.repositories.configs.get_for_target(target)
        state = app_context.repositories.runtime_states.get(target.id)

    assert target.group_id == "222518561920110"
    assert target.parent_post_id == "2187454285426518"
    assert target.canonical_url == (
        "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
    )
    assert target.name == "留言測試社團 / post:2187454285426518"
    assert target.group_name == "留言測試社團"
    assert target.paused
    assert config is not None
    assert config.exclude_keywords == PYTHON_TARGET_CONFIG_DEFAULTS.exclude_keywords
    assert state is not None

    index_response = client.get("/")
    assert index_response.status_code == 200
    assert "留言測試社團" in index_response.text
    assert "留言模式" in index_response.text
    assert "下次刷新：未排程" in index_response.text
    assert "comments · group=222518561920110" not in index_response.text
    assert "parent_post=2187454285426518" not in index_response.text
    assert "scope=222518561920110:post:2187454285426518:comments" not in index_response.text
    assert "target_kind=comments" in index_response.text
    assert "已停止" in index_response.text
    assert "開始" in index_response.text
    assert "comments D3 已建立 sort/load-more" not in index_response.text


def test_create_target_metadata_resolver_captures_request_state_before_thread(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """metadata resolver thread action 不應在背景 thread 內讀取 Request state。"""

    db_path = tmp_path / "app.db"
    resolver_calls: list[tuple[Path, str]] = []

    def fake_resolver(profile_dir: Path, url: str) -> str:
        resolver_calls.append((profile_dir, url))
        return "背景解析社團"

    threadpool_call_count = 0

    async def guarded_threadpool(action, *args, **kwargs):
        nonlocal threadpool_call_count
        threadpool_call_count += 1

        def fail_getter(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("request-bound dependency was read inside thread action")

        if threadpool_call_count >= 2:
            monkeypatch.setattr(target_create_routes, "get_profile_dir", fail_getter)
            monkeypatch.setattr(target_create_routes, "get_group_name_resolver", fail_getter)
        return action(*args, **kwargs)

    monkeypatch.setattr(web_dependencies, "run_in_threadpool", guarded_threadpool)
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=fake_resolver,
        )
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert resolver_calls == [
        (
            tmp_path / "profile",
            "https://www.facebook.com/groups/222518561920110",
        )
    ]


def test_create_target_route_ignores_target_kind_form_field_and_detects_url(
    tmp_path: Path,
) -> None:
    """舊表單若仍送 target_kind，後端仍以 URL 自動判斷 target 類型。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            group_name_resolver=lambda _profile_dir, _url: "測試社團",
        )
    )

    response = client.post(
        "/targets",
        data={
            "target_kind": "comments",
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        posts_target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
        comments_target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.COMMENTS,
            scope_id="222518561920110:post::comments",
        )
    assert posts_target is not None
    assert comments_target is None


def test_create_target_uses_fallback_name_when_scheduler_running(
    tmp_path: Path,
) -> None:
    """背景掃描執行中時，新增 target 不為了解析社團名稱暫停 scheduler。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    scheduler_manager.running = True
    resolver_calls: list[str] = []

    def fake_resolver(_profile_dir: Path, url: str) -> str:
        resolver_calls.append(url)
        return "測試社團"

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            group_name_resolver=fake_resolver,
        )
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
            "auto_load_more": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert resolver_calls == []
    assert scheduler_manager.stopped_count == 0
    assert scheduler_manager.started_count == 0
    assert scheduler_manager.running
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
    assert target is not None
    assert target.name == "group:222518561920110:posts"
    assert target.metadata_status == TargetMetadataStatus.PENDING
    assert scheduler_manager.metadata_refresh_target_ids == [target.id]


def test_create_target_defer_metadata_refresh_when_scheduler_starts_mid_flow(
    tmp_path: Path,
) -> None:
    """plan 建立後 scheduler 若開始執行，resolver skip 仍需補排 metadata refresh。"""

    class RacingSchedulerManager(FakeSchedulerManager):
        def __init__(self) -> None:
            super().__init__()
            self.state_calls = 0

        def state(self) -> SchedulerSessionState:
            self.state_calls += 1
            self.running = self.state_calls >= 2
            return super().state()

    db_path = tmp_path / "app.db"
    scheduler_manager = RacingSchedulerManager()
    resolver_calls: list[str] = []

    def fake_resolver(_profile_dir: Path, url: str) -> str:
        resolver_calls.append(url)
        return "測試社團"

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            group_name_resolver=fake_resolver,
        )
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert resolver_calls == []
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
    assert target is not None
    assert target.name == "group:222518561920110:posts"
    assert target.metadata_status == TargetMetadataStatus.PENDING
    assert scheduler_manager.metadata_refresh_target_ids == [target.id]


def test_create_target_defer_metadata_refresh_when_resolver_fails(
    tmp_path: Path,
) -> None:
    """metadata resolver 失敗不應讓 target 建立失敗，需改由 resident 後續補齊。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()

    def failing_resolver(_profile_dir: Path, _url: str) -> str:
        raise GroupMetadataError("Facebook 尚未登入，請稍後重試")

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            group_name_resolver=failing_resolver,
        )
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" not in response.headers["location"]
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
    assert target is not None
    assert target.name == "group:222518561920110:posts"
    assert target.metadata_status == TargetMetadataStatus.PENDING
    assert scheduler_manager.metadata_refresh_target_ids == [target.id]


def test_create_target_defer_metadata_refresh_when_profile_session_is_busy(
    tmp_path: Path,
) -> None:
    """profile session 忙碌時仍可建立 target，metadata 交給 resident 補齊。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()

    def busy_resolver(_profile_dir: Path, _url: str) -> str:
        raise ProfileSessionError("profile session is busy")

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            group_name_resolver=busy_resolver,
        )
    )

    response = client.post(
        "/targets",
        data={
            "group_url": "https://www.facebook.com/groups/222518561920110/",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" not in response.headers["location"]
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.repositories.targets.find_by_kind_scope(
            target_kind=TargetKind.POSTS,
            scope_id="222518561920110",
        )
    assert target is not None
    assert target.name == "group:222518561920110:posts"
    assert target.metadata_status == TargetMetadataStatus.PENDING
    assert scheduler_manager.metadata_refresh_target_ids == [target.id]
