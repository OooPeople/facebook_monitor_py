"""Comments extractor D2 tests。"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from facebook_monitor.facebook.comment_dom import COMMENTS_LIKE_ITEMS_SCRIPT
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
                    "rawTextLength": 6,
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
        "rawTextLength": 6,
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


def _run_text_snippet_helper(values: list[str]) -> dict[str, object]:
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
