"""Formal resident commit-ready scan result 的驗證 helper。"""

from __future__ import annotations

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.scan_pipeline_results import ProtectiveSkipScanResult
from facebook_monitor.worker.scan_pipeline_results import SuccessScanResult


def validate_protective_skip_result_for_target(
    *,
    target: TargetDescriptor,
    result: ProtectiveSkipScanResult,
) -> None:
    """確認 protective skip result 屬於本次要 commit 的 target。"""

    if result.target_id != target.id:
        raise WorkerFailure(
            "scan_result_target_mismatch",
            "scanner returned protective skip result for a different target",
        )


def validate_success_scan_result_for_target(
    *,
    target: TargetDescriptor,
    result: SuccessScanResult,
) -> None:
    """確認 success result 與 item identity 都屬於本次 target。"""

    if result.target_id != target.id:
        raise WorkerFailure(
            "scan_result_target_mismatch",
            "scanner returned success result for a different target",
        )
    expected_item_kind = expected_item_kind_for_target(target)
    for item in result.items:
        if item.group_id != target.group_id:
            raise WorkerFailure(
                "scan_result_target_mismatch",
                "scanner returned item for a different group",
            )
        if item.item_kind != expected_item_kind:
            raise WorkerFailure(
                "scan_result_target_mismatch",
                "scanner returned item kind that does not match target kind",
            )
        if item.raw_target_kind and item.raw_target_kind != target.target_kind.value:
            raise WorkerFailure(
                "scan_result_target_mismatch",
                "scanner returned item raw target kind that does not match target",
            )
        if (
            target.target_kind == TargetKind.COMMENTS
            and item.parent_post_id != target.parent_post_id
        ):
            raise WorkerFailure(
                "scan_result_target_mismatch",
                "scanner returned comment item for a different parent post",
            )


def expected_item_kind_for_target(target: TargetDescriptor) -> ItemKind:
    """回傳 target kind 對應的 normalized item kind。"""

    if target.target_kind == TargetKind.COMMENTS:
        return ItemKind.COMMENT
    return ItemKind.POST


__all__ = [
    "expected_item_kind_for_target",
    "validate_protective_skip_result_for_target",
    "validate_success_scan_result_for_target",
]
