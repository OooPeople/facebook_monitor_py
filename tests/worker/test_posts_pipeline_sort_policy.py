"""Group posts worker tests。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.posts_pipeline import scan_posts_page
from tests.worker.posts_pipeline_test_helpers import _activate_target
from tests.worker.posts_pipeline_test_helpers import FakePage
from tests.worker.posts_pipeline_test_helpers import MissingSortControlFakePage
from tests.worker.posts_pipeline_test_helpers import UnconfirmedSortFakePage


def test_scan_posts_page_records_sort_adjust_result(tmp_path: Path) -> None:
    """auto_adjust_sort 開啟時會在掃描前嘗試切到新貼文並保存診斷。"""

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
        config = replace(config, auto_adjust_sort=True)

        scan_posts_page(
            page=fake_page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=0,
            scroll_wait_ms=0,
        )
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)

        assert fake_page.sort_adjusted
        assert latest_scan is not None
        assert latest_scan.metadata["sort_adjust"] == {
            "attempted": True,
            "changed": True,
            "preferred_label": "新貼文",
            "before_label": "最相關",
            "after_label": "新貼文",
            "reason": "updated_to_preferred_sort",
            "mutation_suppression_ms": 3200,
            "mutation_suppression_reason": "auto_adjust_sort",
            "menu_candidate_texts": [],
        }


def test_scan_posts_page_allows_missing_sort_control(tmp_path: Path) -> None:
    """沒有排序控制欄位的社團仍應繼續掃描並保存排序診斷。"""

    db_path = tmp_path / "app.db"
    page = MissingSortControlFakePage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    auto_adjust_sort=True,
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        summary = scan_posts_page(
            page=page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=0,
            scroll_wait_ms=0,
        )
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)

    assert page.sort_adjusted
    assert summary.item_count == 2
    assert summary.matched_count == 1
    assert len(latest_items) == 2
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.SUCCESS
    assert latest_scan.item_count == 2
    assert "scan_skipped" not in latest_scan.metadata
    assert latest_scan.metadata["sort_adjust"] == {
        "attempted": False,
        "changed": False,
        "preferred_label": "新貼文",
        "before_label": "",
        "after_label": "",
        "reason": "sort_control_not_found",
        "mutation_suppression_ms": 0,
        "mutation_suppression_reason": "",
        "menu_candidate_texts": [],
    }


def test_scan_posts_page_skips_when_sort_adjust_is_unconfirmed(tmp_path: Path) -> None:
    """auto_adjust_sort 未確認新貼文排序時不污染 seen/history/latest/notification。"""

    db_path = tmp_path / "app.db"
    page = UnconfirmedSortFakePage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    auto_adjust_sort=True,
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        app.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=99,
                    item_kind=ItemKind.POST,
                    item_key="previous-post",
                    item_index=0,
                    author="舊作者",
                    text="上一輪貼文",
                    permalink="https://www.facebook.com/groups/222518561920110/posts/prev",
                )
            ],
        )
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        summary = scan_posts_page(
            page=page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=3,
            scroll_wait_ms=0,
        )
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)
        history = app.repositories.match_history.list_by_target(target.id)
        notifications = app.repositories.notification_events.list_by_target(target.id)

    assert page.sort_adjusted
    assert summary.item_count == 0
    assert summary.new_count == 0
    assert summary.matched_count == 0
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.SUCCESS
    assert latest_scan.item_count == 0
    assert latest_scan.matched_count == 0
    assert latest_scan.metadata["scan_skipped"] is True
    assert latest_scan.metadata["skip_reason"] == "sort_adjust_unconfirmed"
    assert latest_scan.metadata["stop_reason"] == "sort_adjust_unconfirmed_skip"
    assert latest_scan.metadata["sort_adjust"]["after_label"] == "最相關"
    assert latest_scan.metadata["sort_adjust"]["preferred_label"] == "新貼文"
    assert latest_items == []
    assert history == []
    assert notifications == []


def test_scan_posts_page_escalates_third_sort_unconfirmed(tmp_path: Path) -> None:
    """posts 排序未確認連續三輪後，升級交給 scan failure policy。"""

    db_path = tmp_path / "app.db"
    page = UnconfirmedSortFakePage()
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

        scan_posts_page(
            page=page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=3,
            scroll_wait_ms=0,
        )
        scan_posts_page(
            page=page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=3,
            scroll_wait_ms=0,
        )

        with pytest.raises(WorkerFailure) as excinfo:
            scan_posts_page(
                page=page,
                app=app,
                target=target,
                config=config,
                scroll_rounds=3,
                scroll_wait_ms=0,
            )

        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        scan_count = app.repositories.scan_runs.connection.execute(
            "SELECT COUNT(*) FROM scan_runs WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]

    assert excinfo.value.reason == "sort_adjust_unconfirmed"
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.SUCCESS
    assert latest_scan.metadata["skip_streak"] == 2
    assert scan_count == 2
