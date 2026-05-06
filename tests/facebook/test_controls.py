"""Facebook control helper tests。"""

from __future__ import annotations

from facebook_monitor.facebook.scroll_controls import SCROLL_LOAD_MORE_SCRIPT
from facebook_monitor.facebook.scroll_controls import COMMENT_SCROLL_LOAD_MORE_SCRIPT
from facebook_monitor.facebook.scroll_controls import BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT
from facebook_monitor.facebook.scroll_controls import RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT
from facebook_monitor.facebook.feed_dom import POST_LIKE_ITEMS_SCRIPT
from facebook_monitor.facebook.comment_mutations import COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
from facebook_monitor.facebook.sort_controls import COMMENT_SORT_ADJUST_SCRIPT
from facebook_monitor.facebook.sort_controls import COMMENT_SORT_LABELS
from facebook_monitor.facebook.sort_controls import COMMENT_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_controls import FEED_SORT_LABELS
from facebook_monitor.facebook.sort_controls import FEED_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_controls import FEED_SORT_ADJUST_SCRIPT


def test_feed_sort_labels_match_userscript_final_labels() -> None:
    """feed sort 常數需對齊 userscript 成熟版 label。"""

    assert FEED_SORT_NEWEST_LABEL == "新貼文"
    assert FEED_SORT_LABELS == ("新貼文", "最相關", "最新動態")
    assert "熱門貼文" not in FEED_SORT_ADJUST_SCRIPT
    assert "最近動態" not in FEED_SORT_ADJUST_SCRIPT
    assert "最相關" in FEED_SORT_ADJUST_SCRIPT
    assert "最新動態" in FEED_SORT_ADJUST_SCRIPT


def test_sort_script_uses_exact_feed_option_chain() -> None:
    """排序選項判斷需保留 JS 的 scan target 與 known label chain。"""

    assert "isSortMenuOptionForLabel" in FEED_SORT_ADJUST_SCRIPT
    assert "optionLabels.includes(text)" in FEED_SORT_ADJUST_SCRIPT
    assert "const getCurrentScanTarget" in FEED_SORT_ADJUST_SCRIPT
    assert "getPreferredSortLabelForScanTarget" in FEED_SORT_ADJUST_SCRIPT
    assert "getCurrentSortControlForScanTarget" in FEED_SORT_ADJUST_SCRIPT
    assert "findPreferredSortMenuOptionForScanTarget" in FEED_SORT_ADJUST_SCRIPT
    assert "unsupported_scan_target" in FEED_SORT_ADJUST_SCRIPT
    assert 'suppressMutationsForMs(3200, "auto_adjust_sort")' in FEED_SORT_ADJUST_SCRIPT
    assert "getSelectorElementsByOrder" in FEED_SORT_ADJUST_SCRIPT


def test_comment_sort_labels_and_script_match_userscript_semantics() -> None:
    """comments sort 必須使用 JS 成熟版由新到舊與說明文字判斷鏈。"""

    assert COMMENT_SORT_NEWEST_LABEL == "由新到舊"
    assert COMMENT_SORT_LABELS == ("由新到舊", "最相關", "所有留言")
    assert "isLikelyCommentSortOptionText" in COMMENT_SORT_ADJUST_SCRIPT
    assert "getCurrentCommentSortControl" in COMMENT_SORT_ADJUST_SCRIPT
    assert "findCommentSortMenuOption" in COMMENT_SORT_ADJUST_SCRIPT
    assert "最新的留言顯示在最上方" in COMMENT_SORT_ADJUST_SCRIPT
    assert 'suppressMutationsForMs(3200, "auto_adjust_sort")' in COMMENT_SORT_ADJUST_SCRIPT


def test_scroll_script_uses_target_by_and_restore_snapshot() -> None:
    """auto_load_more 不應退回單純 window.scrollBy，需有 target 與 restore 語義。"""

    assert "getLoadMoreScrollTarget" in SCROLL_LOAD_MORE_SCRIPT
    assert "isScrollableElement" in SCROLL_LOAD_MORE_SCRIPT
    assert "findScrollableAncestor" in SCROLL_LOAD_MORE_SCRIPT
    assert "scrollTargetBy" in SCROLL_LOAD_MORE_SCRIPT
    assert "performConfiguredLoadMore" in SCROLL_LOAD_MORE_SCRIPT
    assert "getLoadMoreMode" in SCROLL_LOAD_MORE_SCRIPT
    assert "movedDistance" in SCROLL_LOAD_MORE_SCRIPT
    assert "window.__facebookMonitorLoadMoreSnapshot" in RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT


def test_comment_scroll_script_uses_nested_targets_and_guard() -> None:
    """comments load-more 必須保留 nested scroll target、scoring 與 guard 語義。"""

    assert "collectCommentScrollTargets" in COMMENT_SCROLL_LOAD_MORE_SCRIPT
    assert "scoreCommentScrollElement" in COMMENT_SCROLL_LOAD_MORE_SCRIPT
    assert "scrollFirstMovable" not in COMMENT_SCROLL_LOAD_MORE_SCRIPT
    assert "comment_nested_scroll" in COMMENT_SCROLL_LOAD_MORE_SCRIPT
    assert "movedDistance" in COMMENT_SCROLL_LOAD_MORE_SCRIPT
    assert "window.__facebookMonitorScanRuntime" in BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT
    assert "isLoadingMoreComments" in BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT


def test_comment_mutation_relevance_helpers_match_userscript_chain() -> None:
    """comments mutation relevance 需保留 permalink/text/suppression 判斷鏈。"""

    assert "elementHasCommentMutationSignal" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "elementHasCommentTextMutationSignal" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "mutationTargetHasDirectCommentSignal" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "mutationsHaveRelevantCommentNodes" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "shouldRescanForCommentMutation" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "__facebookMonitorMutationSuppression" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT


def test_feed_dom_returns_collected_meta_shape() -> None:
    """DOM extractor 需回傳候選與過濾統計，供 collected_meta 彙整。"""

    assert "return { items, meta };" in POST_LIKE_ITEMS_SCRIPT
    assert "candidateCount: nodes.length" in POST_LIKE_ITEMS_SCRIPT
    assert "filteredEmptyTextCount" in POST_LIKE_ITEMS_SCRIPT
    assert "filteredNonPostCount" in POST_LIKE_ITEMS_SCRIPT
    assert "filteredFeedSortControlCount" in POST_LIKE_ITEMS_SCRIPT
    assert "postsWithPostIdCount" in POST_LIKE_ITEMS_SCRIPT
