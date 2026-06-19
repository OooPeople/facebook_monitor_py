"""Group posts worker tests。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.posts_pipeline import scan_posts_page_sync_and_finalize
from tests.worker.posts_pipeline_test_helpers import _activate_target
from tests.worker.posts_pipeline_test_helpers import ContentUnavailablePostsPage
from tests.worker.posts_pipeline_test_helpers import FakePage
from tests.worker.posts_pipeline_test_helpers import GrowingFakePage


def test_scan_posts_page_sync_and_finalize_records_seen_match_and_scan(
    tmp_path: Path,
) -> None:
    """單輪掃描會寫入 seen、match history 與 scan run。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(include_keywords=("票券",)),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        fake_page = FakePage()
        summary = scan_posts_page_sync_and_finalize(
            page=fake_page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=0,
            scroll_wait_ms=0,
        )
        history = app.repositories.match_history.list_by_target(target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

        assert summary.item_count == 2
        assert summary.new_count == 2
        assert summary.matched_count == 1
        assert len(history) == 1
        assert history[0].include_rule == "票券"
        assert history[0].author == "王小明"
        assert len(latest_items) == 2
        assert latest_items[0].author == "王小明"
        assert latest_items[0].matched_keyword == "票券"
        assert latest_items[1].author == "陳小華"
        assert latest_items[1].matched_keyword == ""
        assert latest_scan is not None
        assert latest_scan.metadata["new_count"] == 2
        assert latest_scan.metadata["matched_count"] == 1
        assert latest_scan.metadata["target_count"] == 5
        assert latest_scan.metadata["round_count"] == 1
        assert latest_scan.metadata["candidate_count"] == 2
        assert latest_scan.metadata["collected_meta"]["targetCount"] == 5
        assert latest_scan.metadata["collected_meta"]["mode"] == "off"
        assert latest_scan.metadata["collected_meta"]["loadMoreMode"] == "off"
        assert latest_scan.metadata["collected_meta"]["beforeCount"] == 2
        assert fake_page.extract_limits == [30]
        assert latest_scan.metadata["auto_load_more"]
        assert latest_scan.metadata["scroll_collection_enabled"] is False
        assert fake_page.sort_adjusted
        assert latest_scan.metadata["sort_adjust"]["reason"] == "updated_to_preferred_sort"
        assert latest_scan.metadata["stop_reason"] == "scroll_rounds_completed"
        assert latest_scan.metadata["rounds"] == [
            {
                "round_index": 0,
                "raw_item_count": 2,
                "unique_item_count": 2,
                "scroll_y": 0,
                "scroll_height": 1200,
            }
        ]
        assert app.repositories.notification_events.list_by_target(target.id) == []


def test_scan_posts_page_sync_and_finalize_records_all_matched_keywords(
    tmp_path: Path,
) -> None:
    """同一貼文命中多組 include 規則時，history/latest scan 會保留全部命中。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(include_keywords=("6/5;6/6",)),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        fake_page = FakePage(
            items=[
                {
                    "text": "售6/5,6/6的票各一張",
                    "textLength": 14,
                    "permalink": "https://www.facebook.com/groups/222518561920110/posts/1",
                    "linkCount": 1,
                    "author": "王小明",
                }
            ]
        )
        summary = scan_posts_page_sync_and_finalize(
            page=fake_page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=0,
            scroll_wait_ms=0,
        )
        history = app.repositories.match_history.list_by_target(target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)

        assert summary.matched_count == 1
        assert history[0].include_rule == "6/5;6/6"
        assert history[0].include_rules == ("6/5", "6/6")
        assert latest_items[0].matched_keyword == "6/5;6/6"
        assert latest_items[0].matched_keywords == ("6/5", "6/6")


def test_scan_posts_page_sync_and_finalize_honors_auto_load_more_config(
    tmp_path: Path,
) -> None:
    """auto_load_more 關閉時即使 CLI 傳多輪 scroll，也只掃目前可見視窗。"""

    db_path = tmp_path / "app.db"
    fake_page = FakePage()
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
        config = replace(config, auto_load_more=False)

        scan_posts_page_sync_and_finalize(
            page=fake_page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=3,
            scroll_wait_ms=0,
        )
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

        assert not fake_page.scrolled
        assert latest_scan is not None
        assert latest_scan.metadata["auto_load_more"] is False
        assert latest_scan.metadata["scroll_rounds"] == 0
        assert latest_scan.metadata["collection_strategy"] == "feed_visible_window"


def test_scan_posts_page_sync_and_finalize_uses_dynamic_window_limit_for_target_count(
    tmp_path: Path,
) -> None:
    """target_count 較高時以動態視窗上限補足掃描。"""

    db_path = tmp_path / "app.db"
    fake_page = GrowingFakePage(total_items=10, visible_count=1, grow_by=2)
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(max_items_per_scan=10),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        summary = scan_posts_page_sync_and_finalize(
            page=fake_page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=3,
            scroll_wait_ms=0,
        )
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

        assert summary.item_count == 10
        assert fake_page.scroll_count > 3
        assert set(fake_page.extract_limits) == {60}
        assert latest_scan is not None
        assert latest_scan.metadata["target_count"] == 10
        assert latest_scan.metadata["requested_scroll_rounds"] == 3
        assert latest_scan.metadata["scroll_rounds"] == 19
        assert latest_scan.metadata["max_window_count"] == 20
        assert latest_scan.metadata["stop_reason"] == "target_count_reached"
        assert latest_scan.metadata["collected_meta"]["targetCount"] == 10
        assert latest_scan.metadata["load_more_mode"] == "scroll"
        assert latest_scan.metadata["collected_meta"]["loadMoreMode"] == "scroll"
        assert latest_scan.metadata["collected_meta"]["maxWindowCount"] == 20
        assert latest_scan.metadata["collected_meta"]["attempts"] > 3
        assert latest_scan.metadata["collected_meta"]["accumulatedCount"] == 10


def test_scan_posts_page_sync_and_finalize_raises_content_unavailable_before_sort(
    tmp_path: Path,
) -> None:
    """內容不可見頁應分類成 target 失效，不應落到排序失敗。"""

    db_path = tmp_path / "app.db"
    page = ContentUnavailablePostsPage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(auto_adjust_sort=True),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        with pytest.raises(WorkerFailure) as exc_info:
            scan_posts_page_sync_and_finalize(
                page=page,
                app=app,
                target=target,
                config=config,
                scroll_rounds=0,
                scroll_wait_ms=0,
            )

    assert exc_info.value.reason == "content_unavailable"
    assert not page.sort_adjusted
