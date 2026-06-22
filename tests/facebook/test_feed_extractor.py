"""Feed extractor tests。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import facebook_monitor.facebook.feed_extractor as feed_extractor
from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.facebook.feed_dom_scripts import POST_LIKE_ITEMS_SCRIPT
from facebook_monitor.facebook.feed_extraction_rounds import build_extract_round_stats
from facebook_monitor.facebook.feed_extractor import normalize_feed_extraction_payload
from facebook_monitor.facebook.feed_extractor import normalize_debug_metadata
from facebook_monitor.facebook.permalink import extract_comment_permalink_details
from facebook_monitor.facebook.permalink import extract_canonical_permalink_from_href
from facebook_monitor.facebook.permalink import is_comment_permalink_href
from facebook_monitor.core.permalink_identity import normalize_permalink


class _FakeFeedPage:
    """記錄同步 feed collection loop 的 wait 呼叫。"""

    def __init__(self) -> None:
        self.waits: list[int] = []

    def wait_for_timeout(self, milliseconds: int) -> None:
        """記錄等待時間。"""

        self.waits.append(milliseconds)


class _AsyncFakeFeedPage:
    """記錄 async feed collection loop 的 wait 呼叫。"""

    def __init__(self) -> None:
        self.waits: list[int] = []

    async def wait_for_timeout(self, milliseconds: int) -> None:
        """記錄等待時間。"""

        self.waits.append(milliseconds)


def _feed_item(index: int) -> ExtractedItem:
    """建立有穩定 permalink alias 的 feed 測試 item。"""

    text = f"這是第 {index} 則完全不同的貼文內容，用來避免測試 dedupe 混淆"
    return ExtractedItem(
        text=text,
        text_length=len(text),
        permalink=(
            "https://www.facebook.com/groups/222518561920110/posts/"
            f"{2187454285426500 + index}"
        ),
        link_count=0,
        author=f"作者 {index}",
    )


def test_extract_canonical_permalink_from_group_posts_href() -> None:
    """一般 group posts href 會轉成 canonical group post URL。"""

    details = extract_canonical_permalink_from_href(
        "https://www.facebook.com/groups/222518561920110/posts/1234567890123456/"
    )

    assert details.permalink == (
        "https://www.facebook.com/groups/222518561920110/posts/1234567890123456"
    )
    assert details.source == "groups_post_anchor"


def test_extract_canonical_permalink_from_group_permalink_href() -> None:
    """group permalink route 會轉成 canonical posts route。"""

    details = extract_canonical_permalink_from_href(
        "https://www.facebook.com/groups/222518561920110/permalink/1234567890123456"
    )

    assert details.permalink == (
        "https://www.facebook.com/groups/222518561920110/posts/1234567890123456"
    )
    assert details.source == "group_permalink_anchor"


def test_extract_canonical_permalink_from_permalink_php_href() -> None:
    """permalink.php story_fbid 會轉成 canonical posts route。"""

    details = extract_canonical_permalink_from_href(
        "https://www.facebook.com/permalink.php?"
        "story_fbid=1234567890123456&id=222518561920110"
    )

    assert details.permalink == (
        "https://www.facebook.com/groups/222518561920110/posts/1234567890123456"
    )
    assert details.source == "permalink_php_anchor"


def test_extract_canonical_permalink_from_group_query_href() -> None:
    """group route 的 multi_permalinks query 會轉成 canonical posts route。"""

    details = extract_canonical_permalink_from_href(
        "https://www.facebook.com/groups/222518561920110?"
        "multi_permalinks=1234567890123456"
    )

    assert details.permalink == (
        "https://www.facebook.com/groups/222518561920110/posts/1234567890123456"
    )
    assert details.source == "group_query_anchor"


def test_extract_canonical_permalink_from_pcb_href() -> None:
    """posts/pcb route 會轉成 canonical posts route。"""

    details = extract_canonical_permalink_from_href(
        "https://www.facebook.com/groups/222518561920110/posts/pcb.1234567890123456"
    )

    assert details.permalink == (
        "https://www.facebook.com/groups/222518561920110/posts/1234567890123456"
    )
    assert details.source == "pcb_anchor"


def test_extract_canonical_permalink_from_photo_gm_href() -> None:
    """photo route 的 set=gm.<id> 會轉成 canonical posts route。"""

    details = extract_canonical_permalink_from_href(
        "https://www.facebook.com/photo.php?"
        "fbid=999&set=gm.1234567890123456&idorvanity=222518561920110"
    )

    assert details.permalink == (
        "https://www.facebook.com/groups/222518561920110/posts/1234567890123456"
    )
    assert details.source == "photo_gm_anchor"


def test_permalink_canonicalization_matches_golden_fixture() -> None:
    """permalink canonicalization golden fixture 鎖住產品語義。"""

    fixture_path = Path("tests/fixtures/facebook/permalink_golden.json")
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))

    for case in cases:
        details = extract_canonical_permalink_from_href(case["href"])
        assert details.permalink == case["expected_permalink"], case["name"]
        assert details.source == case["expected_source"], case["name"]


def test_extract_canonical_permalink_rejects_other_group_when_expected_group_is_set() -> None:
    """expected group id 不符時不回傳 permalink，避免跨社團連結污染。"""

    details = extract_canonical_permalink_from_href(
        "https://www.facebook.com/groups/999999999999999/posts/1234567890123456",
        expected_group_id="222518561920110",
    )

    assert details.permalink == ""


def test_normalize_permalink_uses_canonical_permalink() -> None:
    """dedupe normalize 會使用 canonical permalink。"""

    assert normalize_permalink(
        "https://www.facebook.com/groups/222518561920110/permalink/1234567890123456"
    ) == "https://www.facebook.com/groups/222518561920110/posts/1234567890123456"


def test_is_comment_permalink_href_detects_comment_query() -> None:
    """comment_id / reply_comment_id 會被視為留言層級 permalink。"""

    assert is_comment_permalink_href(
        "https://www.facebook.com/groups/222518561920110/posts/"
        "1234567890123456?comment_id=9876543210987654"
    )
    assert is_comment_permalink_href(
        "https://www.facebook.com/groups/222518561920110/posts/"
        "1234567890123456?reply_comment_id=9876543210987654"
    )


def test_extract_comment_permalink_details_builds_canonical_comment_url() -> None:
    """comment permalink 會轉成 group post + comment_id canonical URL。"""

    details = extract_comment_permalink_details(
        "https://www.facebook.com/groups/222518561920110/posts/"
        "2187454285426518/?comment_id=9876543210987654",
        group_id="222518561920110",
        parent_post_id="",
    )

    assert details.comment_id == "9876543210987654"
    assert details.permalink == (
        "https://www.facebook.com/groups/222518561920110/posts/"
        "2187454285426518/?comment_id=9876543210987654"
    )
    assert details.source == "comment_anchor"


def test_extract_comment_permalink_details_falls_back_to_parent_post_id() -> None:
    """href 沒有 post route 時，使用 target parent_post_id 建立 canonical URL。"""

    details = extract_comment_permalink_details(
        "https://www.facebook.com/comment/replies/?comment_id=9876543210987654",
        group_id="222518561920110",
        parent_post_id="2187454285426518",
    )

    assert details.permalink == (
        "https://www.facebook.com/groups/222518561920110/posts/"
        "2187454285426518/?comment_id=9876543210987654"
    )


def test_feed_dom_script_contains_permalink_warmup_and_text_cleanup() -> None:
    """DOM script 保留 permalink warmup 與文字清理流程。"""

    assert POST_LIKE_ITEMS_SCRIPT.lstrip().startswith("async (maxItems)")
    assert "warmPermalinkAnchors" in POST_LIKE_ITEMS_SCRIPT
    assert "dispatchPermalinkWarmupEvents" in POST_LIKE_ITEMS_SCRIPT
    assert "cleanExtractedText" in POST_LIKE_ITEMS_SCRIPT
    assert "collapseRepeatedAdjacentText" in POST_LIKE_ITEMS_SCRIPT
    assert "expandCollapsedPostText" in POST_LIKE_ITEMS_SCRIPT
    assert "expandCount" in POST_LIKE_ITEMS_SCRIPT
    assert "collectLinkDiagnostics" in POST_LIKE_ITEMS_SCRIPT
    assert "linkDiagnostics" in POST_LIKE_ITEMS_SCRIPT
    assert "collectPermalinkWarmupDiagnostics" in POST_LIKE_ITEMS_SCRIPT
    assert "warmupDiagnostics" in POST_LIKE_ITEMS_SCRIPT
    assert "collectWarmupAnchorDetails" in POST_LIKE_ITEMS_SCRIPT
    assert "anchorDetails" in POST_LIKE_ITEMS_SCRIPT
    assert "isFacebookHomeHref" in POST_LIKE_ITEMS_SCRIPT
    assert "isLikelyHeaderTimestampWarmupAnchor" in POST_LIKE_ITEMS_SCRIPT
    assert "isLikelyObfuscatedTimestampAnchorText" in POST_LIKE_ITEMS_SCRIPT
    assert "likelyHeaderTimestamp" in POST_LIKE_ITEMS_SCRIPT
    assert "const nodes = sortElementsByViewportTop(candidateNodes)" in POST_LIKE_ITEMS_SCRIPT
    assert "domPosition" in POST_LIKE_ITEMS_SCRIPT
    assert "domIndex" in POST_LIKE_ITEMS_SCRIPT
    assert "addTextSnippetWithOverlap" in POST_LIKE_ITEMS_SCRIPT


def test_normalize_debug_metadata_preserves_link_diagnostics() -> None:
    """posts debug metadata 會保留連結分類診斷，但不改抽取語義。"""

    metadata = normalize_debug_metadata(
        {
            "linkCount": 2,
            "linkDiagnostics": {
                "total": 2,
                "kindCounts": {"profile": 1, "hashtag": 1},
                "samples": [{"kind": "profile", "href": "https://www.facebook.com/x"}],
            },
            "warmupDiagnostics": {
                "total": 1,
                "acceptedCount": 0,
                "rejectedReasonCounts": {"user_profile": 1},
                "samples": [
                    {
                        "reason": "user_profile",
                        "anchorDetails": {"rawHref": "/groups/1/user/2/"},
                    }
                ],
            },
            "firstSeenRound": 2,
            "roundItemIndex": 1,
            "collectionIndex": 3,
            "domIndex": 4,
            "domPosition": {"viewportTop": 20, "documentTop": 120, "height": 48},
        }
    )

    assert metadata["linkCount"] == 2
    assert metadata["linkDiagnostics"]["kindCounts"] == {"profile": 1, "hashtag": 1}
    assert metadata["warmupDiagnostics"]["rejectedReasonCounts"] == {"user_profile": 1}
    assert metadata["warmupDiagnostics"]["samples"][0]["anchorDetails"]["rawHref"]
    assert metadata["firstSeenRound"] == 2
    assert metadata["roundItemIndex"] == 1
    assert metadata["collectionIndex"] == 3
    assert metadata["domPosition"]["documentTop"] == 120


def test_normalize_feed_extraction_payload_preserves_meta_and_item_shape() -> None:
    """feed payload normalizer 保留 DOM meta 與 item diagnostics shape。"""

    items, meta = normalize_feed_extraction_payload(
        {
            "items": [
                {
                    "text": "測試貼文",
                    "displayText": "測試貼文\n第二行",
                    "textLength": 4,
                    "displayTextLength": 8,
                    "permalink": "https://www.facebook.com/groups/1/posts/2",
                    "linkCount": 3,
                    "author": "作者",
                    "postId": "2",
                    "permalinkSource": "groups_post_anchor",
                    "ignored": "不應保存",
                },
                "unexpected",
            ],
            "meta": {
                "candidateCount": 2,
                "parsedCount": 1,
            },
        }
    )

    assert meta == {"candidateCount": 2, "parsedCount": 1}
    assert len(items) == 1
    assert items[0].text == "測試貼文"
    assert items[0].display_text == "測試貼文\n第二行"
    assert items[0].text_length == 4
    assert items[0].permalink == "https://www.facebook.com/groups/1/posts/2"
    assert items[0].link_count == 3
    assert items[0].author == "作者"
    assert items[0].debug_metadata == {
        "textLength": 4,
        "displayTextLength": 8,
        "permalinkSource": "groups_post_anchor",
        "postId": "2",
        "linkCount": 3,
        "author": "作者",
    }


def test_build_extract_round_stats_preserves_scroll_and_filter_diagnostics() -> None:
    """feed round diagnostics builder 保留 scroll action 與 DOM filter counters。"""

    items, _meta = normalize_feed_extraction_payload(
        [{"text": "貼文", "textLength": 2}]
    )

    stats = build_extract_round_stats(
        round_index=2,
        round_items=items,
        round_meta={
            "candidateCount": 4,
            "parsedCount": 3,
            "filteredEmptyTextCount": 1,
            "filteredFeedSortControlCount": 2,
            "postsWithPostIdCount": 1,
        },
        unique_item_count=5,
        scroll_metrics={
            "scrollY": 100,
            "scrollHeight": 500,
            "scrollTargetLabel": "window",
            "scrollTargetTop": 20,
        },
        scroll_action={
            "moved": True,
            "beforeTop": 20,
            "afterTop": 120,
            "movedDistance": 100,
            "scrollStep": 240,
            "loadMoreMode": "scroll",
        },
        scroll_rounds=3,
        added_count=1,
        stagnant_windows=0,
    )

    assert stats.round_index == 2
    assert stats.raw_item_count == 1
    assert stats.unique_item_count == 5
    assert stats.scroll_y == 100
    assert stats.scroll_height == 500
    assert stats.scroll_target_label == "window"
    assert stats.scroll_target_top == 20
    assert stats.scroll_moved is True
    assert stats.scroll_before_top == 20
    assert stats.scroll_after_top == 120
    assert stats.scroll_moved_distance == 100
    assert stats.scroll_step == 240
    assert stats.load_more_mode == "scroll"
    assert stats.candidate_count == 4
    assert stats.parsed_count == 3
    assert stats.filtered_empty_text_count == 1
    assert stats.filtered_feed_sort_control_count == 2
    assert stats.posts_with_post_id_count == 1


def test_collect_items_with_diagnostics_preserves_cross_window_debug_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正式 feed loop 會把跨視窗 collection order 寫進 item diagnostics。"""

    page = _FakeFeedPage()
    events: list[str] = []
    scrolls: list[str] = []
    round_payloads = [
        ([_feed_item(1)], {"candidateCount": 1, "parsedCount": 1}),
        ([_feed_item(2)], {"candidateCount": 1, "parsedCount": 1}),
    ]

    def extract_round(
        _page: object,
        candidate_limit: int,
    ) -> tuple[list[ExtractedItem], dict[str, Any]]:
        assert candidate_limit >= 12
        return round_payloads.pop(0)

    monkeypatch.setattr(feed_extractor, "extract_post_like_items_with_meta", extract_round)
    monkeypatch.setattr(
        feed_extractor,
        "capture_load_more_scroll_snapshot",
        lambda _page: events.append("capture"),
    )
    monkeypatch.setattr(
        feed_extractor,
        "restore_load_more_scroll_snapshot",
        lambda _page: events.append("restore"),
    )
    monkeypatch.setattr(
        feed_extractor,
        "get_scroll_metrics",
        lambda _page: {"scrollY": 10, "scrollHeight": 100},
    )

    def scroll(_page: object) -> dict[str, Any]:
        scrolls.append("scroll")
        return {"moved": True, "loadMoreMode": "scroll"}

    monkeypatch.setattr(feed_extractor, "scroll_load_more", scroll)

    items, round_stats, meta = feed_extractor.collect_items_with_diagnostics(
        page,
        max_items=2,
        scroll_rounds=2,
        scroll_wait_ms=75,
    )

    assert events == ["capture", "restore"]
    assert scrolls == ["scroll"]
    assert page.waits == [75]
    assert [item.permalink for item in items] == [
        "https://www.facebook.com/groups/222518561920110/posts/2187454285426501",
        "https://www.facebook.com/groups/222518561920110/posts/2187454285426502",
    ]
    assert items[0].debug_metadata == {
        "firstSeenRound": 0,
        "roundItemIndex": 0,
        "collectionIndex": 0,
    }
    assert items[1].debug_metadata == {
        "firstSeenRound": 1,
        "roundItemIndex": 0,
        "collectionIndex": 1,
    }
    assert [stat.added_count for stat in round_stats] == [1, 1]
    assert [stat.scroll_moved for stat in round_stats] == [True, None]
    assert meta.accumulated_count == 2
    assert meta.attempts == 1


