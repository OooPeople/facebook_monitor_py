"""Group posts worker tests。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path


from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import utc_now
from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.facebook.extracted_item import make_item_key_aliases
from facebook_monitor.facebook.sort_results import FEED_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_results import SortAdjustResult
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.worker.posts_pipeline import build_feed_seen_stop_predicate
from facebook_monitor.worker.posts_pipeline import scan_posts_page
from tests.worker.posts_pipeline_test_helpers import _activate_target
from tests.worker.posts_pipeline_test_helpers import build_post_payload
from tests.worker.posts_pipeline_test_helpers import mark_post_payload_seen
from tests.worker.posts_pipeline_test_helpers import MissingSortControlSeenStopFakePage
from tests.worker.posts_pipeline_test_helpers import SeenStopFakePage
from tests.worker.posts_pipeline_test_helpers import table_count


def test_scan_posts_page_stops_after_four_consecutive_seen_posts(
    tmp_path: Path,
) -> None:
    """feed seen-stop 從最上方開始，連續四篇 seen 即跳過深度掃描。"""

    db_path = tmp_path / "app.db"
    fake_page = SeenStopFakePage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    max_items_per_scan=10,
                    auto_load_more=True,
                    auto_adjust_sort=True,
                ),
            )
        )
        target = _activate_target(app, target)
        for payload in fake_page.items[:4]:
            mark_post_payload_seen(app, scope_id=target.scope_id, payload=payload)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        summary = scan_posts_page(
            page=fake_page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=5,
            scroll_wait_ms=0,
        )
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

        assert summary.item_count == 4
        assert summary.new_count == 0
        assert fake_page.scroll_count == 0
        assert latest_scan is not None
        assert latest_scan.metadata["stop_reason"] == "seen_stop_consecutive_seen"
        assert latest_scan.metadata["collected_meta"]["seenStopTriggered"] is True
        assert latest_scan.metadata["collected_meta"]["seenStopThreshold"] == 4
        assert latest_scan.metadata["collected_meta"]["seenStopSeenCount"] == 4
        assert latest_scan.metadata["collected_meta"]["seenStopNewCount"] == 0


def test_feed_seen_stop_logical_read_does_not_create_epoch_state(
    tmp_path: Path,
) -> None:
    """seen-stop 的 logical 查詢是唯讀；缺 epoch row 時不應補寫狀態。"""

    db_path = tmp_path / "app.db"
    payload = build_post_payload("10000001", "舊貼文 1")
    item = ExtractedItem(
        text=str(payload["text"]),
        text_length=int(payload["textLength"]),
        permalink=str(payload["permalink"]),
        link_count=int(payload["linkCount"]),
        author=str(payload["author"]),
    )
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    max_items_per_scan=10,
                    auto_load_more=True,
                    auto_adjust_sort=True,
                ),
            )
        )
        target = _activate_target(app, target)
        mark_post_payload_seen(app, scope_id=target.scope_id, payload=payload)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None
        connection = app.repositories.seen_items.connection
        connection.commit()
        assert table_count(connection, "target_dedupe_state") == 0

        predicate = build_feed_seen_stop_predicate(
            app=app,
            target=target,
            config=config,
            scroll_rounds=5,
            sort_adjust_result=SortAdjustResult(
                attempted=True,
                changed=True,
                after_label=FEED_SORT_NEWEST_LABEL,
            ),
        )
        assert predicate is not None
        assert predicate(make_item_key_aliases(item))

        assert table_count(connection, "target_dedupe_state") == 0
        assert not connection.in_transaction


def test_feed_seen_stop_ignores_legacy_seen_outside_retention_horizon(
    tmp_path: Path,
) -> None:
    """legacy seen fallback 超過 bounded horizon 時不應觸發 feed seen-stop。"""

    db_path = tmp_path / "app.db"
    payload = build_post_payload("10000001", "舊貼文 1")
    item = ExtractedItem(
        text=str(payload["text"]),
        text_length=int(payload["textLength"]),
        permalink=str(payload["permalink"]),
        link_count=int(payload["linkCount"]),
        author=str(payload["author"]),
    )
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    max_items_per_scan=10,
                    auto_load_more=True,
                    auto_adjust_sort=True,
                ),
            )
        )
        target = _activate_target(app, target)
        mark_post_payload_seen(app, scope_id=target.scope_id, payload=payload)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None
        connection = app.repositories.seen_items.connection
        connection.execute(
            """
            UPDATE seen_items
            SET last_seen_at = ?
            """,
            (encode_datetime(utc_now() - timedelta(days=61)),),
        )
        connection.commit()

        predicate = build_feed_seen_stop_predicate(
            app=app,
            target=target,
            config=config,
            scroll_rounds=5,
            sort_adjust_result=SortAdjustResult(
                attempted=True,
                changed=True,
                after_label=FEED_SORT_NEWEST_LABEL,
            ),
        )
        assert predicate is not None

        assert not predicate(make_item_key_aliases(item))
        assert table_count(connection, "target_dedupe_state") == 0
        assert not connection.in_transaction


def test_scan_posts_page_keeps_seen_stop_when_sort_control_is_absent(
    tmp_path: Path,
) -> None:
    """posts 找不到排序控制但已放行掃描時，仍保留 seen-stop 防止深度掃描。"""

    db_path = tmp_path / "app.db"
    fake_page = MissingSortControlSeenStopFakePage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    max_items_per_scan=10,
                    auto_load_more=True,
                    auto_adjust_sort=True,
                ),
            )
        )
        target = _activate_target(app, target)
        for payload in fake_page.items[:4]:
            mark_post_payload_seen(app, scope_id=target.scope_id, payload=payload)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        summary = scan_posts_page(
            page=fake_page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=5,
            scroll_wait_ms=0,
        )
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

        assert fake_page.sort_adjusted
        assert summary.item_count == 4
        assert summary.new_count == 0
        assert fake_page.scroll_count == 0
        assert latest_scan is not None
        assert latest_scan.metadata["stop_reason"] == "seen_stop_consecutive_seen"
        assert latest_scan.metadata["sort_adjust"]["reason"] == "sort_control_not_found"
        assert latest_scan.metadata["collected_meta"]["seenStopEnabled"] is True
        assert latest_scan.metadata["collected_meta"]["seenStopTriggered"] is True


def test_seen_stop_latest_scan_snapshot_carries_previous_items_to_target_count(
    tmp_path: Path,
) -> None:
    """seen-stop 提早停止時，最近掃描沿用上一輪項目補足可檢視清單。"""

    db_path = tmp_path / "app.db"
    fake_page = SeenStopFakePage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    max_items_per_scan=5,
                    auto_load_more=True,
                    auto_adjust_sort=True,
                ),
            )
        )
        target = _activate_target(app, target)
        for payload in fake_page.items[:4]:
            mark_post_payload_seen(app, scope_id=target.scope_id, payload=payload)
        app.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=99,
                    item_kind=ItemKind.POST,
                    item_key=f"previous-{index}",
                    item_index=index,
                    author="作者",
                    text=f"上一輪貼文 {index}",
                    permalink=f"https://www.facebook.com/groups/222518561920110/posts/prev-{index}",
                )
                for index in range(5)
            ],
        )
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        summary = scan_posts_page(
            page=fake_page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=5,
            scroll_wait_ms=0,
        )
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)

        assert summary.item_count == 4
        assert len(latest_items) == 5
        assert latest_items[0].text == "舊貼文 1"
        assert latest_items[4].text == "上一輪貼文 0"
        assert latest_items[4].scan_run_id == summary.scan_run_id
        assert latest_items[4].debug_metadata["carriedOverFromPreviousScan"] is True
        assert latest_items[4].debug_metadata["carriedOverFromScanRunId"] == 99
