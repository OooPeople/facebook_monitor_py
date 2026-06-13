"""Resident metadata and cover image maintenance jobs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
import logging

from facebook_monitor.application.target_registry_service import InvalidTargetMetadataError
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import CoverImageRefreshRequestStatus
from facebook_monitor.core.models import TargetCoverImageRefreshResult
from facebook_monitor.core.models import TargetCoverImageRefreshState
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.facebook.group_metadata import (
    AsyncBrowserContextLike as GroupMetadataBrowserContextLike,
)
from facebook_monitor.facebook.group_metadata import GroupMetadataError
from facebook_monitor.facebook.group_metadata import resolve_group_cover_image_with_context
from facebook_monitor.facebook.group_metadata import resolve_group_metadata_with_context
from facebook_monitor.facebook.group_metadata_validation import has_polluted_group_cover_image_url
from facebook_monitor.worker.errors import classify_playwright_exception
from facebook_monitor.worker.errors import classify_wrapped_playwright_exception
from facebook_monitor.worker.resident_runtime_errors import _is_playwright_driver_shutdown_exception
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.scan_failure_finalize import record_guarded_scan_failure_for_db


logger = logging.getLogger(__name__)
StopCheckCallable = Callable[[], bool]
METADATA_REFRESH_TARGET_LIMIT_PER_TICK = (
    PYTHON_SCHEDULER_RUNTIME_DEFAULTS.metadata_refresh_target_limit_per_tick
)
COVER_IMAGE_REFRESH_TARGET_LIMIT_PER_TICK = (
    PYTHON_SCHEDULER_RUNTIME_DEFAULTS.cover_image_refresh_target_limit_per_tick
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
            if _should_skip_refresh_failure_for_shutdown(exc, stop_requested):
                logger.info(
                    "metadata refresh skipped because scheduler is stopping",
                    extra={"target_id": target_id},
                )
                break
            if _is_scheduler_runtime_refresh_failure(exc):
                logger.warning(
                    "metadata refresh requested browser runtime restart",
                    extra={"target_id": target_id},
                )
                recorded_failure = record_refresh_runtime_failure(
                    options=options,
                    target_id=target_id,
                    exc=exc,
                )
                if recorded_failure and request_runtime_restart is not None:
                    request_runtime_restart()
                break
            logger.exception(
                "metadata refresh failed",
                extra={"target_id": target_id},
            )
            mark_target_metadata_refresh_failed(
                options,
                target_id,
                "metadata refresh failed",
            )
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
        candidate
        for candidate in candidates
        if candidate.target_id in explicit_candidate_set
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
            if _should_skip_refresh_failure_for_shutdown(exc, stop_requested):
                logger.info(
                    "cover image refresh skipped because scheduler is stopping",
                    extra={"target_id": state.target_id},
                )
                break
            if _is_scheduler_runtime_refresh_failure(exc):
                logger.warning(
                    "cover image refresh requested browser runtime restart",
                    extra={"target_id": state.target_id},
                )
                recorded_failure = record_refresh_runtime_failure(
                    options=options,
                    target_id=state.target_id,
                    exc=exc,
                )
                if recorded_failure and request_runtime_restart is not None:
                    request_runtime_restart()
                break
            logger.exception(
                "cover image refresh failed",
                extra={"target_id": state.target_id},
            )
            mark_target_cover_image_refresh_failed(
                options,
                state.target_id,
                _format_exception_message(exc),
                reported_url=state.last_reported_url,
                requested_at=state.requested_at,
            )
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
                        PYTHON_SCHEDULER_RUNTIME_DEFAULTS
                        .cover_image_load_failure_min_interval_seconds
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


def filter_maintenance_refresh_target_ids(
    options: ResidentRuntimeOptions,
    target_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """避開已有正式掃描工作的 target，避免 maintenance job 擋住 retry。"""

    if not target_ids:
        return ()
    with SqliteApplicationContext(options.db_path) as app:
        runtime_states = app.repositories.runtime_states.list_by_targets(list(target_ids))
        targets = {target_id: app.repositories.targets.get(target_id) for target_id in target_ids}
    return tuple(
        target_id
        for target_id in target_ids
        if targets.get(target_id) is not None
        and _runtime_state_allows_maintenance_refresh(runtime_states.get(target_id))
    )


def filter_maintenance_cover_refresh_states(
    options: ResidentRuntimeOptions,
    states: list[TargetCoverImageRefreshState],
) -> list[TargetCoverImageRefreshState]:
    """避開已有正式掃描工作的 cover refresh jobs。"""

    if not states:
        return []
    target_ids = [state.target_id for state in states]
    with SqliteApplicationContext(options.db_path) as app:
        runtime_states = app.repositories.runtime_states.list_by_targets(target_ids)
        targets = {target_id: app.repositories.targets.get(target_id) for target_id in target_ids}
    return [
        state
        for state in states
        if targets.get(state.target_id) is not None
        and _runtime_state_allows_maintenance_refresh(runtime_states.get(state.target_id))
    ]


def _runtime_state_allows_maintenance_refresh(
    state: TargetRuntimeState | None,
) -> bool:
    """runtime recovery retry 等待期間，maintenance refresh 先讓位。"""

    if state is None:
        return True
    if _runtime_state_has_pending_failure_retry(state):
        return False
    return state.runtime_status not in {
        TargetRuntimeStatus.QUEUED,
        TargetRuntimeStatus.RUNNING,
        TargetRuntimeStatus.ERROR,
    }


def _runtime_state_has_pending_failure_retry(state: TargetRuntimeState) -> bool:
    """判斷 target 是否正等待 failure policy 自動重試掃描。"""

    return (
        state.runtime_status == TargetRuntimeStatus.IDLE
        and state.scan_requested_at is not None
        and state.consecutive_failure_count > 0
    )


def record_refresh_runtime_failure(
    *,
    options: ResidentRuntimeOptions,
    target_id: str,
    exc: Exception,
) -> bool:
    """將 maintenance refresh 的 browser runtime failure 接回 scan failure policy。"""

    exception_class, message = _runtime_refresh_failure_detail(exc)
    decision = record_guarded_scan_failure_for_db(
        db_path=options.db_path,
        target_id=target_id,
        reason=SCHEDULER_RUNTIME_REASON,
        message=message,
        source="unknown_exception",
        worker_path="resident_main",
        commit_guard=None,
        exception_class=exception_class,
    )
    return decision is not None


def _runtime_refresh_failure_detail(exc: Exception) -> tuple[str, str]:
    """取出最接近 Playwright runtime closed 的 exception 類型與訊息。"""

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, Exception) and (
            classify_playwright_exception(current) == SCHEDULER_RUNTIME_REASON
            or classify_wrapped_playwright_exception(current) == SCHEDULER_RUNTIME_REASON
            or _is_playwright_driver_shutdown_exception(current)
        ):
            return current.__class__.__name__, _format_exception_message(current)
        current = current.__cause__ or current.__context__
    return exc.__class__.__name__, _format_exception_message(exc)


def _should_skip_refresh_failure_for_shutdown(
    exc: Exception,
    should_stop: StopCheckCallable,
) -> bool:
    """停止流程中 Playwright driver 關閉不應污染 maintenance job 診斷。"""

    return should_stop() and _is_playwright_driver_shutdown_exception(exc)


def _is_scheduler_runtime_refresh_failure(exc: Exception) -> bool:
    """判斷 metadata/cover refresh 失敗是否代表 browser runtime 已損壞。"""

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, Exception):
            if classify_playwright_exception(current) == SCHEDULER_RUNTIME_REASON:
                return True
            if classify_wrapped_playwright_exception(current) == SCHEDULER_RUNTIME_REASON:
                return True
            if _is_playwright_driver_shutdown_exception(current):
                return True
        current = current.__cause__ or current.__context__
    return False


async def refresh_target_group_cover_image_from_context(
    *,
    options: ResidentRuntimeOptions,
    browser_context: GroupMetadataBrowserContextLike,
    state: TargetCoverImageRefreshState,
) -> bool:
    """用 resident browser context 只刷新 target group cover image URL。"""

    group_id = ""
    target_id = state.target_id
    reported_url = state.last_reported_url.strip()
    with SqliteApplicationContext(options.db_path) as app:
        target = app.repositories.targets.get(target_id)
        if target is None:
            return False
        current_url = target.group_cover_image_url.strip()
        if current_url != reported_url:
            app.services.target_cover_image_refresh.mark_stale_skipped(
                target_id,
                current_url=current_url,
                reported_url=reported_url,
                requested_at=state.requested_at,
            )
            return False
        group_id = target.group_id
        if not app.services.target_cover_image_refresh.mark_attempted(
            target_id,
            reported_url=reported_url,
            requested_at=state.requested_at,
        ):
            return False
    if not group_id:
        mark_target_cover_image_refresh_failed(
            options,
            target_id,
            "target group id is empty",
            reported_url=reported_url,
            requested_at=state.requested_at,
        )
        return False
    try:
        cover_image_url = await resolve_group_cover_image_with_context(
            browser_context,
            canonical_url=f"https://www.facebook.com/groups/{group_id}",
        )
    except GroupMetadataError as exc:
        if _is_scheduler_runtime_refresh_failure(exc):
            raise
        if clear_polluted_cover_image_url_if_current(
            options,
            target_id=target_id,
            reported_url=reported_url,
        ):
            with SqliteApplicationContext(options.db_path) as app:
                app.services.target_cover_image_refresh.mark_succeeded(
                    target_id,
                    resolved_url="",
                    changed=True,
                    result=TargetCoverImageRefreshResult.SUCCEEDED_CHANGED,
                    reported_url=reported_url,
                    requested_at=state.requested_at,
                )
            return True
        logger.info(
            "cover image refresh skipped",
            extra={"target_id": target_id},
        )
        mark_target_cover_image_refresh_failed(
            options,
            target_id,
            str(exc),
            reported_url=reported_url,
            requested_at=state.requested_at,
        )
        return False
    with SqliteApplicationContext(options.db_path) as app:
        target = app.repositories.targets.get(target_id)
        if target is None:
            return False
        current_url = target.group_cover_image_url.strip()
        if current_url != reported_url:
            app.services.target_cover_image_refresh.mark_stale_skipped(
                target_id,
                current_url=current_url,
                reported_url=reported_url,
                requested_at=state.requested_at,
            )
            return True
        normalized_cover_image_url = cover_image_url.strip()
        changed = normalized_cover_image_url != current_url
        try:
            app.services.target_cover_image_refresh.refresh_target_cover_image_url(
                target_id,
                normalized_cover_image_url,
            )
        except InvalidTargetMetadataError as exc:
            app.services.target_cover_image_refresh.mark_failed(
                target_id,
                str(exc),
                reported_url=reported_url,
                requested_at=state.requested_at,
            )
            return False
        app.services.target_cover_image_refresh.mark_succeeded(
            target_id,
            resolved_url=normalized_cover_image_url,
            changed=changed,
            reported_url=reported_url,
            requested_at=state.requested_at,
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


def _format_exception_message(exc: Exception) -> str:
    """保留非預期例外類型，讓 cover refresh 診斷可回查真正原因。"""

    message = str(exc).strip()
    if message:
        return f"{exc.__class__.__name__}: {message}"
    return exc.__class__.__name__


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
        if _is_scheduler_runtime_refresh_failure(exc):
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