def test_collect_items_with_diagnostics_seen_stop_suppresses_scroll_and_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """seen-stop trigger 後會保留觸發 item，但不再 scroll / wait。"""

    page = _FakeFeedPage()
    events: list[str] = []
    scrolls: list[str] = []

    monkeypatch.setattr(
        feed_extractor,
        "extract_post_like_items_with_meta",
        lambda _page, _candidate_limit: (
            [_feed_item(index) for index in range(4)],
            {"candidateCount": 4, "parsedCount": 4},
        ),
    )
    monkeypatch.setattr(
        feed_extractor,
        "capture_load_more_scroll_snapshot",
        lambda _page: events.append("capture"),
    )
    monkeypatch.setattr(
        feed_extractor,
        "restore_load_more_scroll_snapshot",
        lambda _page: events.append("restore"),
    )
    monkeypatch.setattr(feed_extractor, "get_scroll_metrics", lambda _page: {})

    def scroll(_page: object) -> dict[str, Any]:
        scrolls.append("scroll")
        return {"moved": True}

    monkeypatch.setattr(feed_extractor, "scroll_load_more", scroll)

    items, round_stats, meta = feed_extractor.collect_items_with_diagnostics(
        page,
        max_items=10,
        scroll_rounds=2,
        scroll_wait_ms=75,
        seen_item_predicate=lambda _aliases: True,
    )

    assert events == ["capture", "restore"]
    assert scrolls == []
    assert page.waits == []
    assert len(items) == 4
    assert round_stats[0].added_count == 4
    assert round_stats[0].scroll_moved is None
    assert meta.stop_reason == "seen_stop_consecutive_seen"
    assert meta.seen_stop_triggered is True
    assert meta.seen_stop_seen_count == 4
    assert meta.seen_stop_new_count == 0


