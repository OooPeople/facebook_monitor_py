"""Phase B FastAPI web UI tests。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import CreateCommentsTargetRequest
from facebook_monitor.application.services import CreateGroupPostsTargetRequest
from facebook_monitor.application.services import RecordScanRequest
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetKind
from facebook_monitor.notifications.desktop import DesktopNotificationResult
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import DiscordResult
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.webapp.app import create_app
from facebook_monitor.webapp.app import parse_keywords_text
from facebook_monitor.webapp.profile_session import ProfileSessionOptions
from facebook_monitor.webapp.scheduler_session import AutoScanMode
from facebook_monitor.webapp.scheduler_session import SchedulerSessionState


class FakeProfileManager:
    """測試用 profile manager，避免 Web UI route 測試真的開 Playwright。"""

    def __init__(self) -> None:
        self.active = False
        self.options: ProfileSessionOptions | None = None

    def is_active(self) -> bool:
        """回傳 fake profile 視窗是否開啟。"""

        return self.active

    def open(self, options: ProfileSessionOptions) -> None:
        """保存設定並標記 fake profile 視窗已開啟。"""

        self.options = options
        self.active = True

    def close(self) -> None:
        """關閉 fake profile 視窗。"""

        self.active = False


class FakeSchedulerManager:
    """測試用 scheduler manager，避免 Web UI route 測試真的跑背景掃描。"""

    def __init__(self) -> None:
        self.running = False
        self.started_count = 0
        self.stopped_count = 0
        self.woken_count = 0
        self.options: object | None = None

    def state(self) -> SchedulerSessionState:
        """回傳 fake scheduler 狀態。"""

        return SchedulerSessionState(
            running=self.running,
            interval_seconds=60,
            auto_scan_mode=(
                self.options.auto_scan_mode
                if self.options is not None
                else AutoScanMode.RESIDENT
            ),
            last_cycle_at="",
            last_error="",
            max_concurrent_scans=2,
            current_running_count=1 if self.running else 0,
            current_queued_count=0,
            queue_length=0,
            worker_ids=("resident-slot-1", "resident-slot-2") if self.running else (),
            page_pool_size=1 if self.running else 0,
            last_opened_page_count=1 if self.running else 0,
            last_reused_page_count=2 if self.running else 0,
            last_closed_page_count=0,
            resident_browser_alive=self.running,
        )

    def is_running(self) -> bool:
        """回傳 fake scheduler 是否執行中。"""

        return self.running

    def start(self, options: object) -> None:
        """標記 fake scheduler 已啟動。"""

        self.started_count += 1
        self.options = options
        self.running = True

    def stop(self) -> None:
        """標記 fake scheduler 已停止。"""

        self.stopped_count += 1
        self.running = False

    def wake(self) -> None:
        """記錄 manual-start 喚醒要求。"""

        self.woken_count += 1


def test_parse_keywords_text_dedupes_and_trims() -> None:
    """Web UI keyword parser 會去除空白與重複值。"""

    assert parse_keywords_text("票, 交換,票,,讓票") == ("票", "交換", "讓票")


def test_index_renders_target_rows(tmp_path: Path) -> None:
    """首頁會顯示已保存 target，並清理 Facebook title 前置通知數。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="(3) 測試社團",
            )
        )
        target = app_context.repositories.targets.find_by_kind_scope(
            TargetKind.POSTS,
            "222518561920110",
        )
        assert target is not None
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message="page_load_timeout: timeout",
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                item_count=2,
                matched_count=1,
                metadata={
                    "worker": "phase_b_group_posts_once",
                    "collection_strategy": "feed_visible_window",
                    "new_count": 1,
                    "matched_count": 1,
                    "target_count": 5,
                    "scanned_count": 2,
                    "candidate_count": 2,
                    "round_count": 1,
                    "scroll_rounds": 0,
                    "scroll_wait_ms": 0,
                    "stop_reason": "scroll_rounds_completed",
                    "rounds": [
                        {
                            "round_index": 0,
                            "raw_item_count": 2,
                            "unique_item_count": 2,
                            "scroll_y": 0,
                            "scroll_height": 1200,
                        }
                    ],
                },
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="item-1",
                    item_index=0,
                    author="王小明",
                    text="這是一篇有票券關鍵字的貼文",
                    permalink="https://www.facebook.com/groups/222518561920110/posts/1",
                    matched_keyword="票券",
                    debug_metadata={
                        "textSource": "primary",
                        "permalinkSource": "container:groups_post_anchor",
                        "expandCount": 1,
                    },
                ),
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="item-2",
                    item_index=1,
                    author="陳小華",
                    text="這是一篇普通貼文",
                    permalink="",
                    matched_keyword="",
                ),
            ],
        )
        app_context.repositories.notification_events.add(
            NotificationEvent(
                target_id=target.id,
                item_key="item-1",
                channel=NotificationChannel.NTFY,
                status=NotificationStatus.SENT,
                message="sent",
            )
        )

    scheduler_manager = FakeSchedulerManager()
    scheduler_manager.running = True
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.get("/")

    assert response.status_code == 200
    assert "測試社團" in response.text
    assert "(3) 測試社團" not in response.text
    assert "222518561920110" in response.text
    assert "已啟用 · idle" in response.text
    assert "最近掃描" in response.text
    assert "items=2 · new=1 · matched=1" in response.text
    assert "掃描診斷" in response.text
    assert "rounds=1 · candidates=2 · stop=完成捲動輪數" in response.text
    assert "collection_strategy=feed_visible_window" in response.text
    assert "round=0 raw=2 unique=2" in response.text
    assert "複製掃描診斷" in response.text
    assert "最近錯誤" in response.text
    assert "最近掃描貼文" in response.text
    assert "最近通知" in response.text
    assert "ntfy: sent" in response.text
    assert "背景掃描服務 · 執行中 · 常駐" in response.text
    assert (
        "running=1 · queued=0 · slots=2 · pages=1 · opened=1 · reused=2 · closed=0 "
        "· browser=alive"
    ) in response.text
    assert "啟動自動掃描" not in response.text
    assert "停止自動掃描" not in response.text
    assert "王小明" in response.text
    assert "命中: 票券" in response.text
    assert "陳小華" in response.text
    assert "未命中" in response.text
    assert "未取得連結" in response.text
    assert "這是一篇有票券關鍵字的貼文" in response.text
    assert "除錯" in response.text
    assert "複製除錯資訊" in response.text
    assert "textSource=primary" in response.text
    assert "expandCount=1" in response.text
    assert "監視中" not in response.text
    assert "掃描一次" not in response.text


