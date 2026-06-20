"""Comments extractor D2 tests。"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from typing import Any

import pytest

import facebook_monitor.facebook.comment_extractor as comment_extractor
from facebook_monitor.facebook.comment_dom_scripts import COMMENTS_LIKE_ITEMS_SCRIPT
from facebook_monitor.facebook.comment_extraction_models import CommentCollectionMeta
from facebook_monitor.facebook.comment_extractor import collect_comment_items_with_load_more_guard_held
from facebook_monitor.facebook.comment_extractor import (
    collect_comment_items_with_load_more_guard_held_async,
)
from facebook_monitor.facebook.comment_extractor import extract_visible_comment_items
from facebook_monitor.facebook.text_snippet_dom import TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT


class FakeCommentPage:
    """模擬 Playwright page 的 comment extractor evaluate 呼叫。"""

    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.evaluate_payloads: list[object] = []

    def evaluate(self, script: str, payload: object) -> object:
        """回傳假 DOM extractor payload。"""

        assert "comments_visible_window" in script
        self.evaluate_payloads.append(payload)
        return self.payload


def test_extract_visible_comment_items_normalizes_and_dedupes() -> None:
    """D2 comment extractor 會保留 comment id / parent post / canonical permalink 診斷。"""

    page = FakeCommentPage(
        {
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
                    "text": "這是一則留言",
                    "displayText": "這是一則留言\n第二行票券",
                    "commentAnchorHref": (
                        "https://www.facebook.com/groups/222518561920110/posts/"
                        "2187454285426518/?comment_id=9876543210987654"
                    ),
                    "routePostId": "2187454285426518",
                    "routePostIdMatchesTarget": True,
                    "routePostIdSource": "comment_anchor_href",
                    "commentScopeReason": "route_post_match",
                    "commentSearchRoot": "div role=dialog aria-modal=true",
                    "commentSearchRootStrategy": "target_dialog",
                    "currentRoutePostId": "2187454285426518",
                    "currentRouteMatchesTarget": True,
                    "textLength": 6,
                    "displayTextLength": 12,
                    "rawTextLength": 6,
                    "rawDisplayTextLength": 12,
                    "textSource": "comment",
                    "textDiagnostics": {
                        "candidateCount": 2,
                        "includedCount": 1,
                        "reasonCounts": {"included": 1, "duplicate_snippet": 1},
                        "samples": [
                            {
                                "reason": "included",
                                "included": True,
                                "text": "這是一則留言",
                            }
                        ],
                    },
                    "linkCount": 2,
                    "source": "comment_permalink_anchor",
                    "containerRole": "comment_container",
                },
                {
                    "itemKind": "comment",
                    "commentId": "9876543210987654",
                    "parentPostId": "2187454285426518",
                    "permalink": "duplicate",
                    "text": "這是一則留言",
                    "textLength": 6,
                },
            ],
            "meta": {
                "candidateCount": 2,
                "parsedCount": 2,
                "filteredEmptyTextCount": 0,
                "filteredNonPostCount": 0,
                "commentsWithCommentIdCount": 2,
                "stopReason": "visible_window_completed",
            },
        }
    )

    items, meta = extract_visible_comment_items(
        page,
        group_id="222518561920110",
        parent_post_id="2187454285426518",
        max_items=5,
    )

    assert page.evaluate_payloads == [
        {
            "groupId": "222518561920110",
            "parentPostId": "2187454285426518",
            "limit": 5,
        }
    ]
    assert len(items) == 1
    assert items[0].item_kind == "comment"
    assert items[0].comment_id == "9876543210987654"
    assert items[0].parent_post_id == "2187454285426518"
    assert items[0].author == "留言作者"
    assert items[0].text == "這是一則留言"
    assert items[0].display_text == "這是一則留言\n第二行票券"
    assert items[0].debug_metadata == {
        "source": "comment_permalink_anchor",
        "containerRole": "comment_container",
        "textSource": "comment",
        "textDiagnostics": {
            "candidateCount": 2,
            "includedCount": 1,
            "reasonCounts": {"included": 1, "duplicate_snippet": 1},
            "samples": [
                {
                    "reason": "included",
                    "included": True,
                    "text": "這是一則留言",
                }
            ],
        },
        "textLength": 6,
        "displayTextLength": 12,
        "rawTextLength": 6,
        "rawDisplayTextLength": 12,
        "permalinkSource": "comment_anchor",
        "canonicalPermalinkCandidateCount": 1,
        "parentPostId": "2187454285426518",
        "commentId": "9876543210987654",
        "commentAnchorHref": (
            "https://www.facebook.com/groups/222518561920110/posts/"
            "2187454285426518/?comment_id=9876543210987654"
        ),
        "routePostId": "2187454285426518",
        "routePostIdMatchesTarget": True,
        "routePostIdSource": "comment_anchor_href",
        "commentScopeReason": "route_post_match",
        "commentSearchRoot": "div role=dialog aria-modal=true",
        "commentSearchRootStrategy": "target_dialog",
        "currentRoutePostId": "2187454285426518",
        "currentRouteMatchesTarget": True,
        "linkCount": 2,
        "author": "留言作者",
        "groupId": "222518561920110",
    }
    assert meta.candidate_count == 2
    assert meta.accumulated_count == 1


def test_collect_comments_releases_guard_when_snapshot_capture_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """snapshot capture 失敗仍需釋放 comments load-more guard。"""

    released: list[bool] = []
    restored: list[bool] = []

    def raise_snapshot(_page: object) -> None:
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(comment_extractor, "capture_comment_scroll_snapshot", raise_snapshot)
    monkeypatch.setattr(
        comment_extractor,
        "restore_comment_scroll_snapshot",
        lambda _page: restored.append(True),
    )
    monkeypatch.setattr(
        comment_extractor,
        "end_comment_load_more_guard",
        lambda _page: released.append(True),
    )

    with pytest.raises(RuntimeError, match="snapshot failed"):
        collect_comment_items_with_load_more_guard_held(
            page=object(),
            group_id="group",
            parent_post_id="post",
            max_items=5,
            scroll_rounds=1,
            scroll_wait_ms=0,
            auto_load_more=True,
        )

    assert released == [True]
    assert restored == []


def test_collect_comments_async_releases_guard_when_snapshot_capture_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """async snapshot capture 失敗仍需釋放 comments load-more guard。"""

    released: list[bool] = []
    restored: list[bool] = []

    async def raise_snapshot(_page: object) -> None:
        raise RuntimeError("snapshot failed")

    async def restore_snapshot(_page: object) -> None:
        restored.append(True)

    async def release_guard(_page: object) -> None:
        released.append(True)

    monkeypatch.setattr(
        comment_extractor,
        "capture_comment_scroll_snapshot_async",
        raise_snapshot,
    )
    monkeypatch.setattr(
        comment_extractor,
        "restore_comment_scroll_snapshot_async",
        restore_snapshot,
    )
    monkeypatch.setattr(
        comment_extractor,
        "end_comment_load_more_guard_async",
        release_guard,
    )

    async def run_test() -> None:
        with pytest.raises(RuntimeError, match="snapshot failed"):
            await collect_comment_items_with_load_more_guard_held_async(
                page=object(),
                group_id="group",
                parent_post_id="post",
                max_items=5,
                scroll_rounds=1,
                scroll_wait_ms=0,
                auto_load_more=True,
            )

    asyncio.run(run_test())

    assert released == [True]
    assert restored == []


def test_collect_comments_releases_guard_when_snapshot_restore_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """snapshot restore 失敗仍需釋放 comments load-more guard。"""

    released: list[bool] = []

    def raise_restore(_page: object) -> None:
        raise RuntimeError("restore failed")

    monkeypatch.setattr(comment_extractor, "capture_comment_scroll_snapshot", lambda _page: None)
    monkeypatch.setattr(comment_extractor, "restore_comment_scroll_snapshot", raise_restore)
    monkeypatch.setattr(
        comment_extractor,
        "end_comment_load_more_guard",
        lambda _page: released.append(True),
    )
    monkeypatch.setattr(
        comment_extractor,
        "wait_for_comment_dom_settle",
        lambda _page, *, max_items: None,
    )
    monkeypatch.setattr(
        comment_extractor,
        "extract_visible_comment_items",
        lambda _page, *, group_id, parent_post_id, max_items: (
            [],
            CommentCollectionMeta(
                target_count=max_items,
                candidate_count=0,
                parsed_count=0,
                accumulated_count=0,
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="restore failed"):
        collect_comment_items_with_load_more_guard_held(
            page=object(),
            group_id="group",
            parent_post_id="post",
            max_items=5,
            scroll_rounds=0,
            scroll_wait_ms=0,
            auto_load_more=True,
        )

    assert released == [True]


def test_collect_comments_async_releases_guard_when_snapshot_restore_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """async snapshot restore 失敗仍需釋放 comments load-more guard。"""

    released: list[bool] = []

    async def capture_snapshot(_page: object) -> None:
        return None

    async def raise_restore(_page: object) -> None:
        raise RuntimeError("restore failed")

    async def release_guard(_page: object) -> None:
        released.append(True)

    async def wait_for_settle(_page: object, *, max_items: int) -> None:
        return None

    async def extract_items(
        _page: object,
        *,
        group_id: str,
        parent_post_id: str,
        max_items: int,
    ):
        return (
            [],
            CommentCollectionMeta(
                target_count=max_items,
                candidate_count=0,
                parsed_count=0,
                accumulated_count=0,
            ),
        )

    monkeypatch.setattr(
        comment_extractor,
        "capture_comment_scroll_snapshot_async",
        capture_snapshot,
    )
    monkeypatch.setattr(
        comment_extractor,
        "restore_comment_scroll_snapshot_async",
        raise_restore,
    )
    monkeypatch.setattr(
        comment_extractor,
        "end_comment_load_more_guard_async",
        release_guard,
    )
    monkeypatch.setattr(
        comment_extractor,
        "wait_for_comment_dom_settle_async",
        wait_for_settle,
    )
    monkeypatch.setattr(
        comment_extractor,
        "extract_visible_comment_items_async",
        extract_items,
    )

    async def run_test() -> None:
        with pytest.raises(RuntimeError, match="restore failed"):
            await collect_comment_items_with_load_more_guard_held_async(
                page=object(),
                group_id="group",
                parent_post_id="post",
                max_items=5,
                scroll_rounds=0,
                scroll_wait_ms=0,
                auto_load_more=True,
            )

    asyncio.run(run_test())

    assert released == [True]


def _run_text_snippet_helper(values: list[str]) -> dict[str, Any]:
    """用 Node 驗證 shared JS snippet helper 的包含關係語義。"""

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute DOM snippet helper tests")
    script = f"""
