"""Resident main shared runtime helpers。

職責：提供正式 resident main 與 executor 共同使用的 options、summary、
target loading、route 判斷與失敗狀態寫回 helper。sync fallback worker
不放在本檔，避免 debug/fallback 與正式主路徑混名。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.facebook.route_detection import FACEBOOK_HOSTS
from facebook_monitor.facebook.route_detection import RouteDetectionError
from facebook_monitor.facebook.route_detection import detect_group_comments_route
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.scan_failure_finalize import record_scan_failure
from facebook_monitor.worker.target_validation import validate_posts_target_route


@dataclass(frozen=True)
class ResidentRuntimeOptions:
    """保存 resident main worker 執行選項。"""

    db_path: Path
    profile_dir: Path
    interval_seconds: float = 60
    scheduler_tick_seconds: float = 2
    max_concurrent_scans: int = 2
    scroll_rounds: int = 3
    scroll_wait_ms: int = 2500
    scan_timeout_seconds: float = 120
    stale_running_after_seconds: float = 180
    headed_compat: bool = False
    max_cycles: int | None = None


@dataclass(frozen=True)
class ResidentCycleSummary:
    """保存 resident main worker 單輪摘要。"""

    cycle_index: int
    selected_count: int
    success_count: int
    failure_count: int
    skipped_count: int
    opened_page_count: int
    reused_page_count: int
    closed_page_count: int
    queued_count: int = 0
    running_count: int = 0
    queue_length: int = 0
    queued_target_ids: tuple[str, ...] = ()
    worker_ids: tuple[str, ...] = ()
    page_pool_size: int = 0
    resident_browser_alive: bool = False


@dataclass(frozen=True)
class ResidentTarget:
    """保存 resident main worker 單次掃描需要的 target 與 config。"""

    target: TargetDescriptor
    config: TargetConfig


def list_active_resident_target_ids(db_path: Path) -> set[str]:
    """列出目前 desired active 且 resident 支援掃描的 target ids。"""

    with SqliteApplicationContext(db_path) as app:
        target_ids: set[str] = set()
        for target in app.repositories.targets.list_enabled():
            if target.target_kind not in {TargetKind.POSTS, TargetKind.COMMENTS}:
                continue
            runtime_state = app.services.targets.ensure_runtime_state(target.id)
            if runtime_state.desired_state == TargetDesiredState.ACTIVE:
                target_ids.add(target.id)
        return target_ids


def load_resident_target(db_path: Path, target_id: str) -> ResidentTarget:
    """載入 resident main worker 掃描 target 所需資料。"""

    with SqliteApplicationContext(db_path) as app:
        target = app.repositories.targets.get(target_id)
        if target is None:
            raise WorkerFailure("target_missing", f"Target not found: {target_id}")
        validate_resident_target_route(target)
        config = app.services.targets.get_config_for_target(target)
        return ResidentTarget(target=target, config=config)


def validate_resident_target_route(target: TargetDescriptor) -> None:
    """確認 resident main worker 支援 target kind 與 canonical route。"""

    if target.target_kind == TargetKind.POSTS:
        validate_posts_target_route(target)
        return
    if target.target_kind == TargetKind.COMMENTS:
        try:
            route = detect_group_comments_route(target.canonical_url)
        except RouteDetectionError as exc:
            raise WorkerFailure("target_invalid", str(exc)) from exc
        if route.group_id != target.group_id or route.parent_post_id != target.parent_post_id:
            raise WorkerFailure("target_invalid", "Comments target route does not match saved scope.")
        return
    raise WorkerFailure("target_kind_unsupported", f"Unsupported target kind: {target.target_kind.value}")


def should_reload_resident_page(current_url: str, canonical_url: str) -> bool:
    """判斷 resident page 是否已在同一 target route，可用 reload 取代 goto。"""

    current_key = _resident_route_key(current_url)
    canonical_key = _resident_route_key(canonical_url)
    return bool(current_key and current_key == canonical_key)


def _resident_route_key(url: str) -> tuple[str, ...] | None:
    """回傳 resident target route key，支援 posts feed 與 comments parent post。"""

    return _group_post_route_key(url) or _group_feed_route_key(url)


def _group_post_route_key(url: str) -> tuple[str, str, str] | None:
    """回傳 Facebook group post route key，供 comments target reload 判斷。"""

    try:
        route = detect_group_comments_route(url)
    except RouteDetectionError:
        return None
    return ("comments", route.group_id, route.parent_post_id)


def _group_feed_route_key(url: str) -> tuple[str, str] | None:
    """回傳 Facebook group feed route key；單篇貼文或 groups 入口不視為 feed。"""

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in FACEBOOK_HOSTS:
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 2 or path_parts[0] != "groups":
        return None
    group_id = path_parts[1].strip()
    if not group_id or group_id.lower() == "feed":
        return None
    return ("groups", group_id)


def record_resident_scan_failure(
    db_path: Path,
    target: TargetDescriptor,
    reason: str,
    message: str,
    *,
    retryable: bool = False,
    exception_class: str = "",
    page_reused: bool | None = None,
) -> None:
    """記錄 resident main worker 的失敗 scan run。"""

    with SqliteApplicationContext(db_path) as app:
        record_scan_failure(
            app=app,
            target=target,
            reason=reason,
            message=message,
            worker_path="resident_main",
            retryable=retryable,
            exception_class=exception_class,
            page_reused=page_reused,
        )


def mark_resident_target_error(db_path: Path, target_id: str, message: str) -> None:
    """將 target runtime state 標成 error；target 已不存在時忽略。"""

    with SqliteApplicationContext(db_path) as app:
        if app.repositories.targets.get(target_id) is None:
            return
        app.services.targets.mark_target_error(target_id, message)


def mark_resident_target_idle(db_path: Path, target_id: str) -> None:
    """將 target runtime state 標回 idle；target 已不存在時忽略。"""

    with SqliteApplicationContext(db_path) as app:
        if app.repositories.targets.get(target_id) is None:
            return
        app.services.targets.mark_target_idle(target_id)