def test_index_renders_runtime_state_and_error(tmp_path: Path) -> None:
    """首頁會顯示 scheduler runtime state 與 last error。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        running_target = app_context.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="執行中社團",
            )
        )
        error_target = app_context.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
                group_name="錯誤社團",
            )
        )
        stopped_target = app_context.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="333",
                canonical_url="https://www.facebook.com/groups/333",
                group_name="停止社團",
            )
        )
        app_context.services.targets.mark_target_running(running_target.id, "worker-1")
        app_context.services.targets.mark_target_error(error_target.id, "login_required: 需要登入")
        app_context.services.targets.stop_target(stopped_target.id)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "已啟用 · running" in response.text
    assert "已啟用 · error" in response.text
    assert "login_required: 需要登入" in response.text
    assert "已停止" in response.text


def test_index_renders_scan_guard_skip_reason(tmp_path: Path) -> None:
    """首頁會顯示同 target 重入被 guard 擋下的原因。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="重入測試社團",
            )
        )
        app_context.services.targets.mark_target_running(target.id, "worker-a")
        locked_state = app_context.services.targets.try_mark_target_running(
            target.id,
            "worker-b",
        )

    assert locked_state is None

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "重入測試社團" in response.text
    assert "scan_guard_skipped: target_already_running" in response.text
    assert "active_worker_id=worker-a" in response.text


