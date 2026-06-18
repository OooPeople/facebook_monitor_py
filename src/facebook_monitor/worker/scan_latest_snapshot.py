"""Latest scan snapshot helpers.

職責：把 shared scan classification 結果轉成 latest scan item snapshot。
本模組不執行 DB、notification 或 runtime side effects。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from typing import Protocol

from facebook_monitor.core.keyword_rules import KeywordGroupMatchResult
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import KeywordGroupMatch
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import TargetDescriptor


class LatestSnapshotItem(Protocol):
    """描述 latest snapshot 需要的 normalized item 欄位。"""

    @property
    def item_kind(self) -> ItemKind: ...

    @property
    def item_key(self) -> str: ...

    @property
    def author(self) -> str: ...

    @property
    def text(self) -> str: ...

    @property
    def display_text(self) -> str: ...

    @property
    def permalink(self) -> str: ...

    @property
    def metadata(self) -> dict[str, Any] | None: ...


class LatestSnapshotMatchResult(Protocol):
    """描述 latest snapshot 需要的 scan match result 欄位。"""

    @property
    def item(self) -> LatestSnapshotItem: ...

    @property
    def is_new(self) -> bool: ...

    @property
    def is_matched(self) -> bool: ...

    @property
    def include_rule(self) -> str: ...

    @property
    def exclude_rule(self) -> str: ...

    @property
    def eligible_for_notify(self) -> bool: ...

    @property
    def baseline_mode(self) -> bool: ...

    @property
    def matched_keyword(self) -> str: ...

    @property
    def matched_keywords(self) -> tuple[str, ...]: ...

    @property
    def matched_keyword_groups(self) -> tuple[KeywordGroupMatch, ...]: ...

    @property
    def include_group_results(self) -> tuple[KeywordGroupMatchResult, ...]: ...


def build_latest_scan_items(
    *,
    target: TargetDescriptor,
    scan_run_id: int,
    match_results: Sequence[LatestSnapshotMatchResult],
    previous_latest_items: Sequence[LatestScanItem] | None = None,
    target_count: int | None = None,
    carry_over_previous_items: bool = False,
) -> list[LatestScanItem]:
    """將 shared classification 結果轉成 latest scan snapshot。"""

    latest_items = [
        _latest_scan_item_from_match_result(
            target=target,
            scan_run_id=scan_run_id,
            item_index=item_index,
            result=result,
        )
        for item_index, result in enumerate(match_results)
    ]
    if not carry_over_previous_items:
        return latest_items

    _append_carried_over_previous_items(
        latest_items=latest_items,
        previous_latest_items=previous_latest_items,
        scan_run_id=scan_run_id,
        target_count=target_count,
    )
    return latest_items


def _latest_scan_item_from_match_result(
    *,
    target: TargetDescriptor,
    scan_run_id: int,
    item_index: int,
    result: LatestSnapshotMatchResult,
) -> LatestScanItem:
    """將單一分類結果轉成 latest scan item。"""

    return LatestScanItem(
        target_id=target.id,
        scan_run_id=scan_run_id,
        item_kind=result.item.item_kind,
        item_key=result.item.item_key,
        item_index=item_index,
        author=result.item.author,
        text=result.item.text,
        display_text=result.item.display_text or result.item.text,
        permalink=result.item.permalink,
        matched_keyword=result.matched_keyword,
        matched_keywords=result.matched_keywords,
        matched_keyword_groups=result.matched_keyword_groups,
        debug_metadata={
            **(result.item.metadata or {}),
            "classification": _classification_debug_metadata(result),
        },
    )


def _classification_debug_metadata(
    result: LatestSnapshotMatchResult,
) -> dict[str, Any]:
    """建立 latest snapshot 中的 keyword classification 診斷資料。"""

    return {
        "is_new": result.is_new,
        "is_matched": result.is_matched,
        "include_rule": result.include_rule,
        "include_rules": list(result.matched_keywords),
        "include_group_results": [
            {
                "group_id": group_result.group_id,
                "group_label": group_result.group_label,
                "matched": group_result.matched,
                "rules": list(group_result.rules),
            }
            for group_result in result.include_group_results
        ],
        "exclude_rule": result.exclude_rule,
        "eligible_for_notify": result.eligible_for_notify,
        "baseline_mode": result.baseline_mode,
    }


def _append_carried_over_previous_items(
    *,
    latest_items: list[LatestScanItem],
    previous_latest_items: Sequence[LatestScanItem] | None,
    scan_run_id: int,
    target_count: int | None,
) -> None:
    """將 seen-stop 保留的前次 latest items 補到本輪 snapshot 後方。"""

    existing_item_keys = {item.item_key for item in latest_items}
    limit = max(int(target_count or len(latest_items)), len(latest_items))
    for previous_item in previous_latest_items or []:
        if len(latest_items) >= limit:
            break
        if previous_item.item_key in existing_item_keys:
            continue
        existing_item_keys.add(previous_item.item_key)
        latest_items.append(
            replace(
                previous_item,
                scan_run_id=scan_run_id,
                item_index=len(latest_items),
                debug_metadata={
                    **(previous_item.debug_metadata or {}),
                    "carriedOverFromPreviousScan": True,
                    "carriedOverFromScanRunId": previous_item.scan_run_id,
                },
            )
        )


def should_carry_over_previous_latest_items(metadata: dict[str, Any]) -> bool:
    """seen-stop 提早停止時，用上一輪 latest snapshot 補足 UI 可檢視項目。"""

    collected_meta = metadata.get("collected_meta")
    if isinstance(collected_meta, dict) and collected_meta.get("seenStopTriggered") is True:
        return True
    return metadata.get("stop_reason") == "seen_stop_consecutive_seen"


__all__ = [
    "LatestSnapshotItem",
    "LatestSnapshotMatchResult",
    "build_latest_scan_items",
    "should_carry_over_previous_latest_items",
]
