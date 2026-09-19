"""Resident target cover image refresh tick orchestration。"""

from __future__ import annotations

from collections.abc import Callable

from facebook_monitor.facebook.group_metadata import (
    AsyncBrowserContextLike as GroupMetadataBrowserContextLike,
)
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
import facebook_monitor.worker.resident_cover_image_attempt as cover_image_attempt
import facebook_monitor.worker.resident_cover_image_queue as cover_image_queue
from facebook_monitor.worker.resident_maintenance_errors import (
    StopCheckCallable,
    format_exception_message,
    handle_governed_maintenance_refresh_exception,
    handle_maintenance_refresh_exception,
)
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.facebook_access_runtime_incidents import (
    trip_non_scan_facebook_access_incident,
)
from facebook_monitor.core.facebook_access import FacebookWorkSourceKind
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationCoordinator,
)
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationWorkKind,
)
from facebook_monitor.worker.facebook_automation_admission import (
    FacebookAutomationAdmissionController,
)
from facebook_monitor.worker.facebook_automation_admission import (
    FacebookGovernedAutomationLease,
)
from facebook_monitor.worker.facebook_automation_coordinator import FacebookAutomationLease
from facebook_monitor.worker.facebook_visible_write import FacebookVisibleWriteRejected
from facebook_monitor.worker.facebook_visible_write import fenced_facebook_application_context


async def refresh_pending_target_cover_images(
    *,
    options: ResidentRuntimeOptions,
    browser_context: GroupMetadataBrowserContextLike | None,
    should_stop: StopCheckCallable | None = None,
    request_runtime_restart: Callable[[], None] | None = None,
    automation_coordinator: FacebookAutomationCoordinator | None = None,
    automation_admission_controller: FacebookAutomationAdmissionController | None = None,
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
        automation_lease: FacebookGovernedAutomationLease | FacebookAutomationLease | None = None
        try:
            if automation_admission_controller is not None:
                admission = await automation_admission_controller.acquire(
                    work_kind=FacebookAutomationWorkKind.COVER_REFRESH,
                    operation_kind=FacebookProductOperationKind.COVER_METADATA_ACCESS,
                    owner_alias="cover-refresh",
                )
                if not admission.admitted or admission.lease is None:
                    continue
                automation_lease = admission.lease
            elif automation_coordinator is not None:
                automation_lease = await automation_coordinator.acquire(
                    FacebookAutomationWorkKind.COVER_REFRESH,
                    owner_alias="cover-refresh",
                )
            if stop_requested():
                break
            if await cover_image_attempt.refresh_target_group_cover_image_from_context(
                options=options,
                browser_context=browser_context,
                state=state,
                automation_admission_controller=automation_admission_controller,
                facebook_admission_token=(
                    automation_lease.admission_token
                    if isinstance(automation_lease, FacebookGovernedAutomationLease)
                    else None
                ),
            ):
                refreshed_count += 1
        except FacebookVisibleWriteRejected:
            if request_runtime_restart is not None:
                request_runtime_restart()
            break
        except Exception as exc:
            governed_lease = (
                automation_lease
                if isinstance(automation_lease, FacebookGovernedAutomationLease)
                else None
            )
            if await trip_non_scan_facebook_access_incident(
                db_path=options.db_path,
                controller=automation_admission_controller,
                lease=governed_lease,
                error=exc,
                source_kind=FacebookWorkSourceKind.COVER,
                operation_kind=FacebookProductOperationKind.COVER_METADATA_ACCESS,
                target_id=state.target_id,
            ):
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

            if automation_admission_controller is not None and governed_lease is not None:
                with fenced_facebook_application_context(
                    db_path=options.db_path,
                    controller=automation_admission_controller,
                    token=governed_lease.admission_token,
                ) as app:
                    should_break = handle_governed_maintenance_refresh_exception(
                        app=app,
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
                        mark_failed=lambda guarded_app: (
                            cover_image_attempt.mark_target_cover_image_refresh_failed_in_app(
                                guarded_app,
                                state.target_id,
                                failure_message,
                                reported_url=state.last_reported_url,
                                requested_at=state.requested_at,
                            )
                        ),
                    )
            else:
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
        finally:
            if automation_lease is not None:
                await automation_lease.release()
    return refreshed_count