def test_update_config_route_updates_target_config(tmp_path: Path) -> None:
    """設定表單送出後會更新 target config。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "include_keywords": "票,交換",
            "exclude_keywords": "售完",
            "fixed_refresh_sec": "90",
            "max_items_per_scan": "30",
            "auto_adjust_sort": "on",
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "phase0test",
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/example",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.include_keywords == ("票", "交換")
    assert config.exclude_keywords == ("售完",)
    assert config.fixed_refresh_sec == 90
    assert config.max_items_per_scan == 10
    assert not config.auto_load_more
    assert config.auto_adjust_sort
    assert config.enable_desktop_notification
    assert config.enable_ntfy
    assert config.ntfy_topic == "phase0test"
    assert config.enable_discord_notification
    assert config.discord_webhook == "https://discord.com/api/webhooks/example"


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
            "discord_webhook": "https://discord.com/api/webhooks/example",
        },
        follow_redirects=False,
    )

    assert form_response.status_code == 200
    assert "Facebook group URL" in form_response.text
    assert "自訂顯示名稱" in form_response.text
    assert f'value="{PYTHON_TARGET_CONFIG_DEFAULTS.fixed_refresh_sec}"' in form_response.text
    assert f'value="{PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan}"' in form_response.text
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
    assert config.fixed_refresh_sec == 75
    assert config.max_items_per_scan == 10
    assert config.auto_load_more
    assert config.auto_adjust_sort
    assert config.enable_desktop_notification
    assert config.enable_ntfy
    assert config.ntfy_topic == "phase0test"
    assert config.enable_discord_notification
    assert config.discord_webhook == "https://discord.com/api/webhooks/example"


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
    assert target.name == "留言測試社團"
    assert target.group_name == "留言測試社團"
    assert target.paused
    assert config is not None
    assert state is not None

    index_response = client.get("/")
    assert index_response.status_code == 200
    assert "留言測試社團" in index_response.text
    assert "comments · group=222518561920110" in index_response.text
    assert "parent_post=2187454285426518" in index_response.text
    assert "scope=222518561920110:post:2187454285426518:comments" in index_response.text
    assert "target_kind=comments" in index_response.text
    assert "已停止" in index_response.text
    assert "開始" in index_response.text
    assert "comments D3 已建立 sort/load-more" not in index_response.text


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


def test_settings_routes_control_profile_window(tmp_path: Path) -> None:
    """設定頁可開啟與關閉 Facebook automation profile 視窗。"""

    db_path = tmp_path / "app.db"
    profile_manager = FakeProfileManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            profile_manager=profile_manager,
        )
    )

    settings_response = client.get("/settings")
    open_response = client.post("/settings/facebook/open", follow_redirects=False)
    active_index_response = client.get("/")

    assert settings_response.status_code == 200
    assert "Facebook automation profile" in settings_response.text
    assert open_response.status_code == 303
    assert profile_manager.active
    assert "設定 · 開啟中" in active_index_response.text
    close_response = client.post("/settings/facebook/close", follow_redirects=False)
    assert close_response.status_code == 303
    assert not profile_manager.active


def test_settings_updates_tests_and_applies_global_notifications(tmp_path: Path) -> None:
    """設定頁可保存通知預設值、送測試通知，並批次套用到 target。"""

    db_path = tmp_path / "app.db"
    sent: list[str] = []

    def fake_desktop_sender(title: str, message: str) -> DesktopNotificationResult:
        """記錄桌面測試通知。"""

        sent.append(f"desktop:{title}:{message}")
        return DesktopNotificationResult(ok=True, status_code=None, message="desktop_sent")

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """記錄 ntfy 測試通知。"""

        sent.append(f"ntfy:{config.topic}:{title}:{message}")
        return NtfyResult(ok=True, status_code=200, message="sent")

    def fake_discord_sender(
        config: DiscordConfig,
        title: str,
        message: str,
    ) -> DiscordResult:
        """記錄 Discord 測試通知。"""

        sent.append(f"discord:{config.webhook_url}:{title}:{message}")
        return DiscordResult(ok=True, status_code=204, message="discord_sent")

    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            desktop_sender=fake_desktop_sender,
            ntfy_sender=fake_ntfy_sender,
            discord_sender=fake_discord_sender,
        )
    )
    settings_page = client.get("/settings")
    save_response = client.post(
        "/settings/notifications",
        data={
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "phase0test",
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/example",
        },
        follow_redirects=False,
    )
    form_response = client.get("/targets/new")
    test_response = client.post(
        "/settings/notifications/test",
        follow_redirects=True,
    )
    apply_response = client.post(
        "/settings/notifications/apply-to-targets",
        follow_redirects=False,
    )

    assert save_response.status_code == 303
    assert "通知預設值" in settings_page.text
    assert "未填寫也不影響功能" in settings_page.text
    assert form_response.status_code == 200
    assert "value=\"phase0test\"" in form_response.text
    assert "https://discord.com/api/webhooks/example" in form_response.text
    assert test_response.status_code == 200
    assert "desktop_sent / ntfy_sent / discord_sent" in test_response.text
    assert any(item.startswith("desktop:") for item in sent)
    assert any(item.startswith("ntfy:phase0test:") for item in sent)
    assert any(item.startswith("discord:https://discord.com/api/webhooks/example:") for item in sent)
    assert apply_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert config.enable_desktop_notification
    assert config.enable_ntfy
    assert config.ntfy_topic == "phase0test"
    assert config.enable_discord_notification
    assert config.discord_webhook == "https://discord.com/api/webhooks/example"


def test_settings_open_pauses_scheduler_until_profile_closes(tmp_path: Path) -> None:
    """設定頁開 profile 時會由 Web UI 內部暫停並在關閉後恢復 scheduler。"""

    db_path = tmp_path / "app.db"
    profile_manager = FakeProfileManager()
    scheduler_manager = FakeSchedulerManager()
    scheduler_manager.running = True
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            profile_manager=profile_manager,
            scheduler_manager=scheduler_manager,
        )
    )

    response = client.post("/settings/facebook/open", follow_redirects=False)

    assert response.status_code == 303
    assert profile_manager.active
    assert scheduler_manager.stopped_count == 1
    assert not scheduler_manager.running

    close_response = client.post("/settings/facebook/close", follow_redirects=False)

    assert close_response.status_code == 303
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.running


def test_create_target_temporarily_pauses_scheduler_for_auto_name_resolve(
    tmp_path: Path,
) -> None:
    """背景掃描執行中時，新增 target 會短暫暫停 scheduler 再解析社團名稱。"""

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
    assert resolver_calls == ["https://www.facebook.com/groups/222518561920110"]
    assert scheduler_manager.stopped_count == 1
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.running


def test_scheduler_routes_control_background_scan(tmp_path: Path) -> None:
    """Web UI 可啟停內建背景 scheduler，不需要第二個 terminal。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )

    start_response = client.post(
        "/scheduler/start",
        data={"auto_scan_mode": "one_shot"},
        follow_redirects=False,
    )
    index_response = client.get("/")
    stop_response = client.post("/scheduler/stop", follow_redirects=False)

    assert start_response.status_code == 303
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.options is not None
    assert scheduler_manager.options.auto_scan_mode == AutoScanMode.ONE_SHOT
    assert "背景掃描服務 · 執行中 · 一次性" in index_response.text
    assert "啟動自動掃描" not in index_response.text
    assert "停止自動掃描" not in index_response.text
    assert stop_response.status_code == 303
    assert scheduler_manager.stopped_count == 1
    assert not scheduler_manager.running


