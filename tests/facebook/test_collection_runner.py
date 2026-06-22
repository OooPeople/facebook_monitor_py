"""Facebook collection runner tests。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from facebook_monitor.facebook.collection_policy import (
    CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT,
)
from facebook_monitor.facebook.collection_runner import CollectedItems
from facebook_monitor.facebook.collection_runner import CollectionRoundContext
from facebook_monitor.facebook.collection_runner import CollectionRoundObservation
from facebook_monitor.facebook.collection_runner import run_collection_loop
from facebook_monitor.facebook.collection_runner import run_collection_loop_async
from facebook_monitor.facebook.extracted_item import ExtractedItem


def _item(index: int) -> ExtractedItem:
    """建立有穩定 alias 的測試 item。"""

    return ExtractedItem(
        text=f"item {index}",
        text_length=6,
        permalink=f"https://example.test/posts/{index}",
        link_count=0,
    )


def _merge_items(
    collected: CollectedItems,
    items: list[ExtractedItem],
    _round_index: int,
) -> int:
    """測試用 merge hook，保留 runner 對 accumulated count 的觀察。"""

    previous_count = len(collected)
    for item in items:
        collected.append(((item.permalink,), item))
    return len(collected) - previous_count


def test_collection_loop_stops_at_scroll_rounds_without_terminal_wait() -> None:
    """runner 跑完最後 round 後不應再 wait。"""

    rounds_seen: list[int] = []
    waits: list[int] = []
    scrolls: list[bool] = []

    def collect_round(round_index: int) -> CollectionRoundObservation[dict[str, Any], None]:
        rounds_seen.append(round_index)
        return CollectionRoundObservation(items=[_item(round_index)], meta={})

    def build_stats(
        context: CollectionRoundContext[dict[str, Any], None],
    ) -> tuple[int, int, int, bool]:
        return (
            context.round_index,
            context.accumulated_count,
            context.added_count,
            bool(context.scroll_action),
        )

    def scroll() -> dict[str, Any]:
        scrolls.append(True)
        return {"moved": True}

    result = run_collection_loop(
        rounds=2,
        wait_ms=25,
        target_count=10,
        collect_round=collect_round,
        merge_items=_merge_items,
        build_round_stats=build_stats,
        should_scroll=lambda context: context.round_index < context.scroll_rounds,
        should_stop=lambda _context: False,
        wait=waits.append,
        scroll=scroll,
    )

    assert rounds_seen == [0, 1, 2]
    assert waits == [25, 25]
    assert scrolls == [True, True]
    assert [stats[0] for stats in result.round_stats] == [0, 1, 2]
    assert [stats[1] for stats in result.round_stats] == [1, 2, 3]
    assert len(result.collected) == 3


def test_collection_loop_stops_at_target_count() -> None:
    """達到 target count 時不再 scroll 或 wait。"""

    rounds_seen: list[int] = []
    waits: list[int] = []
    scrolls: list[int] = []

    def collect_round(
        round_index: int,
    ) -> CollectionRoundObservation[dict[str, Any], None]:
        rounds_seen.append(round_index)
        return CollectionRoundObservation(items=[_item(round_index)], meta={})

    def scroll() -> dict[str, Any]:
        scrolls.append(1)
        return {"moved": True}

    result = run_collection_loop(
        rounds=10,
        wait_ms=10,
        target_count=2,
        collect_round=collect_round,
        merge_items=_merge_items,
        build_round_stats=lambda context: context.accumulated_count,
        should_scroll=lambda _context: True,
        should_stop=lambda _context: False,
        wait=waits.append,
        scroll=scroll,
    )

    assert rounds_seen == [0, 1]
    assert waits == [10]
    assert scrolls == [1]
    assert result.round_stats == [1, 2]
    assert len(result.collected) == 2


def test_collection_loop_domain_stop_prevents_scroll_but_keeps_stats() -> None:
    """domain stop 應在 scroll 前生效，但本輪 stats 仍要保留。"""

    waits: list[int] = []
    scrolls: list[int] = []

    def scroll() -> dict[str, Any]:
        scrolls.append(1)
        return {"moved": True}

    result = run_collection_loop(
        rounds=10,
        wait_ms=10,
        target_count=10,
        collect_round=lambda round_index: CollectionRoundObservation(
            items=[_item(round_index)],
            meta={},
        ),
        merge_items=_merge_items,
        build_round_stats=lambda context: (
            context.accumulated_count,
            bool(context.scroll_action),
        ),
        should_scroll=lambda _context: True,
        should_stop=lambda context: context.accumulated_count >= 1,
        wait=waits.append,
        scroll=scroll,
    )

    assert waits == []
    assert scrolls == []
    assert result.round_stats == [(1, False)]


def test_collection_loop_stops_when_scroll_stalls() -> None:
    """scroll action 明確回報未移動時停止。"""

    waits: list[int] = []
    scrolls: list[int] = []

    def scroll() -> dict[str, Any]:
        scrolls.append(1)
        return {"moved": False}

    result = run_collection_loop(
        rounds=10,
        wait_ms=10,
        target_count=10,
        collect_round=lambda round_index: CollectionRoundObservation(
            items=[_item(round_index)],
            meta={},
        ),
        merge_items=_merge_items,
        build_round_stats=lambda context: bool(context.scroll_action.get("moved")),
        should_scroll=lambda context: context.round_index < context.scroll_rounds,
        should_stop=lambda _context: False,
        wait=waits.append,
        scroll=scroll,
    )

    assert waits == []
    assert scrolls == [1]
    assert result.round_stats == [False]
    assert len(result.collected) == 1


def test_collection_loop_stops_after_stagnant_windows() -> None:
    """連續沒有新增 item 的視窗達門檻時停止。"""

    waits: list[int] = []
    scrolls: list[int] = []

    def scroll() -> dict[str, Any]:
        scrolls.append(1)
        return {"moved": True}

    result = run_collection_loop(
        rounds=10,
        wait_ms=10,
        target_count=5,
        collect_round=lambda _round_index: CollectionRoundObservation(
            items=[],
            meta={},
        ),
        merge_items=_merge_items,
        build_round_stats=lambda context: context.stagnant_windows,
        should_scroll=lambda context: context.round_index < context.scroll_rounds,
        should_stop=lambda _context: False,
        wait=waits.append,
        scroll=scroll,
    )

    assert result.round_stats == list(
        range(1, CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT + 1)
    )
    assert waits == [10] * (CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT - 1)
    assert scrolls == [1] * CONSECUTIVE_STAGNANT_WINDOW_STOP_COUNT
    assert result.collected == []


def test_collection_loop_restores_snapshot_and_ends_guard_on_round_error() -> None:
    """單輪抽取失敗時仍執行 snapshot restore 與 guard release。"""

    events: list[str] = []

    def collect_round(_round_index: int) -> CollectionRoundObservation[dict[str, Any], None]:
        raise RuntimeError("round failed")

    with pytest.raises(RuntimeError, match="round failed"):
        run_collection_loop(
            rounds=1,
            wait_ms=0,
            target_count=1,
            collect_round=collect_round,
            merge_items=_merge_items,
            build_round_stats=lambda _context: None,
            should_scroll=lambda _context: False,
            should_stop=lambda _context: False,
            wait=lambda _milliseconds: events.append("wait"),
            scroll=lambda: {"moved": True},
            capture_snapshot=lambda: events.append("capture"),
            restore_snapshot=lambda: events.append("restore"),
            end_guard=lambda: events.append("end_guard"),
        )

    assert events == ["capture", "restore", "end_guard"]


def test_collection_loop_async_matches_sync_target_count_stop() -> None:
    """async runner 與 sync runner 使用相同 target-count 停止規則。"""

    async def run_test() -> None:
        rounds_seen: list[int] = []
        waits: list[int] = []
        scrolls: list[int] = []

        async def collect_round(
            round_index: int,
        ) -> CollectionRoundObservation[dict[str, Any], None]:
            rounds_seen.append(round_index)
            return CollectionRoundObservation(items=[_item(round_index)], meta={})

        async def wait(milliseconds: int) -> None:
            waits.append(milliseconds)

        async def scroll() -> dict[str, Any]:
            scrolls.append(1)
            return {"moved": True}

        result = await run_collection_loop_async(
            rounds=10,
            wait_ms=10,
            target_count=2,
            collect_round=collect_round,
            merge_items=_merge_items,
            build_round_stats=lambda context: context.accumulated_count,
            should_scroll=lambda _context: True,
            should_stop=lambda _context: False,
            wait=wait,
            scroll=scroll,
        )

        assert rounds_seen == [0, 1]
        assert waits == [10]
        assert scrolls == [1]
        assert result.round_stats == [1, 2]
        assert len(result.collected) == 2

    asyncio.run(run_test())


def test_collection_loop_async_domain_stop_prevents_scroll_but_keeps_stats() -> None:
    """async domain stop 也必須在 scroll 前生效並保留 stats。"""

    async def run_test() -> None:
        waits: list[int] = []
        scrolls: list[int] = []

        async def collect_round(
            round_index: int,
        ) -> CollectionRoundObservation[dict[str, Any], None]:
            return CollectionRoundObservation(items=[_item(round_index)], meta={})

        async def wait(milliseconds: int) -> None:
            waits.append(milliseconds)

        async def scroll() -> dict[str, Any]:
            scrolls.append(1)
            return {"moved": True}

        result = await run_collection_loop_async(
            rounds=10,
            wait_ms=10,
            target_count=10,
            collect_round=collect_round,
            merge_items=_merge_items,
            build_round_stats=lambda context: (
                context.accumulated_count,
                bool(context.scroll_action),
            ),
            should_scroll=lambda _context: True,
            should_stop=lambda context: context.accumulated_count >= 1,
            wait=wait,
            scroll=scroll,
        )

        assert waits == []
        assert scrolls == []
        assert result.round_stats == [(1, False)]

    asyncio.run(run_test())
