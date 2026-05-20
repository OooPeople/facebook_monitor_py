"""Worker target route validation helpers。

職責：保存 worker 共用的 target route 驗證，避免 resident runtime 依賴
one-shot dispatch 模組。
"""

from __future__ import annotations

from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.scan_failures import TARGET_INVALID_REASON
from facebook_monitor.facebook.route_detection import RouteDetectionError
from facebook_monitor.facebook.route_detection import detect_group_posts_route
from facebook_monitor.worker.errors import WorkerFailure


def validate_posts_target_route(target: TargetDescriptor) -> None:
    """確認已保存 target 仍是支援的 group posts route。"""

    try:
        detect_group_posts_route(target.canonical_url)
    except RouteDetectionError as exc:
        raise WorkerFailure(TARGET_INVALID_REASON, str(exc)) from exc


def is_valid_posts_target_route(target: TargetDescriptor) -> bool:
    """回傳 posts target route 是否可被目前 worker 支援。"""

    try:
        validate_posts_target_route(target)
    except WorkerFailure:
        return False
    return True