def test_webui_startup_resets_targets_to_stopped(tmp_path: Path) -> None:
    """正式 Web UI 啟動時會先把既有 target 停止，等待使用者手動開始。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                fixed_refresh_sec=None,
            )
        )

    with TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            reset_targets_on_startup=True,
        )
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "已停止" in response.text
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.runtime_states.get(target.id)
        config = app_context.repositories.configs.get_for_target(target)
    assert loaded is not None
    assert loaded.paused
    assert state is not None
    assert state.desired_state.value == "stopped"
    assert config is not None
    assert config.fixed_refresh_sec == 60


def test_webui_startup_can_clear_runtime_debug_data(tmp_path: Path) -> None:
    """Web UI 啟動時可清除上一輪 runtime/debug data，保留 target 設定。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="seen-before-startup",
                item_kind=ItemKind.POST,
            )
        )
        scan_run_id = app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                item_count=1,
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=scan_run_id,
                    item_kind=ItemKind.POST,
                    item_key="seen-before-startup",
                    item_index=0,
                )
            ],
        )
        app_context.repositories.notification_events.add(
            NotificationEvent(
                target_id=target.id,
                item_key="seen-before-startup",
                channel=NotificationChannel.NTFY,
                status=NotificationStatus.SENT,
                message="sent",
            )
        )

    with TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            reset_runtime_data_on_startup=True,
        )
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        config = app_context.repositories.configs.get_for_target(target)
        latest_scan = app_context.repositories.scan_runs.latest_by_target(target.id)
        latest_items = app_context.repositories.latest_scan_items.list_by_target(target.id)
        notifications = app_context.repositories.notification_events.list_by_target(target.id)
        has_seen = app_context.repositories.seen_items.has_seen(
            target.scope_id,
            "seen-before-startup",
        )

    assert loaded is not None
    assert config is not None
    assert latest_scan is None
    assert latest_items == []
    assert notifications == []
    assert not has_seen


