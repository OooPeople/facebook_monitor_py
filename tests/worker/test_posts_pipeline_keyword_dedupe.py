"""Group posts worker tests。"""

from __future__ import annotations

from pathlib import Path


from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import TargetConfigPatch
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.worker.posts_pipeline import scan_posts_page
from tests.worker.posts_pipeline_test_helpers import _activate_target
from tests.worker.posts_pipeline_test_helpers import FakePage


def test_scan_posts_page_supports_keyword_rules(tmp_path: Path) -> None:
    """worker 使用分號 OR、空白 AND 與 exclude 規則。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    include_keywords=("普通;票券 關鍵字",),
                    exclude_keywords=("普通",),
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        summary = scan_posts_page(
            page=FakePage(),
            app=app,
            target=target,
            config=config,
            scroll_rounds=0,
            scroll_wait_ms=0,
        )
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)

        assert summary.matched_count == 1
        assert latest_items[0].matched_keyword == "票券 關鍵字"
        assert latest_items[1].matched_keyword == ""


def test_scan_posts_page_empty_include_does_not_match(tmp_path: Path) -> None:
    """未設定 include 時不應產生命中或通知。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        summary = scan_posts_page(
            page=FakePage(),
            app=app,
            target=target,
            config=config,
            scroll_rounds=0,
            scroll_wait_ms=0,
        )
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)

        assert summary.matched_count == 0
        assert [item.matched_keyword for item in latest_items] == ["", ""]


def test_scan_posts_page_uses_key_aliases_to_prevent_duplicate_notification(
    tmp_path: Path,
) -> None:
    """同一貼文 permalink 與 fallback 抽取不一致時，不會重複通知。"""

    db_path = tmp_path / "app.db"
    sent_payloads: list[tuple[NtfyConfig, str, str]] = []

    def fake_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """記錄通知 payload，避免測試真的呼叫 ntfy。"""

        sent_payloads.append((config, title, message))
        return NtfyResult(ok=True, status_code=200, message="sent")

    first_items = [
        {
            "text": "這是一篇有票券關鍵字的貼文",
            "textLength": 14,
            "permalink": "https://www.facebook.com/groups/222518561920110/posts/1234567890",
            "linkCount": 1,
            "author": "王小明",
        }
    ]
    second_items = [
        {
            "text": "這是一篇有票券關鍵字的貼文",
            "textLength": 14,
            "permalink": "",
            "linkCount": 0,
            "author": "王小明",
        }
    ]

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

        first_summary = scan_posts_page(
            page=FakePage(first_items),
            app=app,
            target=target,
            config=config,
            scroll_rounds=0,
            scroll_wait_ms=0,
            notification_sender=fake_sender,
        )
        second_summary = scan_posts_page(
            page=FakePage(second_items),
            app=app,
            target=target,
            config=config,
            scroll_rounds=0,
            scroll_wait_ms=0,
            notification_sender=fake_sender,
        )

        assert first_summary.new_count == 1
        assert second_summary.new_count == 0

    with SqliteApplicationContext(db_path):
        assert len(sent_payloads) == 1
