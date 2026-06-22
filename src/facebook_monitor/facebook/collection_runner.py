"""Facebook collection loop runner。

職責：集中 feed / comments 多輪收集的 loop、stagnant window、scroll 與 cleanup
流程；domain-specific DOM 抽取、去重、round stats 與 metadata shape 仍由呼叫端提供。
"""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
from typing import Any
from typing import Generic
from typing import TypeVar

from facebook_monitor.facebook.collection_policy import (
    CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT,
)
from facebook_monitor.facebook.extracted_item import ExtractedItem


RoundMetaT = TypeVar("RoundMetaT")
RoundStatsT = TypeVar("RoundStatsT")
RoundExtraT = TypeVar("RoundExtraT")
type CollectedItems = list[tuple[tuple[str, ...], ExtractedItem]]
type MergeItems = Callable[[CollectedItems, list[ExtractedItem], int], int]


@dataclass(frozen=True)
class CollectionRoundObservation(Generic[RoundMetaT, RoundExtraT]):
    """保存單輪 DOM 抽取與 domain-specific 額外診斷。"""

    items: list[ExtractedItem]
    meta: RoundMetaT
    extra: RoundExtraT | None = None


@dataclass(frozen=True)
class CollectionRoundContext(Generic[RoundMetaT, RoundExtraT]):
    """提供 round stats builder 需要的共同 loop 狀態。"""

    round_index: int
    items: list[ExtractedItem]
    meta: RoundMetaT
    extra: RoundExtraT | None
    accumulated_count: int
    added_count: int
    stagnant_windows: int
    scroll_action: dict[str, Any]
    scroll_metrics: dict[str, Any]
    scroll_rounds: int


type RoundStatsBuilder[RoundMetaT, RoundExtraT, RoundStatsT] = Callable[
    [CollectionRoundContext[RoundMetaT, RoundExtraT]],
    RoundStatsT,
]
type CollectionLoopPredicate[RoundMetaT, RoundExtraT] = Callable[
    [CollectionRoundContext[RoundMetaT, RoundExtraT]],
    bool,
]


@dataclass(frozen=True)
class CollectionRunResult(Generic[RoundStatsT]):
    """保存 collection runner 完成後的累積 items 與 round stats。"""

    collected: CollectedItems
    round_stats: list[RoundStatsT]


def run_collection_loop(
    *,
    rounds: int,
    wait_ms: int,
    target_count: int,
    collect_round: Callable[[int], CollectionRoundObservation[RoundMetaT, RoundExtraT]],
    merge_items: MergeItems,
    build_round_stats: RoundStatsBuilder[RoundMetaT, RoundExtraT, RoundStatsT],
    should_scroll: CollectionLoopPredicate[RoundMetaT, RoundExtraT],
    should_stop: CollectionLoopPredicate[RoundMetaT, RoundExtraT],
    wait: Callable[[int], object],
    scroll: Callable[[], dict[str, Any]],
    get_scroll_metrics: Callable[[], dict[str, Any]] | None = None,
    capture_snapshot: Callable[[], object] | None = None,
    restore_snapshot: Callable[[], object] | None = None,
    end_guard: Callable[[], object] | None = None,
) -> CollectionRunResult[RoundStatsT]:
    """執行同步多輪 collection loop。"""

    collected: CollectedItems = []
    round_stats: list[RoundStatsT] = []
    stagnant_windows = 0
    snapshot_captured = False
    try:
        if capture_snapshot is not None:
            capture_snapshot()
            snapshot_captured = True
        for round_index in range(max(rounds, 0) + 1):
            observation = collect_round(round_index)
            added_count = merge_items(collected, observation.items, round_index)
            stagnant_windows = stagnant_windows + 1 if added_count == 0 else 0
            scroll_metrics = get_scroll_metrics() if get_scroll_metrics is not None else {}
            context = CollectionRoundContext(
                round_index=round_index,
                items=observation.items,
                meta=observation.meta,
                extra=observation.extra,
                accumulated_count=len(collected),
                added_count=added_count,
                stagnant_windows=stagnant_windows,
                scroll_action={},
                scroll_metrics=scroll_metrics,
                scroll_rounds=max(rounds, 0),
            )
            scroll_action: dict[str, Any] = {}
            stop_before_scroll = should_stop(context)
            if (
                not stop_before_scroll
                and _default_scroll_allowed(context, target_count=target_count)
                and should_scroll(context)
            ):
                scroll_action = scroll()
            context = replace(context, scroll_action=scroll_action)
            round_stats.append(build_round_stats(context))
            if (
                stop_before_scroll
                or _default_stop_requested(context, target_count=target_count)
                or should_stop(context)
            ):
                break
            wait(wait_ms)
    finally:
        try:
            if snapshot_captured and restore_snapshot is not None:
                restore_snapshot()
        finally:
            if end_guard is not None:
                end_guard()
    return CollectionRunResult(collected=collected, round_stats=round_stats)