def test_start_and_stop_routes_update_target_status(tmp_path: Path) -> None:
    """Web UI 開始/停止 route 對齊 restart/pause 語義。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="seen-before-start",
                item_kind=ItemKind.POST,
            )
        )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    stop_response = client.post(f"/targets/{target.id}/stop", follow_redirects=False)
    start_response = client.post(
        f"/targets/{target.id}/start",
        data={"return_to": f"#target-{target.id}"},
        follow_redirects=False,
    )

    assert stop_response.status_code == 303
    assert start_response.status_code == 303
    assert start_response.headers["location"].endswith(f"#target-{target.id}")
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.runtime_states.get(target.id)
        has_seen = app_context.repositories.seen_items.has_seen(
            target.scope_id,
            "seen-before-start",
        )
    assert loaded is not None
    assert loaded.enabled
    assert not loaded.paused
    assert state is not None
    assert state.scan_requested_at is not None
    assert not has_seen
    assert scheduler_manager.woken_count == 2


def test_start_route_supports_comments_target(tmp_path: Path) -> None:
    """Web UI comments target 的開始 route 會清 comments seen 並喚醒 scheduler。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_comments_target(
            CreateCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="comment-before-start",
                item_kind=ItemKind.COMMENT,
            )
        )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(
        f"/targets/{target.id}/start",
        data={"return_to": f"#target-{target.id}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.runtime_states.get(target.id)
        has_seen = app_context.repositories.seen_items.has_seen(
            target.scope_id,
            "comment-before-start",
        )
    assert loaded is not None
    assert not loaded.paused
    assert state is not None
    assert state.scan_requested_at is not None
    assert not has_seen
    assert scheduler_manager.woken_count == 1


def test_dashboard_revision_endpoint_changes_after_target_update(tmp_path: Path) -> None:
    """dashboard revision endpoint 只在資料有變更時供前端刷新。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    first_revision = client.get("/api/dashboard-revision").json()["revision"]
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "return_to": f"#target-{target.id}",
            "include_keywords": "票券",
            "exclude_keywords": "",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )
    second_revision = client.get("/api/dashboard-revision").json()["revision"]

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"#target-{target.id}")
    assert first_revision != second_revision


def test_index_shows_latest_items_up_to_target_max_items(tmp_path: Path) -> None:
    """右側最近掃描項目顯示上限會跟 target max_items_per_scan 對齊。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                max_items_per_scan=7,
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key=f"item-{index}",
                    item_index=index,
                    author=f"作者 {index}",
                    text=f"貼文 {index}",
                )
                for index in range(7)
            ],
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "作者 0" in response.text
    assert "作者 6" in response.text


def test_delete_route_removes_only_selected_target(tmp_path: Path) -> None:
    """Web UI 刪除 route 只刪除指定 target。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            CreateGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app_context.services.targets.stop_target(second.id)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(f"/targets/{first.id}/delete", follow_redirects=False)

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        assert app_context.repositories.targets.get(first.id) is None
        loaded_second = app_context.repositories.targets.get(second.id)
    assert loaded_second is not None
    assert loaded_second.enabled
    assert loaded_second.paused

