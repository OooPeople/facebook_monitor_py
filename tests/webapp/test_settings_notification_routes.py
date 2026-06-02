"""FastAPI Web UI tests。"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.requests import Request

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.refresh_policy import MIN_REFRESH_SECONDS
from facebook_monitor.core.scan_limits import MIN_TARGET_POSTS
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import DiscordResult
from facebook_monitor.runtime.paths import resolve_runtime_paths
from tests.helpers.webapp import FakeProfileManager
from tests.helpers.webapp import FakeSchedulerManager
from tests.helpers.notifications import NotificationRecorder


from tests.webapp.app_test_helpers import create_app
from tests.webapp.app_test_helpers import page_feedback


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
    assert "未開啟" not in settings_response.text
    assert "視窗開啟中" not in settings_response.text
    assert "關閉視窗" not in settings_response.text
    assert open_response.status_code == 303
    assert profile_manager.active
    assert "設定 · 開啟中" not in active_index_response.text
    close_response = client.post("/settings/facebook/close", follow_redirects=False)
    assert close_response.status_code == 303
    assert not profile_manager.active


def test_target_settings_modal_can_test_notifications_without_saving(
    tmp_path: Path,
) -> None:
    """target 設定 modal 的測試通知會使用表單值，但不保存 target 設定。"""

    db_path = tmp_path / "app.db"
    notifications = NotificationRecorder()

    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            desktop_sender=notifications.desktop_sender,
            ntfy_sender=notifications.ntfy_sender,
            discord_sender=notifications.discord_sender,
        )
    )
    index_response = client.get("/")
    test_response = client.post(
        f"/targets/{target.id}/notifications/test",
        data={
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "modal-topic",
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/1234567890/modal_token",
        },
        follow_redirects=True,
    )
    json_test_response = client.post(
        f"/targets/{target.id}/notifications/test",
        data={
            "enable_desktop_notification": "on",
            "enable_ntfy": "on",
            "ntfy_topic": "modal-topic-json",
        },
        headers={"Accept": "application/json"},
    )

    assert index_response.status_code == 200
    assert "掃描設定" in index_response.text
    assert "刷新設定" in index_response.text
    assert "通知設定" in index_response.text
    assert "測試通知" in index_response.text
    assert f'data-notification-test-action="/targets/{target.id}/notifications/test"' in (
        index_response.text
    )
    assert f'data-notification-test-form-id="config-{target.id}"' in index_response.text
    assert f'formaction="/targets/{target.id}/notifications/test"' not in index_response.text
    assert f'data-dirty-status-for="config-{target.id}"' in index_response.text
    assert "data-notification-test" in index_response.text
    assert "data-notification-test-status" in index_response.text
    assert f'form="config-{target.id}"' in index_response.text
    floating_refresh = re.search(
        rf'name="refresh_mode" type="radio" value="floating"[^>]*form="config-{re.escape(target.id)}"',
        index_response.text,
    )
    fixed_refresh = re.search(
        rf'name="refresh_mode" type="radio" value="fixed"[^>]*form="config-{re.escape(target.id)}"',
        index_response.text,
    )
    assert floating_refresh is not None
    assert fixed_refresh is not None
    assert floating_refresh.start() < fixed_refresh.start()
    assert re.search(
        rf'name="refresh_mode" type="radio" value="floating"[^>]*form="config-{re.escape(target.id)}"[^>]*checked',
        index_response.text,
    )
    assert fixed_refresh is not None
    assert (
        f'name="fixed_refresh_sec" type="number" min="{MIN_REFRESH_SECONDS}" '
        f'value="60" form="config-{target.id}"'
    ) in index_response.text
    assert f'name="max_items_per_scan" type="number" min="{MIN_TARGET_POSTS}"' in (
        index_response.text
    )
    assert test_response.status_code == 200
    assert (
        page_feedback(test_response.text)["message"]
        == "測試通知結果：桌面通知已送出 / ntfy 通知已送出 / Discord 通知已送出"
    )
    assert json_test_response.status_code == 200
    assert json_test_response.json()["ok"] is True
    assert json_test_response.json()["results"] == ["桌面通知已送出", "ntfy 通知已送出"]
    assert any(item.startswith("desktop:") for item in notifications.sent)
    assert any(item.startswith("ntfy:modal-topic:") for item in notifications.sent)
    assert any(
        item.startswith("discord:https://discord.com/api/webhooks/1234567890/modal_token:")
        for item in notifications.sent
    )
    with SqliteApplicationContext(db_path) as app_context:
        config = app_context.repositories.configs.get_for_target(target)
    assert config is not None
    assert not config.enable_desktop_notification
    assert not config.enable_ntfy
    assert config.ntfy_topic == ""
    assert not config.enable_discord_notification
    assert config.discord_webhook == ""


def test_target_notification_test_errors_are_sanitized(tmp_path: Path) -> None:
    """target 測試通知失敗時，UI 錯誤不得回填 webhook / topic。"""

    db_path = tmp_path / "app.db"

    def failing_discord_sender(
        config: DiscordConfig,
        _title: str,
        _message: str,
    ) -> DiscordResult:
        """模擬自訂 sender 例外內含 webhook。"""

        raise RuntimeError(f"failed {config.webhook_url}")

    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            discord_sender=failing_discord_sender,
        )
    )

    response = client.post(
        f"/targets/{target.id}/notifications/test",
        data={
            "enable_discord_notification": "on",
            "discord_webhook": "https://discord.com/api/webhooks/1234567890/private-token",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "通知測試發生錯誤" in response.text
    assert "notification_test_failed:RuntimeError" not in response.text
    assert "private-token" not in response.text


def test_settings_shows_failed_notification_clear_without_retry_action(
    tmp_path: Path,
) -> None:
    """Settings 頁只顯示失敗通知清除入口，不提供重試操作。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app_context.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:failed:desktop",
                target_id=target.id,
                item_key="failed",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.DESKTOP,
                title="測試標題",
                message="測試內容",
                status=NotificationOutboxStatus.FAILED,
                attempts=1,
                last_error="desktop_failed",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    settings_response = client.get("/settings")
    retry_response = client.post("/settings/notifications/retry-failed")

    assert settings_response.status_code == 200
    assert "有 1 筆通知發送失敗" in settings_response.text
    assert "清除失敗通知" in settings_response.text
    assert "通知 outbox" not in settings_response.text
    assert "待送" not in settings_response.text
    assert "最大嘗試" not in settings_response.text
    assert "重試失敗通知" not in settings_response.text
    assert "重試 failed 通知" not in settings_response.text
    assert retry_response.status_code == 404
    with SqliteApplicationContext(db_path) as app_context:
        entry = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{target.id}:failed:desktop",
        )
    assert entry is not None
    assert entry.status == NotificationOutboxStatus.FAILED