async def run_collection_loop_async(
    *,
    rounds: int,
    wait_ms: int,
    target_count: int,
    collect_round: Callable[
        [int],
        Awaitable[CollectionRoundObservation[RoundMetaT, RoundExtraT]],
    ],
    merge_items: MergeItems,
    build_round_stats: RoundStatsBuilder[RoundMetaT, RoundExtraT, RoundStatsT],
    should_scroll: CollectionLoopPredicate[RoundMetaT, RoundExtraT],
    should_stop: CollectionLoopPredicate[RoundMetaT, RoundExtraT],
    wait: Callable[[int], Awaitable[object]],
    scroll: Callable[[], Awaitable[dict[str, Any]]],
    get_scroll_metrics: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    capture_snapshot: Callable[[], Awaitable[object]] | None = None,
    restore_snapshot: Callable[[], Awaitable[object]] | None = None,
    end_guard: Callable[[], Awaitable[object]] | None = None,
) -> CollectionRunResult[RoundStatsT]:
    """執行 async 多輪 collection loop。"""

    collected: CollectedItems = []
    round_stats: list[RoundStatsT] = []
    stagnant_windows = 0
    snapshot_captured = False
    try:
        if capture_snapshot is not None:
            await capture_snapshot()
            snapshot_captured = True
        for round_index in range(max(rounds, 0) + 1):
            observation = await collect_round(round_index)
            added_count = merge_items(collected, observation.items, round_index)
            stagnant_windows = stagnant_windows + 1 if added_count == 0 else 0
            scroll_metrics = (
                await get_scroll_metrics() if get_scroll_metrics is not None else {}
            )
            context = CollectionRoundContext(
                round_index=round_index,
                items=observation.items,
                meta=observation.meta,
                extra=observation.extra,
                accumulated_count=len(collected),
                added_count=added_count,
                stagnant_windows=stagnant_windows,
                scroll_action={},
                scroll_metrics=scroll_metrics,
                scroll_rounds=max(rounds, 0),
            )
            scroll_action: dict[str, Any] = {}
            stop_before_scroll = should_stop(context)
            if (
                not stop_before_scroll
                and _default_scroll_allowed(context, target_count=target_count)
                and should_scroll(context)
            ):
                scroll_action = await scroll()
            context = replace(context, scroll_action=scroll_action)
            round_stats.append(build_round_stats(context))
            if (
                stop_before_scroll
                or _default_stop_requested(context, target_count=target_count)
                or should_stop(context)
            ):
                break
            await wait(wait_ms)
    finally:
        try:
            if snapshot_captured and restore_snapshot is not None:
                await restore_snapshot()
        finally:
            if end_guard is not None:
                await end_guard()
    return CollectionRunResult(collected=collected, round_stats=round_stats)


def _default_stop_requested(
    context: CollectionRoundContext[Any, Any],
    *,
    target_count: int,
) -> bool:
    """套用 feed/comments 共同停止條件。"""

    if context.round_index >= context.scroll_rounds:
        return True
    if context.accumulated_count >= target_count:
        return True
    if context.scroll_action and not bool(context.scroll_action.get("moved")):
        return True
    return context.stagnant_windows >= CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT


def _default_scroll_allowed(
    context: CollectionRoundContext[Any, Any],
    *,
    target_count: int,
) -> bool:
    """在 domain hook 前先套用共同 scroll 前置條件。"""

    if context.round_index >= context.scroll_rounds:
        return False
    return context.accumulated_count < target_count


__all__ = [
    "CollectedItems",
    "CollectionRoundContext",
    "CollectionRoundObservation",
    "CollectionRunResult",
    "run_collection_loop",
    "run_collection_loop_async",
]
