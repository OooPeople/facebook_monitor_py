"""Comments worker tests。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.comments_pipeline import scan_comments_target_page
from facebook_monitor.worker.comments_pipeline import scan_comments_target_page_async
from facebook_monitor.worker.scan_pipeline_results import ProtectiveSkipScanResult
from facebook_monitor.worker.scan_pipeline_results import SuccessScanResult


class FakeLocator:
    """提供 comments worker 測試需要的 body inner_text。"""

    def inner_text(self, *, timeout: int) -> str:
        """回傳假頁面 body 文字。"""

        return "社團貼文頁已登入"


class AsyncFakeLocator:
    """提供 async comments worker 測試需要的 body inner_text。"""

    async def inner_text(self, *, timeout: int) -> str:
        """回傳假頁面 body 文字。"""

        return "社團貼文頁已登入"


class FakeCommentsPage:
    """模擬 Playwright page 的 comments extractor 呼叫。"""

    url = "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"

    def __init__(self) -> None:
        self.sort_adjusted = False
        self.settle_calls = 0

    def locator(self, selector: str) -> FakeLocator:
        """回傳 body locator。"""

        return FakeLocator()

    def evaluate(self, script: str, payload: object = None) -> object:
        """回傳可見留言抽取 payload。"""

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
        assert "comments_visible_window" in script
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


class UnconfirmedCommentSortPage(FakeCommentsPage):
    """模擬留言排序未能確認切到由新到舊。"""

    def evaluate(self, script: str, payload: object = None) -> object:
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
        raise AssertionError("sort-unconfirmed scan should skip before extractor")


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
        raise AssertionError("async sort-unconfirmed comments scan should skip before extractor")


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
        assert "comments_visible_window" in script
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


class MissingCommentSortControlPage(FakeCommentsPage):
    """模擬留言頁完全找不到排序控制與排序標籤。"""

    def evaluate(self, script: str, payload: object = None) -> object:
        """comments 缺排序控制時仍應保護性跳過，不呼叫 extractor。"""

        if "preferredLabel" in script and "getCurrentCommentSortControl" in script:
            self.sort_adjusted = True
            return {
                "attempted": False,
                "changed": False,
                "preferredLabel": "由新到舊",
                "beforeLabel": "",
                "afterLabel": "",
                "reason": "sort_control_not_found",
                "mutationSuppressionMs": 0,
                "mutationSuppressionReason": "",
            }
        raise AssertionError("missing-sort-control comments scan should skip before extractor")


class ContentUnavailableLocator(FakeLocator):
    """提供 Facebook 內容不可見頁文字。"""

    def inner_text(self, *, timeout: int) -> str:
        """回傳內容不可見頁文字。"""

        return (
            "目前無法查看此內容 "
            "會發生此情況，通常是因為擁有者僅與一小群用戶分享內容、"
            "變更了分享對象，或是刪除了內容。"
        )


class ContentUnavailableCommentsPage(FakeCommentsPage):
    """模擬 parent post 已不可見的 comments target。"""

    def locator(self, selector: str) -> ContentUnavailableLocator:
        """回傳內容不可見頁 locator。"""

        return ContentUnavailableLocator()

    def evaluate(self, script: str, payload: object = None) -> object:
        """內容不可見時不應進入排序或留言抽取。"""

        raise AssertionError("content-unavailable scan should stop before sort")


class FakeScrollableCommentsPage(FakeCommentsPage):
    """模擬 comments D3 nested scroll 後逐步載入更多留言。"""

    def __init__(self) -> None:
        super().__init__()
        self.visible_count = 1
        self.scroll_count = 0
        self.guard_active = False
        self.snapshot_captured = False
        self.snapshot_restored = False

    def evaluate(self, script: str, payload: object = None) -> object:
        """依 D3 helper script 回傳 sort / guard / scroll / extractor 結果。"""

        if "preferredLabel" in script and "getCurrentCommentSortControl" in script:
            return {
                "attempted": False,
                "changed": False,
                "preferredLabel": "由新到舊",
                "beforeLabel": "由新到舊",
                "afterLabel": "由新到舊",
                "reason": "already_preferred_sort",
                "mutationSuppressionMs": 0,
                "mutationSuppressionReason": "",
            }
        if "comment_dom_settle" in script:
            self.settle_calls += 1
            return {
                "mode": "comment_dom_settle",
                "candidateCount": self.visible_count,
                "signature": f"stable-comment-signature-{self.visible_count}",
            }
        if "comment_load_more_guard_active" in script:
            if self.guard_active:
                return {"acquired": False, "reason": "comment_load_more_guard_active"}
            self.guard_active = True
            return {"acquired": True, "reason": "comment_load_more_guard_acquired"}
        if "isLoadingMoreComments: false" in script:
            self.guard_active = False
            return {"released": True}
        if "restored: true" in script:
            self.snapshot_restored = True
            return {"restored": True}
        if "__facebookMonitorCommentScrollSnapshot" in script and "targetPositions" in script:
            self.snapshot_captured = True
            return {"captured": True, "targetCount": 2, "targetLabels": ["div role=dialog"]}
        if (
            script.lstrip().startswith("async ()")
            or ("collectCommentScrollTargets" in script and "loadMoreMode" in script)
        ):
            self.scroll_count += 1
            self.visible_count += 1
            return {
                "moved": True,
                "loadMoreMode": "comment_nested_scroll",
                "targetLabel": "div role=dialog",
                "beforeTop": self.scroll_count * 300,
                "afterTop": (self.scroll_count + 1) * 300,
                "movedDistance": 300,
                "scrollStep": 558,
                "scrollHeight": 1800,
            }

        assert "comments_visible_window" in script
        items = []
        for index in range(self.visible_count):
            comment_id = str(9876543210987654 + index)
            keyword = "票券" if index == 1 else "一般"
            items.append(
                {
                    "itemKind": "comment",
                    "commentId": comment_id,
                    "parentPostId": "2187454285426518",
                    "groupId": "222518561920110",
                    "permalink": (
                        "https://www.facebook.com/groups/222518561920110/posts/"
                        f"2187454285426518/?comment_id={comment_id}"
                    ),
                    "permalinkSource": "comment_anchor",
                    "canonicalPermalinkCandidateCount": 1,
                    "author": f"留言作者 {index}",
                    "text": f"這是一則{keyword}留言 {index}",
                    "textLength": 12,
                    "rawTextLength": 12,
                    "textSource": "comment",
                    "linkCount": 1,
                    "source": "comment_permalink_anchor",
                    "containerRole": "comment_container",
                }
            )
        return {
            "items": items,
            "meta": {
                "candidateCount": len(items),
                "parsedCount": len(items),
                "commentsWithCommentIdCount": len(items),
                "stopReason": "visible_window_completed",
            },
        }

    def wait_for_timeout(self, timeout: int) -> None:
        """模擬 Playwright 等待 Facebook 補 DOM。"""

        return None


class FakeDuplicatedCommentTextPage(FakeCommentsPage):
    """模擬 Facebook DOM 將同一段留言文字相鄰輸出兩次。"""

    def evaluate(self, script: str, payload: object = None) -> object:
        """回傳重複文字的 comments extractor payload。"""

        result = super().evaluate(script, payload)
        if isinstance(result, dict) and result.get("items"):
            result["items"][0]["text"] = (
                "這是一則有票券關鍵字的留言 這是一則有票券關鍵字的留言"
            )
        return result


def _activate_target(
    app: ApplicationContext,
    target: TargetDescriptor,
) -> TargetDescriptor:
    """讓 comments pipeline 測試明確模擬正式 worker 正在掃描 active target。"""

    return app.services.targets.restart_target_monitoring(target.id)


def test_scan_comments_target_page_records_latest_scan_and_seen_scope(tmp_path: Path) -> None:
    """comments worker 會寫入 seen/history/latest scan，且使用 comments scope。"""

    db_path = tmp_path / "app.db"
    sent: list[tuple[str, str, str]] = []

    def fake_ntfy_sender(config: Any, title: str, message: str) -> Any:
        sent.append((config.topic, title, message))
        return type("Result", (), {"ok": True, "status_code": 200, "message": "sent"})()

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                    auto_adjust_sort=True,
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        summary = scan_comments_target_page(
            page=FakeCommentsPage(),
            app=app,
            target=target,
            config=config,
            notification_sender=fake_ntfy_sender,
        )

        latest_scan = app.repositories.scan_runs.latest_by_target(target.id, ScanStatus.SUCCESS)
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)
        history = app.repositories.match_history.list_by_target(target.id)

    assert summary.item_count == 1
    assert summary.new_count == 1
    assert summary.matched_count == 1
    assert latest_scan is not None
    assert latest_scan.metadata["collection_strategy"] == "comments_visible_window"
    assert latest_scan.metadata["comments_meta"]["domSettleAttempted"] is True
    assert latest_scan.metadata["comments_meta"]["domSettleStable"] is True
    assert latest_scan.metadata["comment_extract_rounds"][0]["dom_settle_stable"] is True
    assert latest_scan.metadata["comment_sort"]["reason"] == "updated_to_preferred_sort"
    assert latest_scan.metadata["comments_meta"]["commentsWithCommentIdCount"] == 1
    assert len(latest_items) == 1
    assert latest_items[0].item_kind == ItemKind.COMMENT
    assert latest_items[0].author == "留言作者"
    assert latest_items[0].matched_keyword == "票券"
    assert latest_items[0].debug_metadata["commentId"] == "9876543210987654"
    assert len(history) == 1
    assert history[0].item_kind == ItemKind.COMMENT
    assert history[0].parent_post_id == "2187454285426518"
    assert history[0].comment_id == "9876543210987654"
    with SqliteApplicationContext(db_path) as app:
        notifications = app.repositories.notification_events.list_by_target(target.id)
    assert notifications[0].channel == NotificationChannel.NTFY
    assert sent and sent[0][0] == "phase0test"
    assert sent[0][1] == "🎯 Facebook keyword match"
    assert "類型：留言" in sent[0][2]


def test_scan_comments_target_page_skips_when_sort_adjust_is_unconfirmed(
    tmp_path: Path,
) -> None:
    """留言排序未確認時不寫 seen/history/latest/notification，避免舊留言誤通知。"""

    db_path = tmp_path / "app.db"
    page = UnconfirmedCommentSortPage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
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
                    item_kind=ItemKind.COMMENT,
                    item_key="previous-comment",
                    item_index=0,
                    author="舊留言者",
                    text="上一輪留言",
                    permalink=(
                        "https://www.facebook.com/groups/222518561920110/posts/"
                        "2187454285426518/?comment_id=previous"
                    ),
                )
            ],
        )
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        summary = scan_comments_target_page(
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
    assert latest_scan.metadata["comment_sort"]["after_label"] == "最相關"
    assert latest_scan.metadata["comment_sort"]["preferred_label"] == "由新到舊"
    assert latest_items == []
    assert history == []
    assert notifications == []


def test_scan_comments_target_page_async_returns_protective_skip_without_db_write(
    tmp_path: Path,
) -> None:
    """async resident comments protective skip 應先回傳 side-effect-free result。"""

    db_path = tmp_path / "app.db"
    page = AsyncUnconfirmedCommentSortPage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
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
            scan_comments_target_page_async(
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
    assert result.skip_reason == SORT_ADJUST_UNCONFIRMED_REASON
    assert result.metadata["scan_skipped"] is True
    assert result.metadata["stop_reason"] == "sort_adjust_unconfirmed_skip"
    assert latest_scan is None
    assert latest_items == []
    assert history == []
    assert notifications == []


def test_scan_comments_target_page_async_returns_success_result_without_db_write(
    tmp_path: Path,
) -> None:
    """async resident comments success 應回傳 commit-ready result，不直接 finalize。"""

    db_path = tmp_path / "app.db"
    page = AsyncFakeCommentsPage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
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
            scan_comments_target_page_async(
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
    assert result.items[0].item_kind == ItemKind.COMMENT
    assert result.items[0].parent_post_id == "2187454285426518"
    assert result.items[0].comment_id == "9876543210987654"
    assert result.items[0].metadata is not None
    assert result.items[0].metadata["commentId"] == "9876543210987654"
    assert result.metadata["comment_sort"]["reason"] == "updated_to_preferred_sort"
    assert result.metadata["comments_meta"]["commentsWithCommentIdCount"] == 1
    assert "baseline_mode" not in result.metadata
    assert latest_scan is None
    assert latest_items == []
    assert history == []
    assert notifications == []
    assert pending_outbox == []


def test_scan_comments_target_page_escalates_third_sort_unconfirmed(
    tmp_path: Path,
) -> None:
    """comments 排序未確認連續三輪後，升級交給 scan failure policy。"""

    db_path = tmp_path / "app.db"
    page = UnconfirmedCommentSortPage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
                config=TargetConfigPatch(auto_adjust_sort=True),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        scan_comments_target_page(
            page=page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=3,
            scroll_wait_ms=0,
        )
        scan_comments_target_page(
            page=page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=3,
            scroll_wait_ms=0,
        )

        with pytest.raises(WorkerFailure) as excinfo:
            scan_comments_target_page(
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

    assert excinfo.value.reason == SORT_ADJUST_UNCONFIRMED_REASON
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.SUCCESS
    assert latest_scan.metadata["skip_streak"] == 2
    assert scan_count == 2


def test_scan_comments_target_page_skips_when_sort_control_is_missing(
    tmp_path: Path,
) -> None:
    """comments 缺排序控制時不套用 posts 的缺排序控制放寬特例。"""

    db_path = tmp_path / "app.db"
    page = MissingCommentSortControlPage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    auto_adjust_sort=True,
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        summary = scan_comments_target_page(
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
    assert latest_scan.metadata["scan_skipped"] is True
    assert latest_scan.metadata["skip_reason"] == "sort_adjust_unconfirmed"
    assert latest_scan.metadata["comment_sort"]["reason"] == "sort_control_not_found"
    assert latest_scan.metadata["comment_sort"]["before_label"] == ""
    assert latest_scan.metadata["comment_sort"]["after_label"] == ""
    assert latest_items == []
    assert history == []
    assert notifications == []


def test_scan_comments_target_page_raises_content_unavailable_before_sort(
    tmp_path: Path,
) -> None:
    """parent post 不可見時應分類成連結失效，不應落到留言排序失敗。"""

    db_path = tmp_path / "app.db"
    page = ContentUnavailableCommentsPage()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
                config=TargetConfigPatch(auto_adjust_sort=True),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None

        with pytest.raises(WorkerFailure) as exc_info:
            scan_comments_target_page(
                page=page,
                app=app,
                target=target,
                config=config,
                scroll_rounds=0,
                scroll_wait_ms=0,
            )

    assert exc_info.value.reason == "content_unavailable"
    assert not page.sort_adjusted


def test_scan_comments_target_page_collapses_duplicate_comment_text_in_notification(
    tmp_path: Path,
) -> None:
    """comments worker 會沿用共用清理語義，避免通知內文重複兩次。"""

    db_path = tmp_path / "app.db"
    sent: list[tuple[str, str, str]] = []

    def fake_ntfy_sender(config: Any, title: str, message: str) -> Any:
        sent.append((config.topic, title, message))
        return type("Result", (), {"ok": True, "status_code": 200, "message": "sent"})()

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
                group_name="測試社團",
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

        scan_comments_target_page(
            page=FakeDuplicatedCommentTextPage(),
            app=app,
            target=target,
            config=config,
            notification_sender=fake_ntfy_sender,
        )
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)

    assert sent
    assert sent[0][2].count("這是一則有票券關鍵字的留言") == 1
    assert latest_items[0].text == "這是一則有票券關鍵字的留言"


def test_scan_comments_target_page_uses_nested_scroll_load_more(tmp_path: Path) -> None:
    """comments D3 會使用 nested scroll 收集到目標數量並保存診斷。"""

    db_path = tmp_path / "app.db"
    page = FakeScrollableCommentsPage()

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    "2187454285426518"
                ),
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    max_items_per_scan=2,
                    auto_load_more=True,
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.repositories.configs.get_for_target(target)
        assert config is not None
        assert config.max_items_per_scan == 2
        assert config.auto_load_more is True

        summary = scan_comments_target_page(
            page=page,
            app=app,
            target=target,
            config=config,
            scroll_rounds=1,
            scroll_wait_ms=0,
        )
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id, ScanStatus.SUCCESS)
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)

    assert page.scroll_count == 1
    assert page.visible_count == 2
    assert summary.item_count == 2
    assert summary.matched_count == 1
    assert page.snapshot_captured
    assert page.snapshot_restored
    assert latest_scan is not None
    assert latest_scan.metadata["collection_strategy"] == "comments_nested_scroll"
    assert latest_scan.metadata["comment_scroll_collection_enabled"] is True
    assert latest_scan.metadata["load_more_mode"] == "comment_nested_scroll"
    assert latest_scan.metadata["comment_extract_rounds"][0]["scroll_moved"] is True
    assert latest_scan.metadata["comment_extract_rounds"][0]["dom_settle_attempted"] is True
    assert latest_scan.metadata["comments_meta"]["attempted"] is True
    assert latest_items[1].matched_keyword == "票券"
