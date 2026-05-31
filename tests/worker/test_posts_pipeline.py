"""Group posts worker tests。"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import TargetConfigPatch
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import utc_now
from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.facebook.extracted_item import make_item_key
from facebook_monitor.facebook.extracted_item import make_item_key_aliases
from facebook_monitor.facebook.sort_controls import FEED_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_controls import SortAdjustResult
from facebook_monitor.notifications.desktop import DesktopNotificationResult
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import DiscordResult
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.posts_pipeline import build_feed_seen_stop_predicate
from facebook_monitor.worker.posts_pipeline import scan_posts_page


class FakeLocator:
    """提供 worker 測試需要的 body inner_text。"""

    def __init__(self, text: str) -> None:
        self.text = text

    def inner_text(self, timeout: int) -> str:
        """回傳假頁面 body 文字。"""

        return self.text


class FakePage:
    """模擬 Playwright page 的 extractor 最小互動。"""

    url = "https://www.facebook.com/groups/222518561920110"

    def __init__(self, items: list[dict[str, Any]] | None = None) -> None:
        self.scrolled = False
        self.sort_adjusted = False
        self.extract_limits: list[int] = []
        self.items = items or [
            {
                "text": "這是一篇有票券關鍵字的貼文",
                "textLength": 14,
                "permalink": "https://www.facebook.com/groups/222518561920110/posts/1",
                "linkCount": 1,
                "author": "王小明",
            },
            {
                "text": "這是一篇普通貼文",
                "textLength": 8,
                "permalink": "https://www.facebook.com/groups/222518561920110/posts/2",
                "linkCount": 1,
                "author": "陳小華",
            },
        ]

    def locator(self, selector: str) -> FakeLocator:
        """回傳 body locator。"""

        return FakeLocator("社團 feed 已登入")

    def evaluate(self, script: str, *args: Any) -> Any:
        """依 extractor 呼叫型態回傳假 DOM 結果。"""

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
                self.extract_limits.append(int(args[0]))
            return self.items
        if "scrollTargetBy" in script and "moved:" in script:
            self.scrolled = True
            return {
                "moved": True,
                "loadMoreMode": "scroll",
                "targetLabel": "document.scrollingElement",
                "beforeTop": 0,
                "afterTop": 900,
                "movedDistance": 900,
                "scrollStep": 900,
                "scrollHeight": 2400,
                "clientHeight": 900,
                "maxScrollTop": 1500,
            }
        if "scrollY" in script:
            return {
                "scrollY": 0,
                "scrollHeight": 1200,
                "scrollTargetLabel": "document.scrollingElement",
                "scrollTargetTop": 0,
                "scrollTargetClientHeight": 900,
                "scrollTargetMaxTop": 300,
            }
        raise AssertionError(f"Unexpected script: {script[:80]}")

    def wait_for_timeout(self, milliseconds: int) -> None:
        """模擬捲動等待。"""


class UnconfirmedSortFakePage(FakePage):
    """模擬 auto_adjust_sort 未能確認切到新貼文。"""

    def evaluate(self, script: str, *args: Any) -> Any:
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
        raise AssertionError("sort-unconfirmed scan should skip before extractor")


class MissingSortControlFakePage(FakePage):
    """模擬社團 feed 沒有排序控制欄位。"""

    def evaluate(self, script: str, *args: Any) -> Any:
        """找不到排序控制時仍應允許後續 extractor。"""

        if "preferredLabel" in script and "sort_control_not_found" in script:
            self.sort_adjusted = True
            return {
                "attempted": False,
                "changed": False,
                "preferredLabel": "新貼文",
                "beforeLabel": "",
                "afterLabel": "",
                "reason": "sort_control_not_found",
                "mutationSuppressionMs": 0,
                "mutationSuppressionReason": "",
            }
        return super().evaluate(script, *args)


class ContentUnavailablePostsPage(FakePage):
    """模擬 Facebook 內容不可見頁。"""

    def locator(self, selector: str) -> FakeLocator:
        """回傳內容不可見頁文字。"""

        return FakeLocator(
            "目前無法查看此內容 "
            "會發生此情況，通常是因為擁有者僅與一小群用戶分享內容、"
            "變更了分享對象，或是刪除了內容。"
        )

    def evaluate(self, script: str, *args: Any) -> Any:
        """內容不可見時不應進入排序或 extractor。"""

        raise AssertionError("content-unavailable scan should stop before sort")


class GrowingFakePage(FakePage):
    """模擬 Facebook 每次捲動後逐步補入更多貼文。"""

    def __init__(self, total_items: int, visible_count: int = 1, grow_by: int = 2) -> None:
        items = [
            {
                "text": f"票券貼文 {chr(65 + index)}",
                "textLength": 10,
                "permalink": (
                    "https://www.facebook.com/groups/222518561920110/posts/"
                    f"{10000000 + index}"
                ),
                "linkCount": 1,
                "author": f"作者 {index}",
            }
            for index in range(total_items)
        ]
        super().__init__(items)
        self.visible_count = visible_count
        self.grow_by = grow_by
        self.scroll_count = 0

    def evaluate(self, script: str, *args: Any) -> Any:
        """依目前可見數量回傳貼文，捲動時增加可見貼文數。"""

        if "preferredLabel" in script and "sort_control_not_found" in script:
            return {
                "attempted": False,
                "changed": False,
                "preferredLabel": "新貼文",
                "beforeLabel": "新貼文",
                "afterLabel": "新貼文",
                "reason": "auto_adjust_sort_disabled",
                "mutationSuppressionMs": 0,
                "mutationSuppressionReason": "",
            }
        if "document.querySelectorAll" in script:
            max_items = int(args[0]) if args else len(self.items)
            self.extract_limits.append(max_items)
            return self.items[: min(self.visible_count, max_items)]
        if "scrollTargetBy" in script and "moved:" in script:
            before = self.visible_count
            self.scroll_count += 1
            self.visible_count = min(len(self.items), self.visible_count + self.grow_by)
            moved = self.visible_count > before
            return {
                "moved": moved,
                "loadMoreMode": "scroll",
                "targetLabel": "document.scrollingElement",
                "beforeTop": self.scroll_count * 900,
                "afterTop": (self.scroll_count + 1) * 900 if moved else self.scroll_count * 900,
                "movedDistance": 900 if moved else 0,
                "scrollStep": 900,
                "scrollHeight": 2400 + self.visible_count * 400,
                "clientHeight": 900,
                "maxScrollTop": 1500 + self.visible_count * 400,
            }
        if "scrollY" in script:
            return {
                "scrollY": self.scroll_count * 900,
                "scrollHeight": 2400 + self.visible_count * 400,
                "scrollTargetLabel": "document.scrollingElement",
                "scrollTargetTop": self.scroll_count * 900,
                "scrollTargetClientHeight": 900,
                "scrollTargetMaxTop": 1500 + self.visible_count * 400,
            }
        return super().evaluate(script, *args)


class SeenStopFakePage(FakePage):
    """模擬最上方連續四篇已看過貼文，後方仍有深度內容。"""

    def __init__(self) -> None:
        items = [
            build_post_payload("10000001", "舊貼文 1"),
            build_post_payload("10000002", "舊貼文 2"),
            build_post_payload("10000003", "舊貼文 3"),
            build_post_payload("10000004", "舊貼文 4"),
            build_post_payload("10000005", "後面還有未載入貼文"),
            build_post_payload("10000006", "後面還有未載入貼文"),
        ]
        super().__init__(items)
        self.scroll_count = 0

    def evaluate(self, script: str, *args: Any) -> Any:
        """連續 seen 觸發後不應再呼叫 scroll script。"""

        if "scrollTargetBy" in script and "moved:" in script:
            self.scroll_count += 1
        return super().evaluate(script, *args)


class MissingSortControlSeenStopFakePage(SeenStopFakePage):
    """模擬找不到排序控制但仍應保留 posts seen-stop 的社團 feed。"""

    def evaluate(self, script: str, *args: Any) -> Any:
        """找不到排序控制時仍應允許 seen-stop 提早停止深度掃描。"""

        if "preferredLabel" in script and "sort_control_not_found" in script:
            self.sort_adjusted = True
            return {
                "attempted": False,
                "changed": False,
                "preferredLabel": "新貼文",
                "beforeLabel": "",
                "afterLabel": "",
                "reason": "sort_control_not_found",
                "mutationSuppressionMs": 0,
                "mutationSuppressionReason": "",
            }
        return super().evaluate(script, *args)


def build_post_payload(post_id: str, text: str) -> dict[str, Any]:
    """建立 posts pipeline 測試用 payload。"""

    return {
        "text": text,
        "textLength": len(text),
        "permalink": f"https://www.facebook.com/groups/222518561920110/posts/{post_id}",
        "linkCount": 1,
        "author": "作者",
    }


def table_count(connection: Any, table_name: str) -> int:
    """回傳指定測試資料表目前筆數。"""

    row = connection.execute(f"SELECT COUNT(1) FROM {table_name}").fetchone()
    return int(row[0])


def mark_post_payload_seen(
    app: ApplicationContext,
    *,
    scope_id: str,
    payload: dict[str, Any],
) -> None:
    """用正式 alias 規則預先建立 seen item。"""

    item = ExtractedItem(
        text=str(payload["text"]),
        text_length=int(payload["textLength"]),
        permalink=str(payload["permalink"]),
        link_count=int(payload["linkCount"]),
        author=str(payload["author"]),
    )
    app.repositories.seen_items.mark_seen_aliases(
        SeenItem(
            scope_id=scope_id,
            item_key=make_item_key(item),
            item_kind=ItemKind.POST,
        ),
        make_item_key_aliases(item),
    )


def _activate_target(
    app: ApplicationContext,
    target: TargetDescriptor,
) -> TargetDescriptor:
    """讓 posts pipeline 測試明確模擬正式 worker 正在掃描 active target。"""

    return app.services.targets.restart_target_monitoring(target.id)


def test_scan_posts_page_records_seen_match_and_scan(tmp_path: Path) -> None:
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
        summary = scan_posts_page(
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


def test_scan_posts_page_records_all_matched_keywords(tmp_path: Path) -> None:
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
        summary = scan_posts_page(
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


def test_scan_posts_page_honors_auto_load_more_config(tmp_path: Path) -> None:
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

        scan_posts_page(
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


def test_scan_posts_page_uses_dynamic_window_limit_for_target_count(
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

        summary = scan_posts_page(
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


def test_scan_posts_page_raises_content_unavailable_before_sort(tmp_path: Path) -> None:
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
            scan_posts_page(
                page=page,
                app=app,
                target=target,
                config=config,
                scroll_rounds=0,
                scroll_wait_ms=0,
            )

    assert exc_info.value.reason == "content_unavailable"
    assert not page.sort_adjusted


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
        assert sent_payloads[0][1] == "Facebook group match"
        assert "關鍵字: 票券" in sent_payloads[0][2]
        assert "測試社團" in sent_payloads[0][2]
        assert "(3) 測試社團" not in sent_payloads[0][2]
        assert "類型: 貼文" in sent_payloads[0][2]
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

