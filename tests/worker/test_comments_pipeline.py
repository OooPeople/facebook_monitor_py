"""Formal async comments worker tests。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.worker.comments_pipeline import (
    scan_comments_target_page_async_commit_ready,
)
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.scan_pipeline_results import ProtectiveSkipScanResult
from facebook_monitor.worker.scan_pipeline_results import SuccessScanResult

from tests.helpers.repository_reads import list_notification_events_by_target
from tests.helpers.repository_reads import list_pending_notification_outbox


class AsyncFakeLocator:
    """提供 async comments worker 測試需要的 body inner_text。"""

    async def inner_text(self, *, timeout: int) -> str:
        """回傳假頁面 body 文字。"""

        return "社團貼文頁已登入"


class AsyncUnconfirmedCommentSortPage:
    """模擬 async 留言排序未能確認切到由新到舊。"""

    url = "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"

    def __init__(self) -> None:
        self.sort_adjusted = False

    def locator(self, selector: str) -> AsyncFakeLocator:
        """回傳 body locator。"""

        return AsyncFakeLocator()

    async def evaluate(self, script: str, payload: object = None) -> object:
        """排序未確認時不應繼續呼叫留言 extractor。"""

        if "preferredLabel" in script and "getCurrentCommentSortControl" in script:
            self.sort_adjusted = True
            return {
                "attempted": True,
                "changed": False,
                "preferredLabel": "由新到舊",
                "beforeLabel": "最相關",
                "afterLabel": "最相關",
                "reason": "sort_update_unconfirmed",
                "mutationSuppressionMs": 3200,
                "mutationSuppressionReason": "auto_adjust_sort",
                "menuCandidateTexts": ["最相關", "由新到舊"],
            }
        raise AssertionError("async sort-unconfirmed scan should skip before extractor")


class AsyncFakeCommentsPage:
    """模擬 async comments scanner 成功抽取留言。"""

    url = "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"

    def __init__(self) -> None:
        self.sort_adjusted = False
        self.settle_calls = 0

    def locator(self, selector: str) -> AsyncFakeLocator:
        """回傳 body locator。"""

        return AsyncFakeLocator()

    async def evaluate(self, script: str, payload: object = None) -> object:
        """回傳 async 可見留言抽取 payload。"""

        if "preferredLabel" in script and "getCurrentCommentSortControl" in script:
            self.sort_adjusted = True
            return {
                "attempted": True,
                "changed": True,
                "preferredLabel": "由新到舊",
                "beforeLabel": "最相關",
                "afterLabel": "由新到舊",
                "reason": "updated_to_preferred_sort",
                "mutationSuppressionMs": 3200,
                "mutationSuppressionReason": "auto_adjust_sort",
            }
        if "comment_dom_settle" in script:
            self.settle_calls += 1
            return {
                "mode": "comment_dom_settle",
                "candidateCount": 1,
                "signature": "stable-comment-signature",
            }
        if "comments_visible_window" not in script:
            raise AssertionError("unexpected async comments page script")
        return {
            "items": [
                {
                    "itemKind": "comment",
                    "commentId": "9876543210987654",
                    "parentPostId": "2187454285426518",
                    "groupId": "222518561920110",
                    "permalink": (
                        "https://www.facebook.com/groups/222518561920110/posts/"
                        "2187454285426518/?comment_id=9876543210987654"
                    ),
                    "permalinkSource": "comment_anchor",
                    "canonicalPermalinkCandidateCount": 1,
                    "author": "留言作者",
                    "text": "這是一則有票券關鍵字的留言",
                    "textLength": 14,
                    "rawTextLength": 14,
                    "textSource": "comment",
                    "linkCount": 2,
                    "source": "comment_permalink_anchor",
                    "containerRole": "comment_container",
                }
            ],
            "meta": {
                "candidateCount": 1,
                "parsedCount": 1,
                "commentsWithCommentIdCount": 1,
                "stopReason": "visible_window_completed",
            },
        }


class AsyncTemporaryBlockLocator(AsyncFakeLocator):
    """提供 async Facebook 暫時限制頁文字。"""

    async def inner_text(self, *, timeout: int) -> str:
        """回傳只供 page guard 使用的合成文字。"""

        return "你暫時遭到封鎖 你似乎過度使用了這項功能"


class AsyncTemporaryBlockCommentsPage:
    """模擬 async comments target 在排序前落入暫時限制頁。"""

    url = AsyncFakeCommentsPage.url

    def __init__(self) -> None:
        self.sort_adjusted = False
        self.guard_observations = 0

    def locator(self, selector: str) -> AsyncTemporaryBlockLocator:
        """回傳暫時限制頁 locator。"""

        return AsyncTemporaryBlockLocator()

    async def evaluate(self, script: str, payload: object = None) -> object:
        """只允許 bounded page guard probe，不得進入排序或抽取。"""

        if "articleCount" not in script:
            raise AssertionError("temporary-block scan must stop before sort")
        self.guard_observations += 1
        return {
            "headingTexts": ["你暫時遭到封鎖"],
            "detailTexts": ["你似乎過度使用了這項功能"],
            "articleCount": 0,
            "feedCandidateCount": 0,
        }

    async def wait_for_timeout(self, timeout: int) -> None:
        """模擬兩次 bounded observation 的短等待。"""


def _activate_target(
    app: ApplicationContext,
    target: TargetDescriptor,
) -> TargetDescriptor:
    """讓 comments pipeline 測試明確模擬正式 worker 掃描 active target。"""

    return app.services.targets.restart_target_monitoring(target.id)


def test_async_comments_returns_protective_skip_without_db_write(
    tmp_path: Path,
) -> None:
    """Async resident comments protective skip 回傳 side-effect-free result。"""

    db_path = tmp_path / "app.db"
    page = AsyncUnconfirmedCommentSortPage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=page.url,
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
            scan_comments_target_page_async_commit_ready(
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
        notifications = list_notification_events_by_target(
            app.repositories.notification_events, target.id
        )

    assert page.sort_adjusted
    assert isinstance(result, ProtectiveSkipScanResult)
    assert result.target_id == target.id
    assert result.skip_reason == SORT_ADJUST_UNCONFIRMED_REASON
    assert result.metadata["scan_skipped"] is True
    assert result.metadata["stop_reason"] == "sort_adjust_unconfirmed_skip"
    assert latest_scan is None
    assert latest_items == []
    assert history == []
    assert notifications == []


def test_async_comments_returns_success_result_without_db_write(tmp_path: Path) -> None:
    """Async resident comments success 回傳 commit-ready result，不直接 finalize。"""

    db_path = tmp_path / "app.db"
    page = AsyncFakeCommentsPage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=page.url,
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
            scan_comments_target_page_async_commit_ready(
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
        notifications = list_notification_events_by_target(
            app.repositories.notification_events, target.id
        )
        pending_outbox = list_pending_notification_outbox(
            app.repositories.notification_outbox,
        )

    assert page.sort_adjusted
    assert isinstance(result, SuccessScanResult)
    assert result.target_id == target.id
    assert result.item_count == 1
    assert len(result.items) == 1
    assert result.items[0].item_kind == ItemKind.COMMENT
    assert result.items[0].parent_post_id == "2187454285426518"
    assert result.items[0].comment_id == "9876543210987654"
    assert result.items[0].metadata is not None
    assert result.items[0].metadata["commentId"] == "9876543210987654"
    assert result.metadata["comment_sort"]["reason"] == "updated_to_preferred_sort"
    assert result.metadata["comments_meta"]["commentsWithCommentIdCount"] == 1
    assert "guardReason" not in result.metadata["comments_meta"]
    assert "baseline_mode" not in result.metadata
    assert latest_scan is None
    assert latest_items == []
    assert history == []
    assert notifications == []
    assert pending_outbox == []


def test_async_comments_page_guard_short_circuits_before_sort(tmp_path: Path) -> None:
    """Async comments 主路徑在排序前停止暫時限制頁。"""

    db_path = tmp_path / "app.db"
    page = AsyncTemporaryBlockCommentsPage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=page.url,
                config=TargetConfigPatch(auto_adjust_sort=True),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        with pytest.raises(WorkerFailure) as exc_info:
            asyncio.run(
                scan_comments_target_page_async_commit_ready(
                    page=page,
                    app=app,
                    target=target,
                    config=config,
                    scroll_rounds=0,
                    scroll_wait_ms=0,
                )
            )

    assert exc_info.value.reason == "facebook_temporary_block"
    assert page.guard_observations == 2
    assert not page.sort_adjusted
