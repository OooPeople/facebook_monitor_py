"""Group posts worker tests。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import asyncio

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.posts_pipeline import scan_posts_page_sync_and_finalize
from facebook_monitor.worker.posts_pipeline import scan_posts_page_async_commit_ready
from facebook_monitor.worker.scan_pipeline_results import ProtectiveSkipScanResult
from facebook_monitor.worker.scan_pipeline_results import SuccessScanResult
from tests.worker.posts_pipeline_test_helpers import _activate_target
from tests.worker.posts_pipeline_test_helpers import FakePage
from tests.worker.posts_pipeline_test_helpers import MissingSortControlFakePage
from tests.worker.posts_pipeline_test_helpers import UnconfirmedSortFakePage


class AsyncFakeLocator:
    """提供 async posts worker 測試需要的 body inner_text。"""

    async def inner_text(self, *, timeout: int) -> str:
        """回傳假頁面 body 文字。"""

        return "社團 feed 已登入"


class AsyncUnconfirmedSortFakePage:
    """模擬 async auto_adjust_sort 未能確認切到新貼文。"""

    url = "https://www.facebook.com/groups/222518561920110"

    def __init__(self) -> None:
        self.sort_adjusted = False

    def locator(self, selector: str) -> AsyncFakeLocator:
        """回傳 body locator。"""

        return AsyncFakeLocator()

    async def evaluate(self, script: str, *args: object) -> object:
        """排序未確認時不應繼續呼叫 extractor。"""

        if "preferredLabel" in script and "sort_control_not_found" in script:
            self.sort_adjusted = True
            return {
                "attempted": True,
                "changed": False,
                "preferredLabel": "新貼文",
                "beforeLabel": "最相關",
                "afterLabel": "最相關",
                "reason": "sort_update_unconfirmed",
                "mutationSuppressionMs": 3200,
                "mutationSuppressionReason": "auto_adjust_sort",
                "menuCandidateTexts": ["最相關", "新貼文"],
            }
        raise AssertionError("async sort-unconfirmed scan should skip before extractor")


class AsyncSuccessFakePage:
    """模擬 async posts scanner 成功抽取貼文。"""

    url = "https://www.facebook.com/groups/222518561920110"

    def __init__(self) -> None:
        self.sort_adjusted = False
        self.extract_limits: list[int] = []

    def locator(self, selector: str) -> AsyncFakeLocator:
        """回傳 body locator。"""

        return AsyncFakeLocator()

    async def evaluate(self, script: str, *args: object) -> object:
        """依 async extractor 呼叫型態回傳假 DOM 結果。"""

        if "preferredLabel" in script and "sort_control_not_found" in script:
            self.sort_adjusted = True
            return {
                "attempted": True,
                "changed": True,
                "preferredLabel": "新貼文",
                "beforeLabel": "最相關",
                "afterLabel": "新貼文",
                "reason": "updated_to_preferred_sort",
                "mutationSuppressionMs": 3200,
                "mutationSuppressionReason": "auto_adjust_sort",
            }
        if "document.querySelectorAll" in script:
            if args:
                limit = args[0]
                assert isinstance(limit, int)
                self.extract_limits.append(limit)
            return [
                {
                    "text": "這是一篇有票券關鍵字的貼文",
                    "textLength": 14,
                    "permalink": ("https://www.facebook.com/groups/222518561920110/posts/1"),
                    "linkCount": 1,
                    "author": "王小明",
                }
            ]
        if "scrollY" in script:
            return {
                "scrollY": 0,
                "scrollHeight": 1200,
                "scrollTargetLabel": "document.scrollingElement",
                "scrollTargetTop": 0,
                "scrollTargetClientHeight": 900,
                "scrollTargetMaxTop": 300,
            }
        if "scrollTargetBy" in script and "moved:" in script:
            return {
                "moved": False,
                "loadMoreMode": "scroll",
                "targetLabel": "document.scrollingElement",
                "beforeTop": 0,
                "afterTop": 0,
                "movedDistance": 0,
                "scrollStep": 900,
                "scrollHeight": 1200,
                "clientHeight": 900,
                "maxScrollTop": 300,
            }
        raise AssertionError(f"Unexpected async script: {script[:80]}")

    async def wait_for_timeout(self, milliseconds: int) -> None:
        """模擬 async 捲動等待。"""


def test_scan_posts_page_sync_and_finalize_records_sort_adjust_result(
    tmp_path: Path,
) -> None:
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

        scan_posts_page_sync_and_finalize(
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


def test_scan_posts_page_sync_and_finalize_allows_missing_sort_control(
    tmp_path: Path,
) -> None:
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

        summary = scan_posts_page_sync_and_finalize(
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


def test_scan_posts_page_sync_and_finalize_skips_when_sort_adjust_is_unconfirmed(
    tmp_path: Path,
) -> None:
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

        summary = scan_posts_page_sync_and_finalize(
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


def test_scan_posts_page_async_commit_ready_returns_protective_skip_without_db_write(
    tmp_path: Path,
) -> None:
    """async resident posts protective skip 應先回傳 side-effect-free result。"""

    db_path = tmp_path / "app.db"
    page = AsyncUnconfirmedSortFakePage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    auto_adjust_sort=True,
                    enable_ntfy=True,
                    ntfy_topic="phase3a",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        result = asyncio.run(
            scan_posts_page_async_commit_ready(
                page=page,
                app=app,
                target=target,
                config=config,
                scroll_rounds=3,
                scroll_wait_ms=0,
            )
        )
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)
        history = app.repositories.match_history.list_by_target(target.id)
        notifications = app.repositories.notification_events.list_by_target(target.id)

    assert page.sort_adjusted
    assert isinstance(result, ProtectiveSkipScanResult)
    assert result.target_id == target.id
    assert result.skip_reason == "sort_adjust_unconfirmed"
    assert result.metadata["scan_skipped"] is True
    assert result.metadata["stop_reason"] == "sort_adjust_unconfirmed_skip"
    assert latest_scan is None
    assert latest_items == []
    assert history == []
    assert notifications == []


def test_scan_posts_page_async_commit_ready_returns_success_result_without_db_write(
    tmp_path: Path,
) -> None:
    """async resident posts success 應回傳 commit-ready result，不直接 finalize。"""

    db_path = tmp_path / "app.db"
    page = AsyncSuccessFakePage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    auto_adjust_sort=True,
                    enable_ntfy=True,
                    ntfy_topic="phase6",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        result = asyncio.run(
            scan_posts_page_async_commit_ready(
                page=page,
                app=app,
                target=target,
                config=config,
                scroll_rounds=0,
                scroll_wait_ms=0,
            )
        )
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)
        history = app.repositories.match_history.list_by_target(target.id)
        notifications = app.repositories.notification_events.list_by_target(target.id)
        pending_outbox = app.repositories.notification_outbox.list_pending()

    assert page.sort_adjusted
    assert isinstance(result, SuccessScanResult)
    assert result.target_id == target.id
    assert result.item_count == 1
    assert len(result.items) == 1
    assert result.items[0].item_kind == ItemKind.POST
    assert result.items[0].item_key
    assert result.items[0].alias_keys
    assert result.metadata["sort_adjust"]["reason"] == "updated_to_preferred_sort"
    assert "baseline_mode" not in result.metadata
    assert latest_scan is None
    assert latest_items == []
    assert history == []
    assert notifications == []
    assert pending_outbox == []


def test_scan_posts_page_sync_and_finalize_escalates_third_sort_unconfirmed(
    tmp_path: Path,
) -> None:
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

        scan_posts_page_sync_and_finalize(
            page=page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=3,
            scroll_wait_ms=0,
        )
        scan_posts_page_sync_and_finalize(
            page=page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=3,
            scroll_wait_ms=0,
        )

        with pytest.raises(WorkerFailure) as excinfo:
            scan_posts_page_sync_and_finalize(
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
