"""Resident cover image refresh queue selection。"""

from __future__ import annotations

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import CoverImageRefreshRequestStatus
from facebook_monitor.core.models import TargetCoverImageRefreshState
from facebook_monitor.worker.resident_maintenance_eligibility import (
    filter_maintenance_cover_refresh_states,
)
from facebook_monitor.worker.resident_maintenance_eligibility import (
    filter_maintenance_refresh_target_ids,
)
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions


COVER_IMAGE_REFRESH_TARGET_LIMIT_PER_TICK = (
    PYTHON_SCHEDULER_RUNTIME_DEFAULTS.cover_image_refresh_target_limit_per_tick
)


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
