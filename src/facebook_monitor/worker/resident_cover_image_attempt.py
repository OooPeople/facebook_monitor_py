"""Resident cover image refresh single-attempt lifecycle。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_metadata_policy import InvalidTargetMetadataError
from facebook_monitor.core.models import TargetCoverImageRefreshResult
from facebook_monitor.core.models import TargetCoverImageRefreshState
from facebook_monitor.facebook.group_metadata import (
    AsyncBrowserContextLike as GroupMetadataBrowserContextLike,
)
from facebook_monitor.facebook.group_metadata import GroupMetadataError
from facebook_monitor.facebook.group_metadata import resolve_group_cover_image_with_context
from facebook_monitor.facebook.group_metadata_validation import has_polluted_group_cover_image_url
from facebook_monitor.worker.resident_maintenance_errors import is_scheduler_runtime_refresh_failure
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CoverImageRefreshAttempt:
    """保存單一 cover image refresh attempt 的已鎖定輸入。"""

    target_id: str
    group_id: str
    reported_url: str
    requested_at: datetime | None


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
