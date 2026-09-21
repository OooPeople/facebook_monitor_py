"""Resident target cover image refresh tick orchestration。"""

from __future__ import annotations

from collections.abc import Callable

from facebook_monitor.facebook.group_metadata import (
    AsyncBrowserContextLike as GroupMetadataBrowserContextLike,
)
from facebook_monitor.core.facebook_temporary_block import FacebookActionKind
from facebook_monitor.core.facebook_temporary_block import FacebookProductOperationKind
from facebook_monitor.core.facebook_temporary_block import FacebookWorkSourceKind
from facebook_monitor.core.facebook_temporary_block import TemporaryBlockFinding
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
import facebook_monitor.worker.resident_cover_image_attempt as cover_image_attempt
import facebook_monitor.worker.resident_cover_image_queue as cover_image_queue
from facebook_monitor.worker.resident_maintenance_errors import (
    StopCheckCallable,
    format_exception_message,
    handle_maintenance_refresh_exception,
)
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.facebook_automation_runtime import FacebookAutomationRuntime
from facebook_monitor.worker.facebook_automation_runtime import (
    FacebookAutomationRuntimeTripped,
)


async def refresh_pending_target_cover_images(
    *,
    options: ResidentRuntimeOptions,
    browser_context: GroupMetadataBrowserContextLike | None,
    should_stop: StopCheckCallable | None = None,
    request_runtime_restart: Callable[[], None] | None = None,
    facebook_runtime: FacebookAutomationRuntime | None = None,
) -> int:
    """消化 dashboard 壞圖上報排入的 image-only cover refresh jobs。"""

    stop_requested = should_stop or (lambda: False)
    if browser_context is None or stop_requested():
        return 0
    runtime = facebook_runtime or FacebookAutomationRuntime()
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
            with runtime.facebook_work():
                if stop_requested():
                    break
                if await cover_image_attempt.refresh_target_group_cover_image_from_context(
                    options=options,
                    browser_context=browser_context,
                    state=state,
                ):
                    refreshed_count += 1
        except FacebookAutomationRuntimeTripped:
            break
        except Exception as exc:
            if isinstance(exc, WorkerFailure) and exc.reason == FACEBOOK_TEMPORARY_BLOCK_REASON:
                await runtime.record_temporary_block(
                    db_path=options.db_path,
                    finding=TemporaryBlockFinding(
                        source_kind=FacebookWorkSourceKind.COVER,
                        operation_kind=FacebookProductOperationKind.COVER_METADATA_ACCESS,
                        action_kind=FacebookActionKind.GROUP_DOCUMENT,
                        target_id=state.target_id,
                        evidence_code="facebook_page_guard_v1",
                    ),
                    diagnostics=exc.diagnostics,
                )
                if request_runtime_restart is not None:
                    request_runtime_restart()
                break
            failure_message = format_exception_message(exc)

            def mark_failed() -> None:
                cover_image_attempt.mark_target_cover_image_refresh_failed(
                    options,
                    state.target_id,
                    failure_message,
                    reported_url=state.last_reported_url,
                    requested_at=state.requested_at,
                )

            should_break = handle_maintenance_refresh_exception(
                options=options,
                target_id=state.target_id,
                exc=exc,
                stop_requested=stop_requested,
                request_runtime_restart=request_runtime_restart,
                shutdown_log_message=(
                    "cover image refresh skipped because scheduler is stopping"
                ),
                runtime_restart_log_message=(
                    "cover image refresh requested browser runtime restart"
                ),
                failure_log_message="cover image refresh failed",
                mark_failed=mark_failed,
            )
            if should_break:
                break
    return refreshed_count
