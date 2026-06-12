"""Target create routes。"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Annotated

from fastapi import Depends
from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.target_route_service import DetectedCommentsTargetRoute
from facebook_monitor.application.target_route_service import DetectedPostsTargetRoute
from facebook_monitor.application.target_route_service import detect_target_route_from_url
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.input_limits import normalize_display_name
from facebook_monitor.core.input_limits import normalize_target_url
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.refresh_policy import MIN_REFRESH_SECONDS
from facebook_monitor.core.scan_limits import MAX_TARGET_POSTS
from facebook_monitor.core.scan_limits import MIN_TARGET_POSTS
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.facebook.group_metadata import GroupMetadata
from facebook_monitor.facebook.group_metadata import GroupMetadataError
from facebook_monitor.facebook.route_detection import clean_facebook_page_title
from facebook_monitor.facebook.route_detection import RouteDetectionError
from facebook_monitor.webapp.dependencies import get_group_name_resolver
from facebook_monitor.webapp.dependencies import get_profile_dir
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import load_app_theme
from facebook_monitor.webapp.dependencies import load_target_keyword_defaults
from facebook_monitor.webapp.dependencies import redirect_new_target_with_error
from facebook_monitor.webapp.dependencies import redirect_with_message
from facebook_monitor.webapp.dependencies import run_web_app_context_operation
from facebook_monitor.webapp.dependencies import run_with_temporary_profile_access
from facebook_monitor.webapp.notification_form_models import format_notification_form_error
from facebook_monitor.webapp.target_config_form import TargetConfigForm
from facebook_monitor.webapp.target_create_form import CreateTargetConfigFormFields
from facebook_monitor.webapp.profile_session import ProfileSessionError


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CreateTargetRouteContext:
    """保存新增 target route 已解析的表單與 scheduler 狀態。"""

    route: DetectedCommentsTargetRoute | DetectedPostsTargetRoute
    config_form: TargetConfigForm
    custom_name: str
    scheduler_running: bool


async def _resolve_group_metadata_if_needed(
    request: Request,
    *,
    custom_name: str,
    canonical_url: str,
) -> GroupMetadata:
    """未提供自訂名稱時，視 profile 可用狀態嘗試解析 Facebook group metadata。"""

    if custom_name:
        return GroupMetadata()
    scheduler_state = get_scheduler_manager(request).state()
    if scheduler_state.running:
        logger.info(
            "skip group name resolver because scheduler lifecycle is %s",
            scheduler_state.lifecycle_state,
        )
        return GroupMetadata()
    profile_dir = get_profile_dir(request)
    resolver = get_group_name_resolver(request)
    resolved = await run_with_temporary_profile_access(
        request,
        lambda: resolver(profile_dir, canonical_url),
    )
    if isinstance(resolved, GroupMetadata):
        return resolved
    return GroupMetadata(group_name=str(resolved or ""))


async def _build_create_target_context(
    request: Request,
    *,
    group_url: str,
    config_fields: CreateTargetConfigFormFields,
    display_name: str,
) -> _CreateTargetRouteContext:
    """解析新增 target 表單，保留 route handler 外的純表單轉換。"""

    keyword_defaults = await load_target_keyword_defaults(request)
    config_form = config_fields.to_target_config_form(
        default_exclude_keywords=keyword_defaults.exclude_keywords_text,
        default_exclude_ignore_phrases=keyword_defaults.exclude_ignore_phrases_text,
    )
    return _CreateTargetRouteContext(
        route=detect_target_route_from_url(normalize_target_url(group_url)),
        config_form=config_form,
        custom_name=clean_facebook_page_title(normalize_display_name(display_name)),
        scheduler_running=get_scheduler_manager(request).state().running,
    )


async def _upsert_target_from_route_context(
    request: Request,
    context: _CreateTargetRouteContext,
) -> TargetDescriptor:
    """依 URL detection 結果建立 posts 或 comments target。"""

    if isinstance(context.route, DetectedCommentsTargetRoute):
        return await _upsert_comments_target_from_route_context(request, context)
    return await _upsert_group_posts_target_from_route_context(request, context)


async def _upsert_comments_target_from_route_context(
    request: Request,
    context: _CreateTargetRouteContext,
) -> TargetDescriptor:
    """建立 comments target 並保留 metadata refresh pending 語義。"""

    route = context.route
    if not isinstance(route, DetectedCommentsTargetRoute):
        raise TypeError("comments route context expected")
    resolved_metadata = await _resolve_group_metadata_if_needed(
        request,
        custom_name=context.custom_name,
        canonical_url=route.group_canonical_url,
    )

    def upsert(app_context: ApplicationContext) -> TargetDescriptor:
        """在 Web DB retry/thread 邊界內建立 comments target。"""

        target = app_context.services.targets.upsert_comments_target(
            context.config_form.to_comments_upsert_request(
                group_id=route.group_id,
                parent_post_id=route.parent_post_id,
                canonical_url=route.canonical_url,
                name=context.custom_name,
                group_name=resolved_metadata.group_name,
                group_cover_image_url=resolved_metadata.group_cover_image_url,
            )
        )
        return _mark_metadata_refresh_pending_if_needed(
            app_context,
            target=target,
            context=context,
        )

    return await run_web_app_context_operation(
        request,
        upsert,
        operation_name="upsert_comments_target",
    )


async def _upsert_group_posts_target_from_route_context(
    request: Request,
    context: _CreateTargetRouteContext,
) -> TargetDescriptor:
    """建立 group posts target 並保留 metadata refresh pending 語義。"""

    route = context.route
    if not isinstance(route, DetectedPostsTargetRoute):
        raise TypeError("posts route context expected")
    resolved_metadata = await _resolve_group_metadata_if_needed(
        request,
        custom_name=context.custom_name,
        canonical_url=route.canonical_url,
    )

    def upsert(app_context: ApplicationContext) -> TargetDescriptor:
        """在 Web DB retry/thread 邊界內建立 group posts target。"""

        target = app_context.services.targets.upsert_group_posts_target(
            context.config_form.to_group_posts_upsert_request(
                group_id=route.group_id,
                canonical_url=route.canonical_url,
                name=context.custom_name,
                group_name=resolved_metadata.group_name,
                group_cover_image_url=resolved_metadata.group_cover_image_url,
            )
        )
        return _mark_metadata_refresh_pending_if_needed(
            app_context,
            target=target,
            context=context,
        )

    return await run_web_app_context_operation(
        request,
        upsert,
        operation_name="upsert_group_posts_target",
    )


def _mark_metadata_refresh_pending_if_needed(
    app_context: ApplicationContext,
    *,
    target: TargetDescriptor,
    context: _CreateTargetRouteContext,
) -> TargetDescriptor:
    """scheduler 執行中且未自訂名稱時，標記背景 metadata refresh。"""

    if not context.scheduler_running or context.custom_name:
        return target
    return app_context.services.targets.mark_target_metadata_refresh_pending(target.id)


def _request_metadata_refresh_if_needed(
    request: Request,
    *,
    target: TargetDescriptor,
    context: _CreateTargetRouteContext,
) -> None:
    """在 DB commit 後通知 scheduler 背景補 target metadata。"""

    if context.scheduler_running and not context.custom_name:
        get_scheduler_manager(request).request_metadata_refresh(target.id)


async def _create_or_update_target_from_form(
    request: Request,
    *,
    group_url: str,
    config_fields: CreateTargetConfigFormFields,
    display_name: str,
) -> TargetDescriptor:
    """從新增 target 表單完成 URL detection、upsert 與 metadata refresh 排程。"""

    context = await _build_create_target_context(
        request,
        group_url=group_url,
        config_fields=config_fields,
        display_name=display_name,
    )
    target = await _upsert_target_from_route_context(request, context)
    _request_metadata_refresh_if_needed(request, target=target, context=context)
    return target


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
            await _create_or_update_target_from_form(
                request,
                group_url=group_url,
                config_fields=config_fields,
                display_name=display_name,
            )
        except RouteDetectionError as exc:
            return redirect_new_target_with_error(str(exc))
        except GroupMetadataError as exc:
            return redirect_new_target_with_error(str(exc))
        except ProfileSessionError as exc:
            return redirect_new_target_with_error(str(exc))
        except ValueError as exc:
            return redirect_new_target_with_error(
                "新增失敗：" + format_notification_form_error(exc)
            )
        except Exception as exc:
            return redirect_new_target_with_error(
                "新增失敗：" + format_failure_message_text(str(exc))
            )
        return redirect_with_message("target 已新增", feedback="target_created")