const normalizeText = (value) => String(value || "").replace(/\\s+/g, " ").trim();
{TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT}
const snippets = [];
const seen = new Set();
const results = [];
for (const value of {json.dumps(values, ensure_ascii=False)}) {{
  results.push(addTextSnippetWithOverlap(snippets, seen, value));
}}
console.log(JSON.stringify({{ snippets, results }}));
"""
    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def test_text_snippet_helper_skips_child_fragments_after_full_comment_text() -> None:
    """留言完整節點先出現時，後續子片段不應再次併入內容。"""

    full_text = "#收 誠心收 5/26或27內野前排109區或117區4-6張 拜託感謝"
    payload = _run_text_snippet_helper(
        [
            full_text,
            "#收",
            "誠心收",
            "5/26或27內野前排109區或117區4-6張",
        ]
    )

    assert payload["snippets"] == [full_text]
    assert [item["reason"] for item in payload["results"][1:]] == [
        "contained_by_existing_snippet",
        "contained_by_existing_snippet",
        "contained_by_existing_snippet",
    ]


def test_text_snippet_helper_replaces_fragments_when_full_comment_text_arrives_later() -> None:
    """留言子片段先出現時，後續完整節點應取代已收集的片段。"""

    full_text = "#售 5/27 大巨蛋 108 17-17 17-18 兩張連號靠近109"
    payload = _run_text_snippet_helper(
        [
            "#售",
            "5/27 大巨蛋 108 17-17",
            full_text,
        ]
    )

    assert payload["snippets"] == [full_text]
    assert payload["results"][-1]["reason"] == "included_replacing_contained_snippets"
    assert payload["results"][-1]["replacedContainedSnippetCount"] == 2


def test_comment_dom_script_uses_shared_text_snippet_overlap_helper() -> None:
    """comments DOM script 接上 shared snippet overlap helper。"""

    assert "addTextSnippetWithOverlap" in COMMENTS_LIKE_ITEMS_SCRIPT
    assert "contained_by_existing_snippet" in COMMENTS_LIKE_ITEMS_SCRIPT


def test_comment_dom_script_guards_against_background_comments() -> None:
    """comments DOM script 會記錄並排除不屬於目標貼文的背景留言。"""

    assert "collectCommentSearchRoots" in COMMENTS_LIKE_ITEMS_SCRIPT
    assert "evaluateCommentTargetScope" in COMMENTS_LIKE_ITEMS_SCRIPT
    assert "route_post_mismatch" in COMMENTS_LIKE_ITEMS_SCRIPT
    assert "missing_route_post_id_unscoped" in COMMENTS_LIKE_ITEMS_SCRIPT
    assert "filteredOutOfScopeCount" in COMMENTS_LIKE_ITEMS_SCRIPT
    assert "commentAnchorHref" in COMMENTS_LIKE_ITEMS_SCRIPT
