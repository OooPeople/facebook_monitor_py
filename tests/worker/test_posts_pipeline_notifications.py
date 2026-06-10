"""Group posts worker tests。"""

from __future__ import annotations

from pathlib import Path


from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import TargetConfigPatch
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.notifications.desktop import DesktopNotificationResult
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import DiscordResult
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.worker.posts_pipeline import scan_posts_page
from tests.worker.posts_pipeline_test_helpers import _activate_target
from tests.worker.posts_pipeline_test_helpers import FakePage


def test_scan_posts_page_sends_ntfy_for_new_match(tmp_path: Path) -> None:
    """啟用 ntfy 時，新命中的貼文會送通知並記錄 notification event。"""

    db_path = tmp_path / "app.db"
    sent_payloads: list[tuple[NtfyConfig, str, str]] = []

    def fake_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """記錄通知 payload，避免測試真的呼叫 ntfy。"""

        sent_payloads.append((config, title, message))
        return NtfyResult(ok=True, status_code=200, message="sent")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="(3) 測試社團 | Facebook",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        first_summary = scan_posts_page(
            page=FakePage(),
            app=app,
            target=target,
            config=config,
            scroll_rounds=0,
            scroll_wait_ms=0,
            notification_sender=fake_sender,
        )
        second_summary = scan_posts_page(
            page=FakePage(),
            app=app,
            target=target,
            config=config,
            scroll_rounds=0,
            scroll_wait_ms=0,
            notification_sender=fake_sender,
        )

        assert first_summary.new_count == 2
        assert second_summary.new_count == 0
        assert app.repositories.notification_events.list_by_target(target.id) == []

    with SqliteApplicationContext(db_path) as app:
        events = app.repositories.notification_events.list_by_target(target.id)

        assert len(sent_payloads) == 1
        assert sent_payloads[0][0].topic == "phase0test"
        assert sent_payloads[0][0].click_url == (
            "https://www.facebook.com/groups/222518561920110/posts/1"
        )
        assert sent_payloads[0][1] == "🎯 Facebook keyword match"
        assert "命中：票券" in sent_payloads[0][2]
        assert "測試社團" in sent_payloads[0][2]
        assert "(3) 測試社團" not in sent_payloads[0][2]
        assert "類型：貼文" in sent_payloads[0][2]
        assert "王小明" in sent_payloads[0][2]
        assert len(events) == 1
        assert events[0].status == NotificationStatus.SENT


def test_scan_posts_page_records_failed_ntfy_event(tmp_path: Path) -> None:
    """ntfy 發送失敗時會記錄 failed notification event。"""

    db_path = tmp_path / "app.db"

    def fake_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """回傳失敗結果，避免測試真的呼叫 ntfy。"""

        return NtfyResult(ok=False, status_code=None, message="network failed")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        scan_posts_page(
            page=FakePage(),
            app=app,
            target=target,
            config=config,
            scroll_rounds=0,
            scroll_wait_ms=0,
            notification_sender=fake_sender,
        )

    with SqliteApplicationContext(db_path) as app:
        events = app.repositories.notification_events.list_by_target(target.id)
        assert len(events) == 1
        assert events[0].status == NotificationStatus.FAILED
        assert events[0].message == "network failed"


def test_scan_posts_page_records_skipped_ntfy_when_topic_is_empty(
    tmp_path: Path,
) -> None:
    """ntfy 啟用但 topic 空白時記錄 skipped 而非 failed。"""

    db_path = tmp_path / "app.db"
    sent_payloads: list[tuple[NtfyConfig, str, str]] = []

    def fake_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """topic 空白時不應真的呼叫 sender。"""

        sent_payloads.append((config, title, message))
        return NtfyResult(ok=True, status_code=200, message="sent")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        scan_posts_page(
            page=FakePage(),
            app=app,
            target=target,
            config=config,
            scroll_rounds=0,
            scroll_wait_ms=0,
            notification_sender=fake_sender,
        )

    with SqliteApplicationContext(db_path) as app:
        events = app.repositories.notification_events.list_by_target(target.id)
        assert sent_payloads == []
        assert len(events) == 1
        assert events[0].status == NotificationStatus.SKIPPED
        assert events[0].message == "ntfy_skipped"


def test_scan_posts_page_records_all_enabled_notification_channels(
    tmp_path: Path,
) -> None:
    """posts pipeline 會透過 outbox 記錄所有已啟用通知通道的發送結果。"""

    db_path = tmp_path / "app.db"
    sent_payloads: list[tuple[NtfyConfig, str, str]] = []
    desktop_payloads: list[tuple[str, str]] = []
    discord_payloads: list[tuple[DiscordConfig, str, str]] = []

    def fake_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """記錄 ntfy payload，避免測試真的呼叫 ntfy。"""

        sent_payloads.append((config, title, message))
        return NtfyResult(ok=True, status_code=200, message="sent")

    def fake_desktop_sender(title: str, message: str) -> DesktopNotificationResult:
        """記錄 desktop payload，避免測試真的叫 PowerShell。"""

        desktop_payloads.append((title, message))
        return DesktopNotificationResult(ok=True, status_code=None, message="desktop_sent")

    def fake_discord_sender(
        config: DiscordConfig,
        title: str,
        message: str,
    ) -> DiscordResult:
        """記錄 Discord payload，避免測試真的送 webhook。"""

        discord_payloads.append((config, title, message))
        return DiscordResult(ok=True, status_code=204, message="discord_sent")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_desktop_notification=True,
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                    enable_discord_notification=True,
                    discord_webhook="https://discord.com/api/webhooks/example",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        scan_posts_page(
            page=FakePage(),
            app=app,
            target=target,
            config=config,
            scroll_rounds=0,
            scroll_wait_ms=0,
            notification_sender=fake_sender,
            desktop_notification_sender=fake_desktop_sender,
            discord_notification_sender=fake_discord_sender,
        )

    with SqliteApplicationContext(db_path) as app:
        events = app.repositories.notification_events.list_by_target(target.id)
        assert len(sent_payloads) == 1
        assert len(desktop_payloads) == 1
        assert len(discord_payloads) == 1
        assert [(event.channel, event.status, event.message) for event in events] == [
            (
                NotificationChannel.DISCORD,
                NotificationStatus.SENT,
                "discord_sent",
            ),
            (NotificationChannel.NTFY, NotificationStatus.SENT, "sent"),
            (
                NotificationChannel.DESKTOP,
                NotificationStatus.SENT,
                "desktop_sent",
            ),
        ]
