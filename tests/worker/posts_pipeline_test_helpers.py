"""Shared posts pipeline test helpers。"""

from __future__ import annotations

from typing import Any

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.facebook.extracted_item import make_item_key
from facebook_monitor.facebook.extracted_item import make_item_key_aliases


class FakeLocator:
    """提供 worker 測試需要的 body inner_text。"""

    def __init__(self, text: str) -> None:
        self.text = text

    def inner_text(self, *, timeout: int) -> str:
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
                    f"https://www.facebook.com/groups/222518561920110/posts/{10000000 + index}"
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
