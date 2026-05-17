"""Feed extractor tests。"""

from __future__ import annotations

import json
from pathlib import Path

from facebook_monitor.facebook.feed_dom import POST_LIKE_ITEMS_SCRIPT
from facebook_monitor.facebook.feed_extractor import normalize_debug_metadata
from facebook_monitor.facebook.permalink import extract_comment_permalink_details
from facebook_monitor.facebook.permalink import extract_canonical_permalink_from_href
from facebook_monitor.facebook.permalink import is_comment_permalink_href
from facebook_monitor.facebook.permalink import normalize_permalink


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


def test_feed_dom_script_filters_empty_permalink_only_candidates() -> None:
    """DOM script 會排除只有 permalink 但沒有文字的候選。"""

    assert "minCandidateTextLength = 8" in POST_LIKE_ITEMS_SCRIPT
    assert "if (!text) continue" in POST_LIKE_ITEMS_SCRIPT
    assert "item.textLength > 0" in POST_LIKE_ITEMS_SCRIPT
