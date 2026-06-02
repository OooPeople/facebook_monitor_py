"""Target management routes。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Depends
from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.target_actions import delete_target_action
from facebook_monitor.application.target_actions import pause_target_monitoring_action
from facebook_monitor.application.target_actions import request_target_scan_once_action
from facebook_monitor.application.target_actions import restart_target_monitoring_action
from facebook_monitor.application.target_actions import reset_target_notification_state_action
from facebook_monitor.application.target_actions import TargetActionOutcome
from facebook_monitor.application.target_route_service import DetectedCommentsTargetRoute
from facebook_monitor.application.target_route_service import DetectedPostsTargetRoute
from facebook_monitor.application.target_route_service import detect_target_route_from_url
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.input_limits import normalize_display_name
from facebook_monitor.core.input_limits import normalize_target_url
from facebook_monitor.core.models import CoverImageRefreshRequestStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.refresh_policy import MIN_REFRESH_SECONDS
from facebook_monitor.core.scan_limits import MIN_TARGET_POSTS
from facebook_monitor.core.scan_limits import MAX_TARGET_POSTS
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.core.user_messages import format_notification_event_message
from facebook_monitor.facebook.group_metadata import GroupMetadataError
from facebook_monitor.facebook.group_metadata import GroupMetadata
from facebook_monitor.facebook.route_detection import RouteDetectionError
from facebook_monitor.facebook.route_detection import clean_facebook_page_title
from facebook_monitor.notifications.manual_test import send_manual_test_notification
from facebook_monitor.notifications.safe_messages import safe_exception_message
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_desktop_sender
from facebook_monitor.webapp.dependencies import get_discord_sender
from facebook_monitor.webapp.dependencies import get_ntfy_sender
from facebook_monitor.webapp.dependencies import get_group_name_resolver
from facebook_monitor.webapp.dependencies import get_profile_dir
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import load_app_theme
from facebook_monitor.webapp.dependencies import load_target_keyword_defaults
from facebook_monitor.webapp.dependencies import redirect_new_target_with_error
from facebook_monitor.webapp.dependencies import redirect_with_error
from facebook_monitor.webapp.dependencies import redirect_with_message
from facebook_monitor.webapp.request_payloads import json_object_payload
from facebook_monitor.webapp.dependencies import run_with_temporary_profile_access
from facebook_monitor.webapp.dependencies import run_web_app_context_operation
from facebook_monitor.webapp.dependencies import run_web_db_operation
from facebook_monitor.webapp.dependencies import start_resident_scheduler_if_needed
from facebook_monitor.webapp.form_models import CreateTargetConfigFormFields
from facebook_monitor.webapp.form_models import format_notification_form_error
from facebook_monitor.webapp.form_models import NotificationConfigForm
from facebook_monitor.webapp.form_models import TargetConfigForm
from facebook_monitor.webapp.profile_session import ProfileSessionError


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CreateTargetRouteContext:
    """保存新增 target route 已解析的表單與 scheduler 狀態。"""

    route: DetectedCommentsTargetRoute | DetectedPostsTargetRoute
    config_form: TargetConfigForm
    custom_name: str
    scheduler_running: bool


class _TargetNotificationTestNotFound(Exception):
    """表示測試通知 route 找不到指定 target。"""


def _wants_json_response(request: Request) -> bool:
    """判斷前端是否要求保留目前頁面並以 JSON 接收操作結果。"""

    return "application/json" in request.headers.get("accept", "").lower()


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


async def _send_target_test_notifications(
    request: Request,
    *,
    target_id: str,
    notification_form: NotificationConfigForm,
) -> list[str]:
    """依表單欄位送出 target 測試通知，並回傳已在 UI 顯示前本地化的結果。"""

    def load_config(
        app_context: ApplicationContext,
    ) -> tuple[TargetDescriptor, TargetConfig]:
        """讀取測試通知需要的 target 與既有通知 secret。"""

        target = app_context.repositories.targets.get(target_id)
        if target is None:
            raise _TargetNotificationTestNotFound
        existing_config = app_context.services.targets.get_config_for_target(target)
        return target, existing_config

    target, existing_config = await run_web_app_context_operation(
        request,
        load_config,
        operation_name="load_target_notification_test_config",
    )
    config = notification_form.to_target_config(
        target_id=target.id,
        existing_ntfy_topic=existing_config.ntfy_topic,
        existing_discord_webhook=existing_config.discord_webhook,
    )
    results = await run_in_threadpool(
        send_manual_test_notification,
        config=config,
        ntfy_sender=get_ntfy_sender(request),
        desktop_sender=get_desktop_sender(request),
        discord_sender=get_discord_sender(request),
    )
    return [format_notification_event_message(result) for result in results]


def _target_notification_test_error_response(
    request: Request,
    *,
    error_message: str,
    return_to: str,
    status_code: int,
) -> JSONResponse | RedirectResponse:
    """依 Accept header 回傳 target 測試通知的 JSON 或 redirect 錯誤。"""

    if _wants_json_response(request):
        return JSONResponse(
            {"ok": False, "error": error_message},
            status_code=status_code,
        )
    return redirect_with_error(error_message, return_to=return_to)


def _target_notification_test_success_response(
    request: Request,
    *,
    localized_results: list[str],
    return_to: str,
) -> JSONResponse | RedirectResponse:
    """依 Accept header 回傳 target 測試通知成功結果。"""

    message = "測試通知結果：" + " / ".join(localized_results)
    if _wants_json_response(request):
        return JSONResponse({"ok": True, "message": message, "results": localized_results})
    return redirect_with_message(message, return_to=return_to)


async def _run_target_action_redirect(
    request: Request,
    *,
    target_id: str,
    return_to: str,
    action: Callable[[Path, str], TargetActionOutcome],
    failure_prefix: str,
    log_exception_message: str = "",
) -> RedirectResponse:
    """執行 target action 並集中 redirect / scheduler side effect 語義。"""

    try:
        db_path = get_db_path(request)
        outcome = await run_web_db_operation(
            lambda: action(db_path, target_id),
            operation_name=f"target_action.{action.__name__}",
        )
        if not outcome.ok:
            return redirect_with_error(outcome.message, return_to=return_to)
        if outcome.wake_scheduler:
            get_scheduler_manager(request).wake()
        if outcome.start_scheduler:
            start_resident_scheduler_if_needed(request)
    except Exception as exc:
        if log_exception_message:
            logger.exception(log_exception_message, extra={"target_id": target_id})
        return redirect_with_error(
            failure_prefix + format_failure_message_text(str(exc)),
            return_to=return_to,
        )
    return redirect_with_message(
        outcome.message,
        return_to=return_to,
        feedback=outcome.feedback,
    )


def register_target_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    """註冊 target create/update/action routes。"""

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

    @app.post("/targets/{target_id}/config")
    async def update_config(
        request: Request,
        target_id: str,
        config_form: Annotated[
            TargetConfigForm,
            Depends(TargetConfigForm.as_form),
        ],
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """更新單一 target 設定。"""

        try:
            def update(app_context: ApplicationContext) -> None:
                """在 Web DB retry/thread 邊界內更新 target config。"""

                app_context.services.targets.update_target_config(
                    config_form.to_update_request(target_id=target_id)
                )

            await run_web_app_context_operation(
                request,
                update,
                operation_name="update_target_config",
            )
        except ValueError as exc:
            return redirect_with_error(
                "設定更新失敗：" + format_notification_form_error(exc),
                return_to=return_to,
            )
        except Exception as exc:
            return redirect_with_error(
                "設定更新失敗：" + format_failure_message_text(str(exc)),
                return_to=return_to,
            )
        return redirect_with_message(
            "設定已更新",
            return_to=return_to,
            feedback="target_config_saved",
        )

    @app.post("/targets/{target_id}/name")
    async def update_target_name(
        request: Request,
        target_id: str,
        display_name: Annotated[str, Form()] = "",
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """更新 target card 顯示名稱。"""

        try:
            def update(app_context: ApplicationContext) -> None:
                """在 Web DB retry/thread 邊界內更新 target 顯示名稱。"""

                app_context.services.targets.update_target_name(
                    target_id,
                    normalize_display_name(display_name),
                )

            await run_web_app_context_operation(
                request,
                update,
                operation_name="update_target_name",
            )
        except Exception as exc:
            return redirect_with_error(
                "名稱更新失敗：" + format_failure_message_text(str(exc)),
                return_to=return_to,
            )
        return redirect_with_message(
            "名稱已更新",
            return_to=return_to,
            feedback="target_name_saved",
        )

    @app.post("/targets/{target_id}/metadata/refresh")
    async def refresh_target_metadata_route(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """手動要求 resident worker 重新抓取 target 名稱與封面。"""

        try:
            def mark_pending(app_context: ApplicationContext) -> TargetDescriptor | None:
                """標記 metadata refresh pending 並回傳 target 是否存在。"""

                target = app_context.repositories.targets.get(target_id)
                if target is None:
                    return None
                app_context.services.targets.mark_target_metadata_refresh_pending(target_id)
                return target

            target = await run_web_app_context_operation(
                request,
                mark_pending,
                operation_name="request_target_metadata_refresh",
            )
            if target is None:
                return redirect_with_error("重新抓取失敗: target 不存在", return_to=return_to)
            get_scheduler_manager(request).request_metadata_refresh(target_id)
            start_resident_scheduler_if_needed(request)
        except Exception as exc:
            return redirect_with_error(
                "重新抓取失敗：" + format_failure_message_text(str(exc)),
                return_to=return_to,
            )
        return redirect_with_message(
            "已加入排程，會在下一次啟動時抓取名稱與封面",
            return_to=return_to,
        )

    @app.post("/api/targets/{target_id}/cover-image/load-failure")
    async def report_target_cover_image_load_failure(
        request: Request,
        target_id: str,
    ) -> JSONResponse:
        """接收 UI 壞圖 hint，排程 image-only cover URL 背景刷新。"""

        payload = await json_object_payload(request)
        reported_url = str(payload.get("url", "")).strip()
        min_interval_seconds = (
            PYTHON_SCHEDULER_RUNTIME_DEFAULTS.cover_image_load_failure_min_interval_seconds
        )
        result = await run_web_app_context_operation(
            request,
            lambda app_context: app_context.services.targets.request_target_cover_image_refresh(
                target_id,
                reported_url=reported_url,
                min_interval_seconds=min_interval_seconds,
            ),
            operation_name="request_target_cover_image_refresh",
        )
        if result.status in {
            CoverImageRefreshRequestStatus.QUEUED,
            CoverImageRefreshRequestStatus.PENDING,
        }:
            start_resident_scheduler_if_needed(request)
        return JSONResponse({
            "ok": result.status
            not in {
                CoverImageRefreshRequestStatus.NOT_FOUND,
                CoverImageRefreshRequestStatus.INVALID_URL,
            },
            "status": result.status,
            "queued": result.queued,
            "reason": result.reason,
        })

    @app.post("/targets/{target_id}/notifications/test")
    async def test_target_notifications(
        request: Request,
        target_id: str,
        notification_form: Annotated[
            NotificationConfigForm,
            Depends(NotificationConfigForm.as_form),
        ],
        return_to: Annotated[str, Form()] = "",
    ) -> object:
        """依 target 設定 modal 目前欄位送出一則測試通知，不保存設定。"""

        try:
            localized_results = await _send_target_test_notifications(
                request,
                target_id=target_id,
                notification_form=notification_form,
            )
        except _TargetNotificationTestNotFound:
            return _target_notification_test_error_response(
                request,
                error_message="測試通知失敗: target 不存在",
                return_to=return_to,
                status_code=404,
            )
        except ValueError as exc:
            error_message = "測試通知失敗: " + format_notification_form_error(exc)
            return _target_notification_test_error_response(
                request,
                error_message=error_message,
                return_to=return_to,
                status_code=400,
            )
        except Exception as exc:
            error_message = (
                "測試通知失敗: "
                + format_notification_event_message(
                    safe_exception_message("notification_test_failed", exc)
                )
            )
            return _target_notification_test_error_response(
                request,
                error_message=error_message,
                return_to=return_to,
                status_code=400,
            )
        return _target_notification_test_success_response(
            request,
            localized_results=localized_results,
            return_to=return_to,
        )

    @app.post("/targets/{target_id}/start")
    async def restart_target_monitoring_route(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """開始單一 target，保留 seen/outbox 並要求立即掃描。"""

        return await _run_target_action_redirect(
            request,
            target_id=target_id,
            return_to=return_to,
            action=restart_target_monitoring_action,
            failure_prefix="啟動失敗：",
        )

    @app.post("/targets/{target_id}/notifications/clear")
    async def reset_target_notification_state_route(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """重置單一 target 的通知與 seen 去重狀態。"""

        return await _run_target_action_redirect(
            request,
            target_id=target_id,
            return_to=return_to,
            action=reset_target_notification_state_action,
            failure_prefix="重置通知狀態失敗：",
        )

    @app.post("/targets/{target_id}/stop")
    async def pause_target_monitoring_route(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """暫停單一 target，保留 seen scope 與歷史紀錄。"""

        return await _run_target_action_redirect(
            request,
            target_id=target_id,
            return_to=return_to,
            action=pause_target_monitoring_action,
            failure_prefix="停止失敗：",
        )

    @app.post("/targets/{target_id}/delete")
    async def delete_target(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """刪除單一 target。"""

        return await _run_target_action_redirect(
            request,
            target_id=target_id,
            return_to=return_to,
            action=delete_target_action,
            failure_prefix="刪除失敗：",
        )

    @app.post("/targets/{target_id}/scan-once")
    async def scan_once(request: Request, target_id: str) -> RedirectResponse:
        """要求 resident scheduler 對單一 target 執行一次掃描。"""

        return await _run_target_action_redirect(
            request,
            target_id=target_id,
            return_to="",
            action=request_target_scan_once_action,
            failure_prefix="掃描失敗：",
            log_exception_message="scan once failed",
        )