def test_removed_global_notification_routes_are_not_available(tmp_path: Path) -> None:
    """Settings 不再提供全域通知預設、測試或 failed retry 隱藏入口。"""

    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    for path in (
        "/settings/notifications",
        "/settings/notifications/apply-to-targets",
        "/settings/notifications/test",
        "/settings/notifications/retry-failed",
    ):
        response = client.post(path)
        assert response.status_code == 404


def test_settings_can_clear_failed_outbox_without_deleting_pending_rows(
    tmp_path: Path,
) -> None:
    """Settings 頁可清 failed outbox，但不誤刪 pending rows。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app_context.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:failed:desktop",
                target_id=target.id,
                item_key="failed",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.DESKTOP,
                title="失敗通知",
                message="失敗內容",
                status=NotificationOutboxStatus.FAILED,
                attempts=3,
                last_error="desktop_failed",
            )
        )
        app_context.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:pending:desktop",
                target_id=target.id,
                item_key="pending",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.DESKTOP,
                title="待送通知",
                message="待送內容",
                status=NotificationOutboxStatus.PENDING,
            )
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
        )
    )

    response = client.post(
        "/settings/notifications/clear-failed",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert page_feedback(response.text)["message"] == "已清除失敗通知 1 筆"
    with SqliteApplicationContext(db_path) as app_context:
        failed_entry = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{target.id}:failed:desktop",
        )
        pending_entry = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{target.id}:pending:desktop",
        )
    assert failed_entry is None
    assert pending_entry is not None
    assert pending_entry.status == NotificationOutboxStatus.PENDING


def test_settings_clear_failed_outbox_reports_zero_when_no_failed_rows(
    tmp_path: Path,
) -> None:
    """沒有 failed rows 時，清除操作仍回報 0 並保持可預期。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
        )
    )

    response = client.post(
        "/settings/notifications/clear-failed",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert page_feedback(response.text)["message"] == "已清除失敗通知 0 筆"


def test_settings_support_bundle_excludes_private_runtime_files(tmp_path: Path) -> None:
    """Support bundle 只包含 redacted 摘要，不打包 DB/profile/完整 logs/secrets。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.ensure_writable_dirs()
    (paths.logs_dir / "private.log").write_text("secret-token", encoding="utf-8")
    (paths.logs_dir / "app.log").write_text(
        "failed https://discord.com/api/webhooks/123456/private-token",
        encoding="utf-8",
    )
    (paths.profile_dir / "Cookies").write_text("cookie-secret", encoding="utf-8")
    with SqliteApplicationContext(paths.db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    client = TestClient(app)

    response = client.post("/settings/support-bundle")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        combined_text = "\n".join(archive.read(name).decode("utf-8") for name in sorted(names))
    assert {
        "README.txt",
        "metadata.json",
        "runtime_diagnostics.txt",
        "runtime_paths.json",
        "database_summary.json",
        "bundle_manifest.json",
        "log_tail.json",
    }.issubset(names)
    assert all(not name.endswith((".db", "Cookies", ".log", "secrets.key")) for name in names)
    assert "secret-token" not in combined_text
    assert "private-token" not in combined_text
    assert "cookie-secret" not in combined_text


def test_settings_open_pauses_scheduler_until_profile_window_ends(tmp_path: Path) -> None:
    """設定頁開 profile 時暫停 scheduler；視窗自行結束後會自動恢復。"""

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

    profile_manager.active = False
    assert profile_manager.options is not None
    assert profile_manager.options.on_close is not None
    profile_manager.options.on_close()

    assert scheduler_manager.started_count == 1
    assert scheduler_manager.running


def test_manual_scan_does_not_restart_scheduler_while_profile_window_is_active(
    tmp_path: Path,
) -> None:
    """profile 視窗開啟期間 manual scan 只排入 request，不重新搶 automation profile。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app_context.services.targets.restart_target_monitoring(target.id)

    profile_manager = FakeProfileManager()
    scheduler_manager = FakeSchedulerManager()
    scheduler_manager.running = True
    app = create_app(
        db_path=db_path,
        profile_dir=tmp_path / "profile",
        profile_manager=profile_manager,
        scheduler_manager=scheduler_manager,
    )
    client = TestClient(app)

    open_response = client.post("/settings/facebook/open", follow_redirects=False)
    scan_response = client.post(f"/targets/{target.id}/scan-once", follow_redirects=False)

    assert open_response.status_code == 303
    assert scan_response.status_code == 303
    assert profile_manager.active
    assert scheduler_manager.started_count == 0
    assert scheduler_manager.woken_count == 0
    with SqliteApplicationContext(db_path) as app_context:
        state = app_context.repositories.runtime_states.get(target.id)
    assert state is not None
    assert state.scan_requested_at is not None

    profile_manager.active = False
    assert profile_manager.options is not None
    assert profile_manager.options.on_close is not None
    closure_values = tuple(
        cell.cell_contents for cell in (profile_manager.options.on_close.__closure__ or ())
    )
    assert not any(isinstance(value, Request) for value in closure_values)
    profile_manager.options.on_close()

    assert scheduler_manager.started_count == 1
    assert scheduler_manager.running
    assert app.state.scheduler_paused_for_profile is False


def test_webui_shutdown_closes_active_profile_window(tmp_path: Path) -> None:
    """Web UI 關閉時會先收掉設定頁開出的 profile 視窗。"""

    db_path = tmp_path / "app.db"
    profile_manager = FakeProfileManager()
    scheduler_manager = FakeSchedulerManager()

    with TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            profile_manager=profile_manager,
            scheduler_manager=scheduler_manager,
        )
    ) as client:
        response = client.post("/settings/facebook/open", follow_redirects=False)
        assert response.status_code == 303
        assert profile_manager.active

    assert not profile_manager.active
    assert profile_manager.close_count == 1
    assert scheduler_manager.stopped_count == 1
