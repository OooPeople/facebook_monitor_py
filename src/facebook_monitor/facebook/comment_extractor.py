"""Facebook comment extractor helpers。

職責：提供 comments 的可見留言抽取、comment identity、診斷整理與
D3 nested scroll/load-more 收集流程。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from facebook_monitor.core.dedupe import aliases_overlap
from facebook_monitor.facebook.comment_dom import COMMENTS_LIKE_ITEMS_SCRIPT
from facebook_monitor.facebook.collection_policy import (
    CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT,
)
from facebook_monitor.facebook.collection_policy import get_dynamic_max_windows
from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.facebook.extracted_item import make_item_key_aliases
from facebook_monitor.facebook.comment_dom_settle_script import COMMENT_DOM_SETTLE_SCRIPT
from facebook_monitor.facebook.scroll_controls import begin_comment_load_more_guard
from facebook_monitor.facebook.scroll_controls import begin_comment_load_more_guard_async
from facebook_monitor.facebook.scroll_controls import capture_comment_scroll_snapshot
from facebook_monitor.facebook.scroll_controls import capture_comment_scroll_snapshot_async
from facebook_monitor.facebook.scroll_controls import end_comment_load_more_guard
from facebook_monitor.facebook.scroll_controls import end_comment_load_more_guard_async
from facebook_monitor.facebook.scroll_controls import restore_comment_scroll_snapshot
from facebook_monitor.facebook.scroll_controls import restore_comment_scroll_snapshot_async
from facebook_monitor.facebook.scroll_controls import scroll_comment_load_more
from facebook_monitor.facebook.scroll_controls import scroll_comment_load_more_async
from facebook_monitor.facebook.text_cleanup import clean_facebook_text
from facebook_monitor.facebook.text_cleanup import clean_facebook_multiline_text

COMMENT_DOM_SETTLE_INITIAL_WAIT_MS = 700
COMMENT_DOM_SETTLE_POLL_INTERVAL_MS = 500
COMMENT_DOM_SETTLE_MAX_WAIT_MS = 2500
COMMENT_DOM_SETTLE_STABLE_OBSERVATIONS = 2

@dataclass(frozen=True)
class CommentCollectionMeta:
    """保存 comments visible-window 抽取統計。"""

    target_count: int
    candidate_count: int
    parsed_count: int
    accumulated_count: int
    filtered_empty_text_count: int = 0
    filtered_non_post_count: int = 0
    article_element_count: int = 0
    comments_with_comment_id_count: int = 0
    filtered_out_of_scope_count: int = 0
    comment_search_root_strategy: str = ""
    current_route_post_id: str = ""
    current_route_matches_target: bool = False
    mode: str = "comments_visible_window"
    attempted: bool = False
    attempts: int = 0
    before_count: int = 0
    after_count: int = 0
    window_count: int = 1
    max_window_count: int = 1
    stagnant_windows: int = 0
    load_more_mode: str = "off"
    guard_reason: str = ""
    stop_reason: str = "visible_window_completed"
    dom_settle_attempted: bool = False
    dom_settle_stable: bool = False
    dom_settle_observations: int = 0
    dom_settle_wait_ms: int = 0
    dom_settle_candidate_count: int = 0

    def to_metadata(self) -> dict[str, Any]:
        """轉成 latest scan metadata 使用的 comments vocabulary。"""

        return {
            "mode": self.mode,
            "targetCount": self.target_count,
            "attempted": self.attempted,
            "attempts": self.attempts,
            "beforeCount": self.before_count,
            "afterCount": self.after_count,
            "windowCount": self.window_count,
            "candidateCount": self.candidate_count,
            "parsedCount": self.parsed_count,
            "accumulatedCount": self.accumulated_count,
            "maxWindowCount": self.max_window_count,
            "stagnantWindows": self.stagnant_windows,
            "loadMoreMode": self.load_more_mode,
            "guardReason": self.guard_reason,
            "filteredEmptyTextCount": self.filtered_empty_text_count,
            "filteredNonPostCount": self.filtered_non_post_count,
            "articleElementCount": self.article_element_count,
            "commentsWithCommentIdCount": self.comments_with_comment_id_count,
            "filteredOutOfScopeCount": self.filtered_out_of_scope_count,
            "commentSearchRootStrategy": self.comment_search_root_strategy,
            "currentRoutePostId": self.current_route_post_id,
            "currentRouteMatchesTarget": self.current_route_matches_target,
            "stopReason": self.stop_reason,
            "domSettleAttempted": self.dom_settle_attempted,
            "domSettleStable": self.dom_settle_stable,
            "domSettleObservations": self.dom_settle_observations,
            "domSettleWaitMs": self.dom_settle_wait_ms,
            "domSettleCandidateCount": self.dom_settle_candidate_count,
        }


@dataclass(frozen=True)
class CommentExtractRoundStats:
    """保存 comments 跨視窗收集單輪診斷。"""

    round_index: int
    raw_item_count: int
    unique_item_count: int
    candidate_count: int
    parsed_count: int
    accumulated_count: int
    filtered_empty_text_count: int = 0
    filtered_non_post_count: int = 0
    article_element_count: int = 0
    comments_with_comment_id_count: int = 0
    filtered_out_of_scope_count: int = 0
    scroll_moved: bool | None = None
    scroll_target_label: str = ""
    scroll_before_top: int | None = None
    scroll_after_top: int | None = None
    scroll_moved_distance: int | None = None
    scroll_step: int | None = None
    scroll_height: int | None = None
    load_more_mode: str = "off"
    added_count: int = 0
    stagnant_windows: int = 0
    dom_settle_attempted: bool = False
    dom_settle_stable: bool = False
    dom_settle_observations: int = 0
    dom_settle_wait_ms: int = 0
    dom_settle_candidate_count: int = 0


@dataclass(frozen=True)
class CommentDomSettleResult:
    """保存 comments DOM settle 的非破壞性觀察結果。"""

    attempted: bool
    stable: bool
    observations: int
    wait_ms: int
    candidate_count: int = 0


def extract_visible_comment_items(
    page: Any,
    *,
    group_id: str,
    parent_post_id: str,
    max_items: int,
) -> tuple[list[ExtractedItem], CommentCollectionMeta]:
    """從目前頁面已載入 DOM 抽取可見留言候選。"""

    raw_items = page.evaluate(
        COMMENTS_LIKE_ITEMS_SCRIPT,
        {
            "groupId": group_id,
            "parentPostId": parent_post_id,
            "limit": max(max_items, 1),
        },
    )
    return normalize_comment_extraction_payload(raw_items, max_items=max_items)


def wait_for_comment_dom_settle(page: Any, *, max_items: int) -> CommentDomSettleResult:
    """等待 comments DOM 短暫穩定；失敗時回傳診斷但不中斷既有抽取。"""

    return _wait_for_comment_dom_settle_with_waiter(
        evaluate=page.evaluate,
        wait_for_timeout=getattr(page, "wait_for_timeout", None),
        max_items=max_items,
    )


async def wait_for_comment_dom_settle_async(
    page: Any,
    *,
    max_items: int,
) -> CommentDomSettleResult:
    """async 版本：等待 comments DOM 短暫穩定。"""

    return await _wait_for_comment_dom_settle_with_waiter_async(
        evaluate=page.evaluate,
        wait_for_timeout=getattr(page, "wait_for_timeout", None),
        max_items=max_items,
    )


def _wait_for_comment_dom_settle_with_waiter(
    *,
    evaluate: Any,
    wait_for_timeout: Any,
    max_items: int,
) -> CommentDomSettleResult:
    """同步 settle 實作，將 Playwright 例外降級成不阻斷診斷。"""

    wait_ms = 0
    observations = 0
    last_signature = ""
    stable_observations = 0
    candidate_count = 0
    try:
        _call_waiter(wait_for_timeout, COMMENT_DOM_SETTLE_INITIAL_WAIT_MS)
        wait_ms += COMMENT_DOM_SETTLE_INITIAL_WAIT_MS
        while wait_ms <= COMMENT_DOM_SETTLE_MAX_WAIT_MS:
            payload = evaluate(
                COMMENT_DOM_SETTLE_SCRIPT,
                {"limit": max(max_items, 1)},
            )
            signature, candidate_count = _normalize_comment_dom_settle_payload(payload)
            observations += 1
            if signature and signature == last_signature:
                stable_observations += 1
            else:
                stable_observations = 1
                last_signature = signature
            if stable_observations >= COMMENT_DOM_SETTLE_STABLE_OBSERVATIONS:
                return CommentDomSettleResult(
                    attempted=True,
                    stable=True,
                    observations=observations,
                    wait_ms=wait_ms,
                    candidate_count=candidate_count,
                )
            if wait_ms >= COMMENT_DOM_SETTLE_MAX_WAIT_MS:
                break
            _call_waiter(wait_for_timeout, COMMENT_DOM_SETTLE_POLL_INTERVAL_MS)
            wait_ms += COMMENT_DOM_SETTLE_POLL_INTERVAL_MS
    except Exception:
        return CommentDomSettleResult(
            attempted=True,
            stable=False,
            observations=observations,
            wait_ms=wait_ms,
            candidate_count=candidate_count,
        )
    return CommentDomSettleResult(
        attempted=True,
        stable=False,
        observations=observations,
        wait_ms=wait_ms,
        candidate_count=candidate_count,
    )


async def _wait_for_comment_dom_settle_with_waiter_async(
    *,
    evaluate: Any,
    wait_for_timeout: Any,
    max_items: int,
) -> CommentDomSettleResult:
    """async settle 實作，保持失敗不阻斷 comments extractor。"""

    wait_ms = 0
    observations = 0
    last_signature = ""
    stable_observations = 0
    candidate_count = 0
    try:
        await _call_waiter_async(wait_for_timeout, COMMENT_DOM_SETTLE_INITIAL_WAIT_MS)
        wait_ms += COMMENT_DOM_SETTLE_INITIAL_WAIT_MS
        while wait_ms <= COMMENT_DOM_SETTLE_MAX_WAIT_MS:
            payload = await evaluate(
                COMMENT_DOM_SETTLE_SCRIPT,
                {"limit": max(max_items, 1)},
            )
            signature, candidate_count = _normalize_comment_dom_settle_payload(payload)
            observations += 1
            if signature and signature == last_signature:
                stable_observations += 1
            else:
                stable_observations = 1
                last_signature = signature
            if stable_observations >= COMMENT_DOM_SETTLE_STABLE_OBSERVATIONS:
                return CommentDomSettleResult(
                    attempted=True,
                    stable=True,
                    observations=observations,
                    wait_ms=wait_ms,
                    candidate_count=candidate_count,
                )
            if wait_ms >= COMMENT_DOM_SETTLE_MAX_WAIT_MS:
                break
            await _call_waiter_async(wait_for_timeout, COMMENT_DOM_SETTLE_POLL_INTERVAL_MS)
            wait_ms += COMMENT_DOM_SETTLE_POLL_INTERVAL_MS
    except Exception:
        return CommentDomSettleResult(
            attempted=True,
            stable=False,
            observations=observations,
            wait_ms=wait_ms,
            candidate_count=candidate_count,
        )
    return CommentDomSettleResult(
        attempted=True,
        stable=False,
        observations=observations,
        wait_ms=wait_ms,
        candidate_count=candidate_count,
    )


def _normalize_comment_dom_settle_payload(payload: object) -> tuple[str, int]:
    """整理 settle script payload。"""

    if not isinstance(payload, Mapping):
        return "", 0
    return (
        str(payload.get("signature") or ""),
        int(payload.get("candidateCount") or 0),
    )


def _call_waiter(wait_for_timeout: Any, milliseconds: int) -> None:
    """呼叫 Playwright wait_for_timeout；測試 fake 缺方法時安靜略過。"""

    if callable(wait_for_timeout):
        wait_for_timeout(milliseconds)


async def _call_waiter_async(wait_for_timeout: Any, milliseconds: int) -> None:
    """async 呼叫 Playwright wait_for_timeout；相容同步 fake。"""

    if not callable(wait_for_timeout):
        return
    result = wait_for_timeout(milliseconds)
    if hasattr(result, "__await__"):
        await result


def collect_comment_items_with_diagnostics(
    page: Any,
    *,
    group_id: str,
    parent_post_id: str,
    max_items: int,
    scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
) -> tuple[list[ExtractedItem], list[CommentExtractRoundStats], CommentCollectionMeta]:
    """依 D3 comments 規則跨可見視窗累積留言。"""

    rounds = max(int(scroll_rounds), 0) if auto_load_more else 0
    wait_ms = max(int(scroll_wait_ms), 0)
    if rounds <= 0:
        return collect_visible_comment_window_with_diagnostics(
            page,
            group_id=group_id,
            parent_post_id=parent_post_id,
            max_items=max_items,
            stop_reason="visible_window_completed",
            auto_load_more=False,
        )

    guard = begin_comment_load_more_guard(page)
    if not guard.get("acquired"):
        reason = str(guard.get("reason") or "comment_load_more_guard_active")
        return collect_visible_comment_window_with_diagnostics(
            page,
            group_id=group_id,
            parent_post_id=parent_post_id,
            max_items=max_items,
            stop_reason=reason,
            auto_load_more=True,
            guard_reason=reason,
        )

    return collect_comment_items_with_load_more_guard_held(
        page=page,
        group_id=group_id,
        parent_post_id=parent_post_id,
        max_items=max_items,
        scroll_rounds=rounds,
        scroll_wait_ms=wait_ms,
        auto_load_more=True,
    )


async def extract_visible_comment_items_async(
    page: Any,
    *,
    group_id: str,
    parent_post_id: str,
    max_items: int,
) -> tuple[list[ExtractedItem], CommentCollectionMeta]:
    """async 版本：抽取目前頁面已載入留言候選。"""

    raw_items = await page.evaluate(
        COMMENTS_LIKE_ITEMS_SCRIPT,
        {
            "groupId": group_id,
            "parentPostId": parent_post_id,
            "limit": max(max_items, 1),
        },
    )
    return normalize_comment_extraction_payload(raw_items, max_items=max_items)


async def collect_comment_items_with_diagnostics_async(
    page: Any,
    *,
    group_id: str,
    parent_post_id: str,
    max_items: int,
    scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
) -> tuple[list[ExtractedItem], list[CommentExtractRoundStats], CommentCollectionMeta]:
    """resident main 版本：跨可見視窗累積留言。"""

    rounds = max(int(scroll_rounds), 0) if auto_load_more else 0
    wait_ms = max(int(scroll_wait_ms), 0)
    if rounds <= 0:
        return await collect_visible_comment_window_with_diagnostics_async(
            page,
            group_id=group_id,
            parent_post_id=parent_post_id,
            max_items=max_items,
            stop_reason="visible_window_completed",
            auto_load_more=False,
        )

    guard = await begin_comment_load_more_guard_async(page)
    if not guard.get("acquired"):
        reason = str(guard.get("reason") or "comment_load_more_guard_active")
        return await collect_visible_comment_window_with_diagnostics_async(
            page,
            group_id=group_id,
            parent_post_id=parent_post_id,
            max_items=max_items,
            stop_reason=reason,
            auto_load_more=True,
            guard_reason=reason,
        )

    return await collect_comment_items_with_load_more_guard_held_async(
        page=page,
        group_id=group_id,
        parent_post_id=parent_post_id,
        max_items=max_items,
        scroll_rounds=rounds,
        scroll_wait_ms=wait_ms,
        auto_load_more=True,
    )


def collect_visible_comment_window_with_diagnostics(
    page: Any,
    *,
    group_id: str,
    parent_post_id: str,
    max_items: int,
    stop_reason: str,
    auto_load_more: bool,
    guard_reason: str = "",
) -> tuple[list[ExtractedItem], list[CommentExtractRoundStats], CommentCollectionMeta]:
    """收集單一 comments visible window，供 fallback 與 non-load-more 共用。"""

    settle = wait_for_comment_dom_settle(page, max_items=max_items)
    items, meta = extract_visible_comment_items(
        page,
        group_id=group_id,
        parent_post_id=parent_post_id,
        max_items=max_items,
    )
    round_stats = [
        build_comment_round_stats(
            round_index=0,
            items=items,
            meta=meta,
            accumulated_count=len(items),
            dom_settle=settle,
        )
    ]
    return items, round_stats, build_comment_collection_meta(
        target_count=max_items,
        round_stats=round_stats,
        accumulated_count=len(items),
        stop_reason=stop_reason,
        auto_load_more=auto_load_more,
        guard_reason=guard_reason,
    )


async def collect_visible_comment_window_with_diagnostics_async(
    page: Any,
    *,
    group_id: str,
    parent_post_id: str,
    max_items: int,
    stop_reason: str,
    auto_load_more: bool,
    guard_reason: str = "",
) -> tuple[list[ExtractedItem], list[CommentExtractRoundStats], CommentCollectionMeta]:
    """async 版本：收集單一 comments visible window。"""

    settle = await wait_for_comment_dom_settle_async(page, max_items=max_items)
    items, meta = await extract_visible_comment_items_async(
        page,
        group_id=group_id,
        parent_post_id=parent_post_id,
        max_items=max_items,
    )
    round_stats = [
        build_comment_round_stats(
            round_index=0,
            items=items,
            meta=meta,
            accumulated_count=len(items),
            dom_settle=settle,
        )
    ]
    return items, round_stats, build_comment_collection_meta(
        target_count=max_items,
        round_stats=round_stats,
        accumulated_count=len(items),
        stop_reason=stop_reason,
        auto_load_more=auto_load_more,
        guard_reason=guard_reason,
    )


def normalize_comment_extraction_payload(
    raw_payload: object,
    *,
    max_items: int,
) -> tuple[list[ExtractedItem], CommentCollectionMeta]:
    """整理 DOM extractor payload，並用 comment aliases 去重。"""

    raw_meta, raw_items = _split_comment_payload(raw_payload)
    items = _unique_normalized_comment_items(raw_items, max_items=max_items)
    return items, _comment_payload_meta(
        raw_meta,
        raw_items=raw_items,
        items=items,
        max_items=max_items,
    )


def _split_comment_payload(raw_payload: object) -> tuple[dict[str, Any], list[object]]:
    """拆出 DOM extractor payload 的 meta 與 items。"""

    if not isinstance(raw_payload, Mapping):
        return {}, raw_payload if isinstance(raw_payload, list) else []
    raw_items = raw_payload.get("items") or []
    return (
        dict(raw_payload.get("meta") or {}),
        raw_items if isinstance(raw_items, list) else [],
    )


def _unique_normalized_comment_items(
    raw_items: list[object],
    *,
    max_items: int,
) -> list[ExtractedItem]:
    """將 raw comments 轉成 ExtractedItem 並依 aliases 去重。"""

    collected: list[tuple[tuple[str, ...], ExtractedItem]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            continue
        item = _normalized_comment_item(raw_item)
        aliases = make_item_key_aliases(item)
        if not aliases:
            continue
        if any(aliases_overlap(aliases, existing_aliases) for existing_aliases, _ in collected):
            continue
        collected.append((aliases, item))
        if len(collected) >= max(max_items, 1):
            break
    return [item for _aliases, item in collected]


def _normalized_comment_item(raw_item: Mapping[str, Any]) -> ExtractedItem:
    """將單筆 comment DOM payload 轉成穩定 item model。"""

    cleaned_text = clean_facebook_text(raw_item.get("text") or "")
    display_text = clean_facebook_multiline_text(
        raw_item.get("displayText") or cleaned_text
    ) or cleaned_text
    return ExtractedItem(
        text=cleaned_text,
        text_length=len(cleaned_text),
        permalink=str(raw_item.get("permalink") or ""),
        link_count=int(raw_item.get("linkCount") or 0),
        display_text=display_text,
        author=str(raw_item.get("author") or ""),
        debug_metadata=normalize_comment_debug_metadata(raw_item),
        item_kind="comment",
        parent_post_id=str(raw_item.get("parentPostId") or ""),
        comment_id=str(raw_item.get("commentId") or ""),
    )


def _comment_payload_meta(
    raw_meta: Mapping[str, Any],
    *,
    raw_items: list[object],
    items: list[ExtractedItem],
    max_items: int,
) -> CommentCollectionMeta:
    """建立單次 visible-window extractor meta。"""

    return CommentCollectionMeta(
        target_count=max(max_items, 1),
        candidate_count=int(raw_meta.get("candidateCount") or len(raw_items)),
        parsed_count=int(raw_meta.get("parsedCount") or len(items)),
        accumulated_count=len(items),
        filtered_empty_text_count=int(raw_meta.get("filteredEmptyTextCount") or 0),
        filtered_non_post_count=int(raw_meta.get("filteredNonPostCount") or 0),
        article_element_count=int(raw_meta.get("articleElementCount") or 0),
        comments_with_comment_id_count=int(raw_meta.get("commentsWithCommentIdCount") or 0),
        filtered_out_of_scope_count=int(raw_meta.get("filteredOutOfScopeCount") or 0),
        comment_search_root_strategy=str(raw_meta.get("commentSearchRootStrategy") or ""),
        current_route_post_id=str(raw_meta.get("currentRoutePostId") or ""),
        current_route_matches_target=bool(raw_meta.get("currentRouteMatchesTarget")),
        stop_reason=str(raw_meta.get("stopReason") or "visible_window_completed"),
    )


def build_comment_round_stats(
    *,
    round_index: int,
    items: list[ExtractedItem],
    meta: CommentCollectionMeta,
    accumulated_count: int,
    scroll_action: dict[str, Any] | None = None,
    added_count: int = 0,
    stagnant_windows: int = 0,
    dom_settle: CommentDomSettleResult | None = None,
) -> CommentExtractRoundStats:
    """把單輪 comments 抽取與捲動結果整理成診斷資料。"""

    action = scroll_action or {}
    settle = dom_settle or CommentDomSettleResult(
        attempted=False,
        stable=False,
        observations=0,
        wait_ms=0,
    )
    return CommentExtractRoundStats(
        round_index=round_index,
        raw_item_count=len(items),
        unique_item_count=accumulated_count,
        candidate_count=meta.candidate_count,
        parsed_count=meta.parsed_count,
        accumulated_count=accumulated_count,
        filtered_empty_text_count=meta.filtered_empty_text_count,
        filtered_non_post_count=meta.filtered_non_post_count,
        article_element_count=meta.article_element_count,
        comments_with_comment_id_count=meta.comments_with_comment_id_count,
        filtered_out_of_scope_count=meta.filtered_out_of_scope_count,
        scroll_moved=bool(action.get("moved")) if action else None,
        scroll_target_label=str(action.get("targetLabel") or "") if action else "",
        scroll_before_top=int(action.get("beforeTop") or 0) if action else None,
        scroll_after_top=int(action.get("afterTop") or 0) if action else None,
        scroll_moved_distance=int(action.get("movedDistance") or 0) if action else None,
        scroll_step=int(action.get("scrollStep") or 0) if action else None,
        scroll_height=int(action.get("scrollHeight") or 0) if action else None,
        load_more_mode=str(action.get("loadMoreMode") or ("comment_nested_scroll" if action else "off")),
        added_count=added_count,
        stagnant_windows=stagnant_windows,
        dom_settle_attempted=settle.attempted,
        dom_settle_stable=settle.stable,
        dom_settle_observations=settle.observations,
        dom_settle_wait_ms=settle.wait_ms,
        dom_settle_candidate_count=settle.candidate_count,
    )


def build_comment_collection_meta(
    *,
    target_count: int,
    round_stats: list[CommentExtractRoundStats],
    accumulated_count: int,
    stop_reason: str,
    auto_load_more: bool,
    guard_reason: str = "",
) -> CommentCollectionMeta:
    """彙整 comments 跨視窗 collected meta。"""

    dom_settle_attempted = any(stat.dom_settle_attempted for stat in round_stats)
    return CommentCollectionMeta(
        target_count=max(int(target_count), 1),
        candidate_count=sum(stat.candidate_count for stat in round_stats),
        parsed_count=sum(stat.parsed_count for stat in round_stats),
        accumulated_count=accumulated_count,
        filtered_empty_text_count=sum(stat.filtered_empty_text_count for stat in round_stats),
        filtered_non_post_count=sum(stat.filtered_non_post_count for stat in round_stats),
        article_element_count=sum(stat.article_element_count for stat in round_stats),
        comments_with_comment_id_count=sum(
            stat.comments_with_comment_id_count for stat in round_stats
        ),
        filtered_out_of_scope_count=sum(stat.filtered_out_of_scope_count for stat in round_stats),
        mode="comments_nested_scroll" if auto_load_more else "comments_visible_window",
        attempted=any(stat.scroll_moved is not None for stat in round_stats),
        attempts=sum(1 for stat in round_stats if stat.scroll_moved is not None),
        before_count=round_stats[0].candidate_count if round_stats else 0,
        after_count=max((stat.candidate_count for stat in round_stats), default=0),
        window_count=len(round_stats),
        max_window_count=get_dynamic_max_windows(target_count) if auto_load_more else 1,
        stagnant_windows=round_stats[-1].stagnant_windows if round_stats else 0,
        load_more_mode=next(
            (stat.load_more_mode for stat in round_stats if stat.load_more_mode != "off"),
            "comment_nested_scroll" if auto_load_more else "off",
        ),
        guard_reason=guard_reason,
        stop_reason=stop_reason,
        dom_settle_attempted=dom_settle_attempted,
        dom_settle_stable=all(
            stat.dom_settle_stable
            for stat in round_stats
            if stat.dom_settle_attempted
        )
        if dom_settle_attempted
        else False,
        dom_settle_observations=sum(stat.dom_settle_observations for stat in round_stats),
        dom_settle_wait_ms=sum(stat.dom_settle_wait_ms for stat in round_stats),
        dom_settle_candidate_count=max(
            (stat.dom_settle_candidate_count for stat in round_stats),
            default=0,
        ),
    )


def merge_comment_items(
    *,
    collected: list[tuple[tuple[str, ...], ExtractedItem]],
    items: list[ExtractedItem],
    max_items: int,
) -> int:
    """將單視窗 comments 依 aliases 併入跨視窗累積結果。"""

    added_count = 0
    for item in items:
        item_aliases = make_item_key_aliases(item)
        if not item_aliases:
            continue
        if any(aliases_overlap(item_aliases, aliases) for aliases, _ in collected):
            continue
        collected.append((item_aliases, item))
        added_count += 1
        if len(collected) >= max(max_items, 1):
            break
    return added_count


def infer_comment_stop_reason(
    *,
    accumulated_count: int,
    target_count: int,
    round_stats: list[CommentExtractRoundStats],
    scroll_rounds: int,
    auto_load_more: bool,
) -> str:
    """依 comments 跨視窗狀態推斷停止原因。"""

    if accumulated_count >= target_count:
        return "target_count_reached"
    if not auto_load_more:
        return "auto_load_more_disabled"
    if not round_stats:
        return "no_comment_round_stats"
    if round_stats[-1].scroll_moved is False:
        return "comment_scroll_stalled"
    if round_stats[-1].stagnant_windows >= CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT:
        return "comment_stagnant_windows"
    if round_stats[-1].round_index >= max(scroll_rounds, 0):
        return "comment_scroll_rounds_completed"
    return "comment_collection_stopped"


def collect_comment_items_with_load_more_guard_held(
    *,
    page: Any,
    group_id: str,
    parent_post_id: str,
    max_items: int,
    scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
) -> tuple[list[ExtractedItem], list[CommentExtractRoundStats], CommentCollectionMeta]:
    """在已取得 guard 時執行 comments nested scroll 收集。"""

    collected: list[tuple[tuple[str, ...], ExtractedItem]] = []
    round_stats: list[CommentExtractRoundStats] = []
    stagnant_windows = 0
    snapshot_captured = False
    try:
        capture_comment_scroll_snapshot(page)
        snapshot_captured = True
        for round_index in range(max(scroll_rounds, 0) + 1):
            settle = wait_for_comment_dom_settle(page, max_items=max_items)
            items, meta = extract_visible_comment_items(
                page,
                group_id=group_id,
                parent_post_id=parent_post_id,
                max_items=max_items,
            )
            added_count = merge_comment_items(
                collected=collected,
                items=items,
                max_items=max_items,
            )
            stagnant_windows = stagnant_windows + 1 if added_count == 0 else 0
            scroll_action: dict[str, Any] = {}
            should_scroll = round_index < max(scroll_rounds, 0) and len(collected) < max_items
            if should_scroll:
                scroll_action = scroll_comment_load_more(page)
            round_stats.append(
                build_comment_round_stats(
                    round_index=round_index,
                    items=items,
                    meta=meta,
                    accumulated_count=len(collected),
                    scroll_action=scroll_action,
                    added_count=added_count,
                    stagnant_windows=stagnant_windows,
                    dom_settle=settle,
                )
            )
            if round_index >= max(scroll_rounds, 0) or len(collected) >= max_items:
                break
            if scroll_action and not bool(scroll_action.get("moved")):
                break
            if stagnant_windows >= CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT:
                break
            page.wait_for_timeout(max(scroll_wait_ms, 0))
    finally:
        try:
            if snapshot_captured:
                restore_comment_scroll_snapshot(page)
        finally:
            end_comment_load_more_guard(page)

    items = [item for _aliases, item in collected[:max(max_items, 1)]]
    stop_reason = infer_comment_stop_reason(
        accumulated_count=len(items),
        target_count=max(max_items, 1),
        round_stats=round_stats,
        scroll_rounds=scroll_rounds,
        auto_load_more=auto_load_more,
    )
    return items, round_stats, build_comment_collection_meta(
        target_count=max_items,
        round_stats=round_stats,
        accumulated_count=len(items),
        stop_reason=stop_reason,
        auto_load_more=auto_load_more,
    )


async def collect_comment_items_with_load_more_guard_held_async(
    *,
    page: Any,
    group_id: str,
    parent_post_id: str,
    max_items: int,
    scroll_rounds: int,
    scroll_wait_ms: int,
    auto_load_more: bool,
) -> tuple[list[ExtractedItem], list[CommentExtractRoundStats], CommentCollectionMeta]:
    """async 版本：在已取得 guard 時執行 comments nested scroll 收集。"""

    collected: list[tuple[tuple[str, ...], ExtractedItem]] = []
    round_stats: list[CommentExtractRoundStats] = []
    stagnant_windows = 0
    snapshot_captured = False
    try:
        await capture_comment_scroll_snapshot_async(page)
        snapshot_captured = True
        for round_index in range(max(scroll_rounds, 0) + 1):
            settle = await wait_for_comment_dom_settle_async(page, max_items=max_items)
            items, meta = await extract_visible_comment_items_async(
                page,
                group_id=group_id,
                parent_post_id=parent_post_id,
                max_items=max_items,
            )
            added_count = merge_comment_items(
                collected=collected,
                items=items,
                max_items=max_items,
            )
            stagnant_windows = stagnant_windows + 1 if added_count == 0 else 0
            scroll_action: dict[str, Any] = {}
            should_scroll = round_index < max(scroll_rounds, 0) and len(collected) < max_items
            if should_scroll:
                scroll_action = await scroll_comment_load_more_async(page)
            round_stats.append(
                build_comment_round_stats(
                    round_index=round_index,
                    items=items,
                    meta=meta,
                    accumulated_count=len(collected),
                    scroll_action=scroll_action,
                    added_count=added_count,
                    stagnant_windows=stagnant_windows,
                    dom_settle=settle,
                )
            )
            if round_index >= max(scroll_rounds, 0) or len(collected) >= max_items:
                break
            if scroll_action and not bool(scroll_action.get("moved")):
                break
            if stagnant_windows >= CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT:
                break
            await page.wait_for_timeout(max(scroll_wait_ms, 0))
    finally:
        try:
            if snapshot_captured:
                await restore_comment_scroll_snapshot_async(page)
        finally:
            await end_comment_load_more_guard_async(page)

    items = [item for _aliases, item in collected[:max(max_items, 1)]]
    stop_reason = infer_comment_stop_reason(
        accumulated_count=len(items),
        target_count=max(max_items, 1),
        round_stats=round_stats,
        scroll_rounds=scroll_rounds,
        auto_load_more=auto_load_more,
    )
    return items, round_stats, build_comment_collection_meta(
        target_count=max_items,
        round_stats=round_stats,
        accumulated_count=len(items),
        stop_reason=stop_reason,
        auto_load_more=auto_load_more,
    )


def normalize_comment_debug_metadata(item: Mapping[str, Any]) -> dict[str, Any]:
    """整理 comment DOM extractor 回傳的診斷欄位。"""

    keys = (
        "source",
        "containerRole",
        "textSource",
        "textDiagnostics",
        "textLength",
        "displayTextLength",
        "rawTextLength",
        "rawDisplayTextLength",
        "permalinkSource",
        "canonicalPermalinkCandidateCount",
        "parentPostId",
        "commentId",
        "commentAnchorHref",
        "routePostId",
        "routePostIdMatchesTarget",
        "routePostIdSource",
        "commentScopeReason",
        "commentSearchRoot",
        "commentSearchRootStrategy",
        "currentRoutePostId",
        "currentRouteMatchesTarget",
        "linkCount",
        "author",
        "groupId",
    )
    return {key: item.get(key) for key in keys if item.get(key) not in (None, "")}
