"""Target create routes。"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends
from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.target_create_service import build_create_target_plan
from facebook_monitor.application.target_create_service import CreateTargetPlan
from facebook_monitor.application.target_create_service import CreateTargetResult
from facebook_monitor.application.target_create_service import create_or_update_target_from_plan
from facebook_monitor.application.target_create_service import MetadataResolutionOutcome
from facebook_monitor.application.target_create_service import TargetCreateMetadata
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.facebook_temporary_block import FacebookActionKind
from facebook_monitor.core.facebook_temporary_block import FacebookProductOperationKind
from facebook_monitor.core.facebook_temporary_block import FacebookWorkSourceKind
from facebook_monitor.core.facebook_temporary_block import TemporaryBlockFinding
from facebook_monitor.core.refresh_policy import MIN_REFRESH_SECONDS
from facebook_monitor.core.scan_limits import MAX_TARGET_POSTS
from facebook_monitor.core.scan_limits import MIN_TARGET_POSTS
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.facebook.group_metadata import GroupMetadata
from facebook_monitor.facebook.group_metadata import GroupMetadataError
from facebook_monitor.facebook.route_detection import RouteDetectionError
from facebook_monitor.webapp.dependencies import get_group_name_resolver
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_profile_dir
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import load_app_theme
from facebook_monitor.webapp.dependencies import load_target_keyword_defaults
from facebook_monitor.webapp.dependencies import redirect_new_target_with_error
from facebook_monitor.webapp.dependencies import redirect_with_message
from facebook_monitor.webapp.dependencies import run_web_app_context_operation
from facebook_monitor.webapp.dependencies import run_with_temporary_profile_access
from facebook_monitor.webapp.notification_form_models import format_notification_form_error
from facebook_monitor.webapp.profile_session import ProfileSessionError
from facebook_monitor.webapp.target_create_form import CreateTargetConfigFormFields
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.facebook_automation_runtime import (
    FacebookAutomationTripSignal,
)
from facebook_monitor.worker.facebook_automation_runtime import (
    FacebookTemporaryBlockIncidentRecorded,
)
from facebook_monitor.worker.facebook_automation_runtime import record_temporary_block_sync


logger = logging.getLogger(__name__)


async def _resolve_group_metadata_if_needed(
    request: Request,
    *,
    plan: CreateTargetPlan,
) -> MetadataResolutionOutcome:
    """未提供自訂名稱時，視 profile 可用狀態嘗試解析 Facebook group metadata。"""

    if plan.custom_name:
        return MetadataResolutionOutcome()
    if not plan.should_resolve_metadata:
        if plan.scheduler_running:
            scheduler_state = get_scheduler_manager(request).state()
            logger.info(
                "skip group name resolver because scheduler lifecycle is %s",
                scheduler_state.lifecycle_state,
            )
            return MetadataResolutionOutcome(skipped_reason="scheduler_running")
        return MetadataResolutionOutcome()
    scheduler_state = get_scheduler_manager(request).state()
    if scheduler_state.running:
        logger.info(
            "skip group name resolver because scheduler lifecycle is %s",
            scheduler_state.lifecycle_state,
        )
        return MetadataResolutionOutcome(skipped_reason="scheduler_running")
    profile_dir = get_profile_dir(request)
    resolver = get_group_name_resolver(request)
    db_path = get_db_path(request)

    def resolve_with_incident() -> str | GroupMetadata:
        """在 temporary profile thread 內讓 detector 與 durable writer 保持同一路徑。"""

        try:
            return resolver(profile_dir, plan.metadata_canonical_url)
        except WorkerFailure as exc:
            if exc.reason != FACEBOOK_TEMPORARY_BLOCK_REASON:
                raise
            outcome = record_temporary_block_sync(
                signal=FacebookAutomationTripSignal(),
                db_path=db_path,
                finding=TemporaryBlockFinding(
                    source_kind=FacebookWorkSourceKind.SYNC_RESOLVER,
                    operation_kind=FacebookProductOperationKind.GROUP_METADATA_ACCESS,
                    action_kind=FacebookActionKind.GROUP_DOCUMENT,
                    evidence_code="facebook_page_guard_v1",
                ),
                diagnostics=exc.diagnostics,
            )
            raise FacebookTemporaryBlockIncidentRecorded(outcome) from exc

    try:
        resolved = await run_with_temporary_profile_access(
            request,
            resolve_with_incident,
        )
    except ProfileSessionError:
        logger.info("defer group metadata resolver because profile is unavailable")
        return MetadataResolutionOutcome(skipped_reason="profile_unavailable")
    except GroupMetadataError:
        logger.info("defer group metadata resolver after resolution failure")
        return MetadataResolutionOutcome(skipped_reason="resolution_failed")
    if isinstance(resolved, GroupMetadata):
        return MetadataResolutionOutcome(
            metadata=TargetCreateMetadata(
                group_name=resolved.group_name,
                group_cover_image_url=resolved.group_cover_image_url,
            )
        )
    group_name = str(resolved or "")
    if not group_name:
        return MetadataResolutionOutcome(skipped_reason="resolution_failed")
    return MetadataResolutionOutcome(metadata=TargetCreateMetadata(group_name=group_name))


def _request_metadata_refresh_if_needed(
    request: Request,
    *,
    result: CreateTargetResult,
) -> None:
    """在 DB commit 後通知 scheduler 背景補 target metadata。"""

    if result.metadata_refresh_target_id:
        get_scheduler_manager(request).request_metadata_refresh(
            result.metadata_refresh_target_id
        )


async def _create_or_update_target_from_form(
    request: Request,
    *,
    group_url: str,
    config_fields: CreateTargetConfigFormFields,
    display_name: str,
) -> CreateTargetResult:
    """從新增 target 表單完成 URL detection、upsert 與 metadata refresh 排程。"""

    keyword_defaults = await load_target_keyword_defaults(request)
    config_form = config_fields.to_target_config_form(
        default_exclude_keywords=keyword_defaults.exclude_keywords_text,
        default_exclude_ignore_phrases=keyword_defaults.exclude_ignore_phrases_text,
    )
    plan = build_create_target_plan(
        group_url=group_url,
        display_name=display_name,
        scheduler_running=get_scheduler_manager(request).state().running,
    )
    metadata_outcome = await _resolve_group_metadata_if_needed(request, plan=plan)

    def upsert(app_context: ApplicationContext) -> CreateTargetResult:
        """在 Web DB retry/thread 邊界內建立 target。"""

        return create_or_update_target_from_plan(
            app_context.services.targets,
            plan=plan,
            config=config_form.to_config_patch(preserve_secret_fields_as_unset=False),
            metadata=metadata_outcome.metadata,
            metadata_refresh_required=metadata_outcome.requires_deferred_refresh,
        )

    result = await run_web_app_context_operation(
        request,
        upsert,
        operation_name="create_or_update_target_from_form",
    )
    _request_metadata_refresh_if_needed(request, result=result)
    return result


def register_create_target_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    """註冊 target create routes。"""

    @app.get("/targets/new")
    async def new_target(request: Request) -> object:
        """顯示新增 group posts target 表單。"""

        message = request.query_params.get("message", "")
        error = request.query_params.get("error", "")
        target_keyword_defaults = await load_target_keyword_defaults(request)
        initial_theme = await load_app_theme(request)
        return templates.TemplateResponse(
            request,
            "new_target.html",
            {
                "message": message,
                "error": error,
                "target_defaults": PYTHON_TARGET_CONFIG_DEFAULTS,
                "min_refresh_seconds": MIN_REFRESH_SECONDS,
                "min_target_posts": MIN_TARGET_POSTS,
                "max_target_posts": MAX_TARGET_POSTS,
                "target_keyword_defaults": target_keyword_defaults,
                "initial_theme": initial_theme,
            },
        )

    @app.post("/targets")
    async def create_target(
        request: Request,
        group_url: Annotated[str, Form()],
        config_fields: Annotated[
            CreateTargetConfigFormFields,
            Depends(CreateTargetConfigFormFields.as_form),
        ],
        display_name: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """從表單 URL 建立或更新 posts/comments target。"""

        try:
            result = await _create_or_update_target_from_form(
                request,
                group_url=group_url,
                config_fields=config_fields,
                display_name=display_name,
            )
        except RouteDetectionError as exc:
            return redirect_new_target_with_error(str(exc))
        except ValueError as exc:
            return redirect_new_target_with_error(
                "新增失敗：" + format_notification_form_error(exc)
            )
        except Exception as exc:
            return redirect_new_target_with_error(
                "新增失敗：" + format_failure_message_text(str(exc))
            )
        if result.metadata_refresh_pending:
            return redirect_with_message(
                "target 已新增；名稱與封面將由背景服務稍後補齊",
                feedback="target_created",
            )
        return redirect_with_message("target 已新增", feedback="target_created")