def test_collect_items_with_diagnostics_async_seen_stop_matches_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """async feed loop 保留 seen-stop no-scroll/no-wait 語義。"""

    async def run_test() -> None:
        page = _AsyncFakeFeedPage()
        events: list[str] = []
        scrolls: list[str] = []

        async def extract_round(
            _page: object,
            _candidate_limit: int,
        ) -> tuple[list[ExtractedItem], dict[str, Any]]:
            return (
                [_feed_item(index) for index in range(4)],
                {"candidateCount": 4, "parsedCount": 4},
            )

        async def capture_snapshot(_page: object) -> None:
            events.append("capture")

        async def restore_snapshot(_page: object) -> None:
            events.append("restore")

        async def scroll(_page: object) -> dict[str, Any]:
            scrolls.append("scroll")
            return {"moved": True}

        async def metrics(_page: object) -> dict[str, Any]:
            return {}

        monkeypatch.setattr(
            feed_extractor,
            "extract_post_like_items_with_meta_async",
            extract_round,
        )
        monkeypatch.setattr(
            feed_extractor,
            "capture_load_more_scroll_snapshot_async",
            capture_snapshot,
        )
        monkeypatch.setattr(
            feed_extractor,
            "restore_load_more_scroll_snapshot_async",
            restore_snapshot,
        )
        monkeypatch.setattr(feed_extractor, "get_scroll_metrics_async", metrics)
        monkeypatch.setattr(feed_extractor, "scroll_load_more_async", scroll)

        items, round_stats, meta = await feed_extractor.collect_items_with_diagnostics_async(
            page,
            max_items=10,
            scroll_rounds=2,
            scroll_wait_ms=75,
            seen_item_predicate=lambda _aliases: True,
        )

        assert events == ["capture", "restore"]
        assert scrolls == []
        assert page.waits == []
        assert len(items) == 4
        assert round_stats[0].added_count == 4
        assert meta.stop_reason == "seen_stop_consecutive_seen"

    asyncio.run(run_test())


def test_feed_dom_script_filters_empty_permalink_only_candidates() -> None:
    """DOM script 會排除只有 permalink 但沒有文字的候選。"""

    assert "minCandidateTextLength = 8" in POST_LIKE_ITEMS_SCRIPT
    assert "if (!text) continue" in POST_LIKE_ITEMS_SCRIPT
    assert "item.textLength > 0" in POST_LIKE_ITEMS_SCRIPT
