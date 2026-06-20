"""Resident target cover image refresh tick orchestration。"""

from __future__ import annotations

from collections.abc import Callable

from facebook_monitor.facebook.group_metadata import (
    AsyncBrowserContextLike as GroupMetadataBrowserContextLike,
)
import facebook_monitor.worker.resident_cover_image_attempt as cover_image_attempt
import facebook_monitor.worker.resident_cover_image_queue as cover_image_queue
from facebook_monitor.worker.resident_maintenance_errors import StopCheckCallable
from facebook_monitor.worker.resident_maintenance_errors import format_exception_message
from facebook_monitor.worker.resident_maintenance_errors import (
    handle_maintenance_refresh_exception,
)
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions


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
    cover_image_queue.queue_polluted_cover_image_refresh_candidates(options)
    refreshed_count = 0
    states = cover_image_queue.list_eligible_pending_cover_image_refreshes(
        options,
        limit=cover_image_queue.COVER_IMAGE_REFRESH_TARGET_LIMIT_PER_TICK,
    )
    for state in states:
        if stop_requested():
            break
        try:
            if await cover_image_attempt.refresh_target_group_cover_image_from_context(
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
                mark_failed=lambda: cover_image_attempt.mark_target_cover_image_refresh_failed(
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
