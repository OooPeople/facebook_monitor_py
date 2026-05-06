"""Comments extractor D2 tests。"""

from __future__ import annotations

from typing import Any

from facebook_monitor.facebook.comment_extractor import extract_visible_comment_items


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
                    "textLength": 6,
                    "rawTextLength": 6,
                    "textSource": "comment",
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
        "textLength": 6,
        "rawTextLength": 6,
        "permalinkSource": "comment_anchor",
        "canonicalPermalinkCandidateCount": 1,
        "parentPostId": "2187454285426518",
        "commentId": "9876543210987654",
        "linkCount": 2,
        "author": "留言作者",
        "groupId": "222518561920110",
    }
    assert meta.candidate_count == 2
    assert meta.accumulated_count == 1
