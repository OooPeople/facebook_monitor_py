"""Resident target metadata refresh job。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
import logging

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_metadata_policy import InvalidTargetMetadataError
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.facebook.group_metadata import (
    AsyncBrowserContextLike as GroupMetadataBrowserContextLike,
)
from facebook_monitor.facebook.group_metadata import GroupMetadataError
from facebook_monitor.facebook.group_metadata import resolve_group_metadata_with_context
from facebook_monitor.worker.resident_maintenance_errors import StopCheckCallable
from facebook_monitor.worker.resident_maintenance_eligibility import (
    filter_maintenance_refresh_target_ids,
)
from facebook_monitor.worker.resident_maintenance_errors import (
    handle_maintenance_refresh_exception,
)
from facebook_monitor.worker.resident_maintenance_errors import (
    is_scheduler_runtime_refresh_failure,
)
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions


logger = logging.getLogger(__name__)
METADATA_REFRESH_TARGET_LIMIT_PER_TICK = (
    PYTHON_SCHEDULER_RUNTIME_DEFAULTS.metadata_refresh_target_limit_per_tick
)


@dataclass(frozen=True)
class _MetadataRefreshCandidate:
    """保存單一 metadata maintenance candidate 的寫回語義。"""

    target_id: str
    overwrite_name: bool = True


async def refresh_requested_target_metadata(
    *,
    options: ResidentRuntimeOptions,
    browser_context: GroupMetadataBrowserContextLike | None,
    should_stop: StopCheckCallable | None = None,
    request_runtime_restart: Callable[[], None] | None = None,
) -> int:
    """消化 Web UI request 與 DB pending metadata refresh job。"""

    stop_requested = should_stop or (lambda: False)
    if browser_context is None or stop_requested():
        return 0
    refreshed_count = 0
    for candidate in list_metadata_refresh_candidates(options):
        if stop_requested():
            break
        target_id = candidate.target_id
        try:
            if await refresh_target_group_name_from_context(
                options=options,
                browser_context=browser_context,
                target_id=target_id,
                overwrite_name=candidate.overwrite_name,
            ):
                refreshed_count += 1
        except Exception as exc:
            should_break = handle_maintenance_refresh_exception(
                options=options,
                target_id=target_id,
                exc=exc,
                stop_requested=stop_requested,
                request_runtime_restart=request_runtime_restart,
                shutdown_log_message=("metadata refresh skipped because scheduler is stopping"),
                runtime_restart_log_message=("metadata refresh requested browser runtime restart"),
                failure_log_message="metadata refresh failed",
                mark_failed=lambda: mark_target_metadata_refresh_failed(
                    options,
                    target_id,
                    "metadata refresh failed",
                ),
            )
            if should_break:
                break
    return refreshed_count


def list_metadata_refresh_candidates(
    options: ResidentRuntimeOptions,
) -> tuple[_MetadataRefreshCandidate, ...]:
    """列出本輪 metadata maintenance candidates，明確 request 優先於自動修復。"""

    candidates: list[_MetadataRefreshCandidate] = []
    seen: set[str] = set()

    def add_candidate(target_id: str, *, overwrite_name: bool) -> None:
        normalized_target_id = str(target_id or "").strip()
        if not normalized_target_id or normalized_target_id in seen:
            return
        seen.add(normalized_target_id)
        candidates.append(
            _MetadataRefreshCandidate(
                target_id=normalized_target_id,
                overwrite_name=overwrite_name,
            )
        )

    if options.metadata_refresh_provider is not None:
        for target_id in options.metadata_refresh_provider():
            add_candidate(target_id, overwrite_name=True)
    explicit_candidates = filter_maintenance_refresh_target_ids(
        options,
        tuple(candidate.target_id for candidate in candidates),
    )
    explicit_candidate_set = set(explicit_candidates)
    candidates = [
        candidate for candidate in candidates if candidate.target_id in explicit_candidate_set
    ]
    seen = {candidate.target_id for candidate in candidates}
    remaining_limit = METADATA_REFRESH_TARGET_LIMIT_PER_TICK - len(candidates)
    if remaining_limit > 0:
        _append_pending_metadata_refresh_candidates(
            options=options,
            candidates=candidates,
            seen=seen,
            remaining_limit=remaining_limit,
        )
    remaining_limit = METADATA_REFRESH_TARGET_LIMIT_PER_TICK - len(candidates)
    if remaining_limit > 0:
        retry_after_seconds = (
            PYTHON_SCHEDULER_RUNTIME_DEFAULTS.cover_image_load_failure_min_interval_seconds
        )
        retry_before = utc_now() - timedelta(seconds=retry_after_seconds)
        _append_polluted_metadata_refresh_candidates(
            options=options,
            candidates=candidates,
            seen=seen,
            remaining_limit=remaining_limit,
            retry_before=retry_before,
        )
    return tuple(candidates)


def _append_pending_metadata_refresh_candidates(
    *,
    options: ResidentRuntimeOptions,
    candidates: list[_MetadataRefreshCandidate],
    seen: set[str],
    remaining_limit: int,
) -> None:
    """分批尋找可執行的 pending metadata refresh，避免舊 ineligible row 卡隊首。"""

    inspected_ids: set[str] = set()
    batch_limit = max(remaining_limit * 4, remaining_limit, 1)
    while remaining_limit > 0:
        with SqliteApplicationContext(options.db_path) as app:
            pending_targets = app.repositories.targets.list_by_metadata_status(
                TargetMetadataStatus.PENDING,
                limit=batch_limit,
                exclude_ids=tuple(seen | inspected_ids),
            )
        if not pending_targets:
            break
        inspected_ids.update(target.id for target in pending_targets)
        pending_ids = filter_maintenance_refresh_target_ids(
            options,
            tuple(target.id for target in pending_targets),
        )
        for target_id in pending_ids:
            if target_id in seen:
                continue
            seen.add(target_id)
            candidates.append(
                _MetadataRefreshCandidate(
                    target_id=target_id,
                    overwrite_name=True,
                )
            )
            remaining_limit -= 1
            if remaining_limit <= 0:
                break
        if len(pending_targets) < batch_limit:
            break


def _append_polluted_metadata_refresh_candidates(
    *,
    options: ResidentRuntimeOptions,
    candidates: list[_MetadataRefreshCandidate],
    seen: set[str],
    remaining_limit: int,
    retry_before: datetime,
) -> None:
    """分批尋找可執行的污染名稱修復候選，避免舊 ineligible row 餓死後續 target。"""

    inspected_ids: set[str] = set()
    batch_limit = max(remaining_limit * 4, remaining_limit, 1)
    while remaining_limit > 0:
        with SqliteApplicationContext(options.db_path) as app:
            polluted_targets = app.repositories.targets.list_polluted_group_name_candidates(
                limit=batch_limit,
                retry_failed_before=retry_before,
                exclude_ids=tuple(seen | inspected_ids),
            )
        if not polluted_targets:
            break
        inspected_ids.update(target.id for target in polluted_targets)
        polluted_ids = filter_maintenance_refresh_target_ids(
            options,
            tuple(target.id for target in polluted_targets),
        )
        for target_id in polluted_ids:
            if target_id in seen:
                continue
            seen.add(target_id)
            candidates.append(
                _MetadataRefreshCandidate(
                    target_id=target_id,
                    overwrite_name=False,
                )
            )
            remaining_limit -= 1
            if remaining_limit <= 0:
                break
        if len(polluted_targets) < batch_limit:
            break


def mark_target_metadata_refresh_failed(
    options: ResidentRuntimeOptions,
    target_id: str,
    error: str,
) -> None:
    """將 metadata refresh 失敗寫回 DB；target 已被刪除時忽略。"""

    with SqliteApplicationContext(options.db_path) as app:
        if app.repositories.targets.get(target_id) is None:
            return
        app.services.targets.mark_target_metadata_refresh_failed(target_id, error)


async def refresh_target_group_name_from_context(
    *,
    options: ResidentRuntimeOptions,
    browser_context: GroupMetadataBrowserContextLike,
    target_id: str,
    overwrite_name: bool = True,
) -> bool:
    """用 resident browser context 補齊 target group name。"""

    group_id = ""
    with SqliteApplicationContext(options.db_path) as app:
        target = app.repositories.targets.get(target_id)
        if target is None:
            return False
        group_id = target.group_id
    if not group_id:
        return False
    try:
        metadata = await resolve_group_metadata_with_context(
            browser_context,
            canonical_url=f"https://www.facebook.com/groups/{group_id}",
        )
    except GroupMetadataError as exc:
        if is_scheduler_runtime_refresh_failure(exc):
            raise
        logger.info(
            "metadata refresh skipped",
            extra={"target_id": target_id},
        )
        mark_target_metadata_refresh_failed(options, target_id, str(exc))
        return False
    with SqliteApplicationContext(options.db_path) as app:
        if app.repositories.targets.get(target_id) is None:
            return False
        try:
            app.services.targets.refresh_target_group_metadata(
                target_id,
                group_name=metadata.group_name,
                group_cover_image_url=metadata.group_cover_image_url,
                overwrite_name=overwrite_name,
            )
        except InvalidTargetMetadataError as exc:
            app.services.targets.mark_target_metadata_refresh_failed(target_id, str(exc))
            return False
    return True


__all__ = [
    "METADATA_REFRESH_TARGET_LIMIT_PER_TICK",
    "list_metadata_refresh_candidates",
    "mark_target_metadata_refresh_failed",
    "refresh_requested_target_metadata",
    "refresh_target_group_name_from_context",
]
