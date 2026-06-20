"""Resident target cover image refresh job。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_registry_service import InvalidTargetMetadataError
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import CoverImageRefreshRequestStatus
from facebook_monitor.core.models import TargetCoverImageRefreshResult
from facebook_monitor.core.models import TargetCoverImageRefreshState
from facebook_monitor.facebook.group_metadata import (
    AsyncBrowserContextLike as GroupMetadataBrowserContextLike,
)
from facebook_monitor.facebook.group_metadata import GroupMetadataError
from facebook_monitor.facebook.group_metadata import resolve_group_cover_image_with_context
from facebook_monitor.facebook.group_metadata_validation import has_polluted_group_cover_image_url
from facebook_monitor.worker.resident_maintenance_errors import StopCheckCallable
from facebook_monitor.worker.resident_maintenance_errors import (
    filter_maintenance_cover_refresh_states,
)
from facebook_monitor.worker.resident_maintenance_errors import (
    filter_maintenance_refresh_target_ids,
)
from facebook_monitor.worker.resident_maintenance_errors import format_exception_message
from facebook_monitor.worker.resident_maintenance_errors import (
    handle_maintenance_refresh_exception,
)
from facebook_monitor.worker.resident_maintenance_errors import (
    is_scheduler_runtime_refresh_failure,
)
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions


logger = logging.getLogger(__name__)
COVER_IMAGE_REFRESH_TARGET_LIMIT_PER_TICK = (
    PYTHON_SCHEDULER_RUNTIME_DEFAULTS.cover_image_refresh_target_limit_per_tick
)


@dataclass(frozen=True)
class _CoverImageRefreshAttempt:
    """保存單一 cover image refresh attempt 的已鎖定輸入。"""

    target_id: str
    group_id: str
    reported_url: str
    requested_at: datetime | None


async def refresh_pending_target_cover_images(
    *,
    options: ResidentRuntimeOptions,
    browser_context: GroupMetadataBrowserContextLike | None,
    should_stop: StopCheckCallable | None = None,
    request_runtime_restart: Callable[[], None] | None = None,
) -> int:
    """消化 dashboard 壞圖上報排入的 image-only cover refresh jobs。"""

    stop_requested = should_stop or (lambda: False)
    if browser_context is None or stop_requested():
        return 0
    queue_polluted_cover_image_refresh_candidates(options)
    refreshed_count = 0
    states = list_eligible_pending_cover_image_refreshes(
        options,
        limit=COVER_IMAGE_REFRESH_TARGET_LIMIT_PER_TICK,
    )
    for state in states:
        if stop_requested():
            break
        try:
            if await refresh_target_group_cover_image_from_context(
                options=options,
                browser_context=browser_context,
                state=state,
            ):
                refreshed_count += 1
        except Exception as exc:
            failure_message = format_exception_message(exc)
            should_break = handle_maintenance_refresh_exception(
                options=options,
                target_id=state.target_id,
                exc=exc,
                stop_requested=stop_requested,
                request_runtime_restart=request_runtime_restart,
                shutdown_log_message=("cover image refresh skipped because scheduler is stopping"),
                runtime_restart_log_message=(
                    "cover image refresh requested browser runtime restart"
                ),
                failure_log_message="cover image refresh failed",
                mark_failed=lambda: mark_target_cover_image_refresh_failed(
                    options,
                    state.target_id,
                    failure_message,
                    reported_url=state.last_reported_url,
                    requested_at=state.requested_at,
                ),
            )
            if should_break:
                break
    return refreshed_count


def queue_polluted_cover_image_refresh_candidates(options: ResidentRuntimeOptions) -> int:
    """將已知 Facebook 通用封面圖排入既有 image-only refresh queue。"""

    queued_count = 0
    inspected_ids: set[str] = set()
    batch_limit = max(COVER_IMAGE_REFRESH_TARGET_LIMIT_PER_TICK * 4, 1)
    while queued_count < COVER_IMAGE_REFRESH_TARGET_LIMIT_PER_TICK:
        with SqliteApplicationContext(options.db_path) as app:
            targets = app.repositories.targets.list_polluted_group_cover_image_candidates(
                limit=batch_limit,
                exclude_ids=tuple(inspected_ids),
            )
        if not targets:
            break
        inspected_ids.update(target.id for target in targets)
        eligible_target_ids = set(
            filter_maintenance_refresh_target_ids(
                options,
                tuple(target.id for target in targets),
            )
        )
        with SqliteApplicationContext(options.db_path) as app:
            for target in targets:
                if target.id not in eligible_target_ids:
                    continue
                cover_refresh = app.services.target_cover_image_refresh
                result = cover_refresh.request_refresh_for_current_url(
                    target.id,
                    reported_url=target.group_cover_image_url,
                    min_interval_seconds=(
                        PYTHON_SCHEDULER_RUNTIME_DEFAULTS.cover_image_load_failure_min_interval_seconds
                    ),
                )
                if result.status == CoverImageRefreshRequestStatus.QUEUED:
                    queued_count += 1
                    if queued_count >= COVER_IMAGE_REFRESH_TARGET_LIMIT_PER_TICK:
                        break
        if len(targets) < batch_limit:
            break
    return queued_count


def list_eligible_pending_cover_image_refreshes(
    options: ResidentRuntimeOptions,
    *,
    limit: int,
) -> list[TargetCoverImageRefreshState]:
    """分批列出可執行 cover refresh jobs，避免舊 ineligible pending 卡住隊首。"""

    normalized_limit = max(int(limit), 0)
    if normalized_limit <= 0:
        return []
    selected_states: list[TargetCoverImageRefreshState] = []
    inspected_target_ids: set[str] = set()
    batch_limit = max(normalized_limit * 4, normalized_limit, 1)
    while len(selected_states) < normalized_limit:
        with SqliteApplicationContext(options.db_path) as app:
            states = app.services.target_cover_image_refresh.list_pending(
                limit=batch_limit,
                exclude_target_ids=tuple(inspected_target_ids),
            )
        if not states:
            break
        inspected_target_ids.update(state.target_id for state in states)
        eligible_states = filter_maintenance_cover_refresh_states(options, states)
        for state in eligible_states:
            selected_states.append(state)
            if len(selected_states) >= normalized_limit:
                break
        if len(states) < batch_limit:
            break
    return selected_states


async def refresh_target_group_cover_image_from_context(
    *,
    options: ResidentRuntimeOptions,
    browser_context: GroupMetadataBrowserContextLike,
    state: TargetCoverImageRefreshState,
) -> bool:
    """用 resident browser context 只刷新 target group cover image URL。"""

    attempt = _begin_cover_image_refresh_attempt(options=options, state=state)
    if attempt is None:
        return False
    try:
        cover_image_url = await resolve_group_cover_image_with_context(
            browser_context,
            canonical_url=f"https://www.facebook.com/groups/{attempt.group_id}",
        )
    except GroupMetadataError as exc:
        return _handle_cover_image_resolve_error(
            options=options,
            attempt=attempt,
            exc=exc,
        )
    return _finish_resolved_cover_image_refresh_attempt(
        options=options,
        attempt=attempt,
        cover_image_url=cover_image_url,
    )


def _begin_cover_image_refresh_attempt(
    *,
    options: ResidentRuntimeOptions,
    state: TargetCoverImageRefreshState,
) -> _CoverImageRefreshAttempt | None:
    """鎖定 cover image refresh attempt 輸入，並標記本輪已嘗試。"""

    target_id = state.target_id
    reported_url = state.last_reported_url.strip()
    with SqliteApplicationContext(options.db_path) as app:
        target = app.repositories.targets.get(target_id)
        if target is None:
            return None
        current_url = target.group_cover_image_url.strip()
        if current_url != reported_url:
            app.services.target_cover_image_refresh.mark_stale_skipped(
                target_id,
                current_url=current_url,
                reported_url=reported_url,
                requested_at=state.requested_at,
            )
            return None
        group_id = target.group_id
        if not app.services.target_cover_image_refresh.mark_attempted(
            target_id,
            reported_url=reported_url,
            requested_at=state.requested_at,
        ):
            return None
    if not group_id:
        mark_target_cover_image_refresh_failed(
            options,
            target_id,
            "target group id is empty",
            reported_url=reported_url,
            requested_at=state.requested_at,
        )
        return None
    return _CoverImageRefreshAttempt(
        target_id=target_id,
        group_id=group_id,
        reported_url=reported_url,
        requested_at=state.requested_at,
    )


def _handle_cover_image_resolve_error(
    *,
    options: ResidentRuntimeOptions,
    attempt: _CoverImageRefreshAttempt,
    exc: GroupMetadataError,
) -> bool:
    """處理 cover image resolve 失敗；回傳本 refresh job 是否已完成。"""

    if is_scheduler_runtime_refresh_failure(exc):
        raise
    if clear_polluted_cover_image_url_if_current(
        options,
        target_id=attempt.target_id,
        reported_url=attempt.reported_url,
    ):
        with SqliteApplicationContext(options.db_path) as app:
            app.services.target_cover_image_refresh.mark_succeeded(
                attempt.target_id,
                resolved_url="",
                changed=True,
                result=TargetCoverImageRefreshResult.SUCCEEDED_CHANGED,
                reported_url=attempt.reported_url,
                requested_at=attempt.requested_at,
            )
        return True
    logger.info(
        "cover image refresh skipped",
        extra={"target_id": attempt.target_id},
    )
    mark_target_cover_image_refresh_failed(
        options,
        attempt.target_id,
        str(exc),
        reported_url=attempt.reported_url,
        requested_at=attempt.requested_at,
    )
    return False


def _finish_resolved_cover_image_refresh_attempt(
    *,
    options: ResidentRuntimeOptions,
    attempt: _CoverImageRefreshAttempt,
    cover_image_url: str,
) -> bool:
    """寫回已 resolve 的 cover image URL；回傳本 refresh job 是否已完成。"""

    with SqliteApplicationContext(options.db_path) as app:
        target = app.repositories.targets.get(attempt.target_id)
        if target is None:
            return False
        current_url = target.group_cover_image_url.strip()
        if current_url != attempt.reported_url:
            app.services.target_cover_image_refresh.mark_stale_skipped(
                attempt.target_id,
                current_url=current_url,
                reported_url=attempt.reported_url,
                requested_at=attempt.requested_at,
            )
            return True
        normalized_cover_image_url = cover_image_url.strip()
        changed = normalized_cover_image_url != current_url
        try:
            app.services.target_cover_image_refresh.refresh_target_cover_image_url(
                attempt.target_id,
                normalized_cover_image_url,
            )
        except InvalidTargetMetadataError as exc:
            app.services.target_cover_image_refresh.mark_failed(
                attempt.target_id,
                str(exc),
                reported_url=attempt.reported_url,
                requested_at=attempt.requested_at,
            )
            return False
        app.services.target_cover_image_refresh.mark_succeeded(
            attempt.target_id,
            resolved_url=normalized_cover_image_url,
            changed=changed,
            reported_url=attempt.reported_url,
            requested_at=attempt.requested_at,
        )
    return True


def clear_polluted_cover_image_url_if_current(
    options: ResidentRuntimeOptions,
    *,
    target_id: str,
    reported_url: str,
) -> bool:
    """若目前封面仍是通用 Facebook logo，清空 URL 作為低風險修復。"""

    with SqliteApplicationContext(options.db_path) as app:
        target = app.repositories.targets.get(target_id)
        if target is None:
            return False
        current_url = target.group_cover_image_url.strip()
        if current_url != reported_url.strip():
            return False
        if not has_polluted_group_cover_image_url(current_url):
            return False
        updated = app.services.target_cover_image_refresh.refresh_target_cover_image_url(
            target_id,
            "",
        )
    return not updated.group_cover_image_url


def mark_target_cover_image_refresh_failed(
    options: ResidentRuntimeOptions,
    target_id: str,
    error: str,
    *,
    reported_url: str | None = None,
    requested_at: datetime | None = None,
) -> None:
    """將 cover image refresh 失敗寫回獨立狀態；target 已刪除時忽略。"""

    with SqliteApplicationContext(options.db_path) as app:
        if app.repositories.targets.get(target_id) is None:
            return
        app.services.target_cover_image_refresh.mark_failed(
            target_id,
            error,
            reported_url=reported_url,
            requested_at=requested_at,
        )


__all__ = [
    "COVER_IMAGE_REFRESH_TARGET_LIMIT_PER_TICK",
    "clear_polluted_cover_image_url_if_current",
    "list_eligible_pending_cover_image_refreshes",
    "mark_target_cover_image_refresh_failed",
    "queue_polluted_cover_image_refresh_candidates",
    "refresh_pending_target_cover_images",
    "refresh_target_group_cover_image_from_context",
]
