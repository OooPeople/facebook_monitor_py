"""Target management routes。"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import DEFAULT_WEBUI_FIXED_REFRESH_SECONDS
from facebook_monitor.application.target_route_service import DetectedCommentsTargetRoute
from facebook_monitor.application.target_route_service import detect_target_route_from_url
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import CoverImageRefreshRequestStatus
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
from facebook_monitor.webapp.dependencies import get_app_theme
from facebook_monitor.webapp.dependencies import get_desktop_sender
from facebook_monitor.webapp.dependencies import get_discord_sender
from facebook_monitor.webapp.dependencies import get_global_notification_settings
from facebook_monitor.webapp.dependencies import get_ntfy_sender
from facebook_monitor.webapp.dependencies import get_group_name_resolver
from facebook_monitor.webapp.dependencies import get_profile_dir
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import get_target_keyword_defaults
from facebook_monitor.webapp.dependencies import redirect_new_target_with_error
from facebook_monitor.webapp.dependencies import redirect_with_error
from facebook_monitor.webapp.dependencies import redirect_with_message
from facebook_monitor.webapp.dependencies import run_with_temporary_profile_access
from facebook_monitor.webapp.dependencies import start_resident_scheduler_if_needed
from facebook_monitor.webapp.form_models import NotificationConfigForm
from facebook_monitor.webapp.form_models import TargetConfigForm
from facebook_monitor.webapp.profile_session import ProfileSessionError


logger = logging.getLogger(__name__)


def _wants_json_response(request: Request) -> bool:
    """判斷前端是否要求保留目前頁面並以 JSON 接收操作結果。"""

    return "application/json" in request.headers.get("accept", "").lower()


def _build_target_config_form(
    *,
    include_keywords: str,
    exclude_keywords: str,
    exclude_ignore_phrases: str,
    refresh_mode: str,
    fixed_refresh_sec: int,
    min_refresh_sec: int,
    max_refresh_sec: int,
    max_items_per_scan: int,
    auto_load_more: str | None,
    auto_adjust_sort: str | None,
    enable_desktop_notification: str | None,
    enable_ntfy: str | None,
    ntfy_topic: str,
    enable_discord_notification: str | None,
    discord_webhook: str,
) -> TargetConfigForm:
    """集中建立 target config form，避免 create/update route 重複欄位映射。"""

    return TargetConfigForm(
        include_keywords=include_keywords,
        exclude_keywords=exclude_keywords,
        exclude_ignore_phrases=exclude_ignore_phrases,
        refresh_mode=refresh_mode,
        fixed_refresh_sec=fixed_refresh_sec,
        min_refresh_sec=min_refresh_sec,
        max_refresh_sec=max_refresh_sec,
        max_items_per_scan=max_items_per_scan,
        auto_load_more=auto_load_more,
        auto_adjust_sort=auto_adjust_sort,
        enable_desktop_notification=enable_desktop_notification,
        enable_ntfy=enable_ntfy,
        ntfy_topic=ntfy_topic,
        enable_discord_notification=enable_discord_notification,
        discord_webhook=discord_webhook,
    )


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
    resolved = await run_with_temporary_profile_access(
        request,
        lambda: get_group_name_resolver(request)(
            get_profile_dir(request),
            canonical_url,
        ),
    )
    if isinstance(resolved, GroupMetadata):
        return resolved
    return GroupMetadata(group_name=str(resolved or ""))


def register_target_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    """註冊 target create/update/action routes。"""

    @app.get("/targets/new")
    async def new_target(request: Request) -> object:
        """顯示新增 group posts target 表單。"""

        message = request.query_params.get("message", "")
        error = request.query_params.get("error", "")
        return templates.TemplateResponse(
            request,
            "new_target.html",
            {
                "message": message,
                "error": error,
                "notification_settings": get_global_notification_settings(request),
                "target_defaults": PYTHON_TARGET_CONFIG_DEFAULTS,
                "min_refresh_seconds": MIN_REFRESH_SECONDS,
                "min_target_posts": MIN_TARGET_POSTS,
                "max_target_posts": MAX_TARGET_POSTS,
                "target_keyword_defaults": get_target_keyword_defaults(request),
                "initial_theme": get_app_theme(request),
            },
        )

    @app.post("/targets")
    async def create_target(
        request: Request,
        group_url: Annotated[str, Form()],
        display_name: Annotated[str, Form()] = "",
        include_keywords: Annotated[str, Form()] = "",
        exclude_keywords: Annotated[str | None, Form()] = None,
        exclude_ignore_phrases: Annotated[str | None, Form()] = None,
        refresh_mode: Annotated[str, Form()] = "floating",
        fixed_refresh_sec: Annotated[int, Form()] = DEFAULT_WEBUI_FIXED_REFRESH_SECONDS,
        min_refresh_sec: Annotated[int, Form()] = PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec,
        max_refresh_sec: Annotated[int, Form()] = PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec,
        max_items_per_scan: Annotated[
            int,
            Form(),
        ] = PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan,
        auto_load_more: Annotated[str | None, Form()] = None,
        auto_adjust_sort: Annotated[str | None, Form()] = None,
        enable_desktop_notification: Annotated[str | None, Form()] = None,
        enable_ntfy: Annotated[str | None, Form()] = None,
        ntfy_topic: Annotated[str, Form()] = "",
        enable_discord_notification: Annotated[str | None, Form()] = None,
        discord_webhook: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """從表單 URL 建立或更新 posts/comments target。"""

        try:
            keyword_defaults = get_target_keyword_defaults(request)
            config_form = _build_target_config_form(
                include_keywords=include_keywords,
                exclude_keywords=(
                    keyword_defaults.exclude_keywords_text
                    if exclude_keywords is None
                    else exclude_keywords
                ),
                exclude_ignore_phrases=(
                    keyword_defaults.exclude_ignore_phrases_text
                    if exclude_ignore_phrases is None
                    else exclude_ignore_phrases
                ),
                refresh_mode=refresh_mode,
                fixed_refresh_sec=fixed_refresh_sec,
                min_refresh_sec=min_refresh_sec,
                max_refresh_sec=max_refresh_sec,
                max_items_per_scan=max_items_per_scan,
                auto_load_more=auto_load_more,
                auto_adjust_sort=auto_adjust_sort,
                enable_desktop_notification=enable_desktop_notification,
                enable_ntfy=enable_ntfy,
                ntfy_topic=ntfy_topic,
                enable_discord_notification=enable_discord_notification,
                discord_webhook=discord_webhook,
            )
            custom_name = clean_facebook_page_title(display_name)
            scheduler_running = get_scheduler_manager(request).state().running
            route = detect_target_route_from_url(group_url.strip())
            if isinstance(route, DetectedCommentsTargetRoute):
                resolved_metadata = await _resolve_group_metadata_if_needed(
                    request,
                    custom_name=custom_name,
                    canonical_url=route.group_canonical_url,
                )
                with SqliteApplicationContext(get_db_path(request)) as app_context:
                    target = app_context.services.targets.upsert_comments_target(
                        config_form.to_comments_upsert_request(
                            group_id=route.group_id,
                            parent_post_id=route.parent_post_id,
                            canonical_url=route.canonical_url,
                            name=custom_name,
                            group_name=resolved_metadata.group_name,
                            group_cover_image_url=resolved_metadata.group_cover_image_url,
                        )
                    )
                    if scheduler_running and not custom_name:
                        target = app_context.services.targets.mark_target_metadata_refresh_pending(
                            target.id,
                        )
                if scheduler_running and not custom_name:
                    get_scheduler_manager(request).request_metadata_refresh(target.id)
            else:
                resolved_metadata = await _resolve_group_metadata_if_needed(
                    request,
                    custom_name=custom_name,
                    canonical_url=route.canonical_url,
                )
                with SqliteApplicationContext(get_db_path(request)) as app_context:
                    target = app_context.services.targets.upsert_group_posts_target(
                        config_form.to_group_posts_upsert_request(
                            group_id=route.group_id,
                            canonical_url=route.canonical_url,
                            name=custom_name,
                            group_name=resolved_metadata.group_name,
                            group_cover_image_url=resolved_metadata.group_cover_image_url,
                        )
                    )
                    if scheduler_running and not custom_name:
                        target = app_context.services.targets.mark_target_metadata_refresh_pending(
                            target.id,
                        )
                if scheduler_running and not custom_name:
                    get_scheduler_manager(request).request_metadata_refresh(target.id)
        except RouteDetectionError as exc:
            return redirect_new_target_with_error(str(exc))
        except GroupMetadataError as exc:
            return redirect_new_target_with_error(str(exc))
        except ProfileSessionError as exc:
            return redirect_new_target_with_error(str(exc))
        except Exception as exc:
            return redirect_new_target_with_error(
                "新增失敗：" + format_failure_message_text(str(exc))
            )
        return redirect_with_message("target 已新增", feedback="target_created")

    @app.post("/targets/{target_id}/config")
    async def update_config(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
        include_keywords: Annotated[str, Form()] = "",
        exclude_keywords: Annotated[str, Form()] = "",
        exclude_ignore_phrases: Annotated[str, Form()] = "",
        refresh_mode: Annotated[str, Form()] = "floating",
        fixed_refresh_sec: Annotated[int, Form()] = DEFAULT_WEBUI_FIXED_REFRESH_SECONDS,
        min_refresh_sec: Annotated[int, Form()] = PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec,
        max_refresh_sec: Annotated[int, Form()] = PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec,
        max_items_per_scan: Annotated[
            int,
            Form(),
        ] = PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan,
        auto_load_more: Annotated[str | None, Form()] = None,
        auto_adjust_sort: Annotated[str | None, Form()] = None,
        enable_desktop_notification: Annotated[str | None, Form()] = None,
        enable_ntfy: Annotated[str | None, Form()] = None,
        ntfy_topic: Annotated[str, Form()] = "",
        enable_discord_notification: Annotated[str | None, Form()] = None,
        discord_webhook: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """更新單一 target 設定。"""

        try:
            config_form = _build_target_config_form(
                include_keywords=include_keywords,
                exclude_keywords=exclude_keywords,
                exclude_ignore_phrases=exclude_ignore_phrases,
                refresh_mode=refresh_mode,
                fixed_refresh_sec=fixed_refresh_sec,
                min_refresh_sec=min_refresh_sec,
                max_refresh_sec=max_refresh_sec,
                max_items_per_scan=max_items_per_scan,
                auto_load_more=auto_load_more,
                auto_adjust_sort=auto_adjust_sort,
                enable_desktop_notification=enable_desktop_notification,
                enable_ntfy=enable_ntfy,
                ntfy_topic=ntfy_topic,
                enable_discord_notification=enable_discord_notification,
                discord_webhook=discord_webhook,
            )
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                app_context.services.targets.update_target_config(
                    config_form.to_update_request(target_id=target_id)
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
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                app_context.services.targets.update_target_name(target_id, display_name)
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
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                target = app_context.repositories.targets.get(target_id)
                if target is None:
                    return redirect_with_error("重新抓取失敗: target 不存在", return_to=return_to)
                app_context.services.targets.mark_target_metadata_refresh_pending(target_id)
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

        payload = await request.json()
        reported_url = str(payload.get("url", "")).strip() if isinstance(payload, dict) else ""
        min_interval_seconds = (
            PYTHON_SCHEDULER_RUNTIME_DEFAULTS.cover_image_load_failure_min_interval_seconds
        )
        with SqliteApplicationContext(get_db_path(request)) as app_context:
            result = app_context.services.targets.request_target_cover_image_refresh(
                target_id,
                reported_url=reported_url,
                min_interval_seconds=min_interval_seconds,
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
        return_to: Annotated[str, Form()] = "",
        enable_desktop_notification: Annotated[str | None, Form()] = None,
        enable_ntfy: Annotated[str | None, Form()] = None,
        ntfy_topic: Annotated[str, Form()] = "",
        enable_discord_notification: Annotated[str | None, Form()] = None,
        discord_webhook: Annotated[str, Form()] = "",
    ) -> object:
        """依 target 設定 modal 目前欄位送出一則測試通知，不保存設定。"""

        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                target = app_context.repositories.targets.get(target_id)
                if target is None:
                    if _wants_json_response(request):
                        return JSONResponse(
                            {"ok": False, "error": "測試通知失敗: target 不存在"},
                            status_code=404,
                        )
                    return redirect_with_error("測試通知失敗: target 不存在", return_to=return_to)
            config = NotificationConfigForm(
                enable_desktop_notification=enable_desktop_notification,
                enable_ntfy=enable_ntfy,
                ntfy_topic=ntfy_topic,
                enable_discord_notification=enable_discord_notification,
                discord_webhook=discord_webhook,
            ).to_target_config(target_id=target.id)
            results = await run_in_threadpool(
                send_manual_test_notification,
                config=config,
                ntfy_sender=get_ntfy_sender(request),
                desktop_sender=get_desktop_sender(request),
                discord_sender=get_discord_sender(request),
            )
        except Exception as exc:
            error_message = (
                "測試通知失敗: "
                + format_notification_event_message(
                    safe_exception_message("notification_test_failed", exc)
                )
            )
            if _wants_json_response(request):
                return JSONResponse(
                    {"ok": False, "error": error_message},
                    status_code=400,
                )
            return redirect_with_error(
                error_message,
                return_to=return_to,
            )
        localized_results = [
            format_notification_event_message(result)
            for result in results
        ]
        message = "測試通知結果：" + " / ".join(localized_results)
        if _wants_json_response(request):
            return JSONResponse({"ok": True, "message": message, "results": localized_results})
        return redirect_with_message(message, return_to=return_to)

    @app.post("/targets/{target_id}/start")
    async def restart_target_monitoring_route(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """重新開始單一 target，清 seen/outbox 去重並要求立即掃描。"""

        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                app_context.services.targets.restart_target_monitoring(target_id)
            get_scheduler_manager(request).wake()
        except Exception as exc:
            return redirect_with_error(
                "啟動失敗：" + format_failure_message_text(str(exc)),
                return_to=return_to,
            )
        return redirect_with_message(
            "target 已開始",
            return_to=return_to,
            feedback="target_started",
        )

    @app.post("/targets/{target_id}/stop")
    async def pause_target_monitoring_route(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """暫停單一 target，保留 seen scope 與歷史紀錄。"""

        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                app_context.services.targets.pause_target_monitoring(target_id)
            get_scheduler_manager(request).wake()
        except Exception as exc:
            return redirect_with_error(
                "停止失敗：" + format_failure_message_text(str(exc)),
                return_to=return_to,
            )
        return redirect_with_message(
            "target 已停止",
            return_to=return_to,
            feedback="target_stopped",
        )

    @app.post("/targets/{target_id}/delete")
    async def delete_target(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """刪除單一 target。"""

        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                app_context.services.targets.delete_target(target_id)
        except Exception as exc:
            return redirect_with_error(
                "刪除失敗：" + format_failure_message_text(str(exc)),
                return_to=return_to,
            )
        return redirect_with_message(
            "target 已刪除",
            return_to=return_to,
            feedback="target_deleted",
        )

    @app.post("/targets/{target_id}/scan-once")
    async def scan_once(request: Request, target_id: str) -> RedirectResponse:
        """要求 resident scheduler 對單一 target 執行一次掃描。"""

        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                target = app_context.repositories.targets.get(target_id)
                if target is None:
                    return redirect_with_error("掃描失敗：target 不存在")
                if not target.enabled or target.paused:
                    return redirect_with_error("掃描失敗：請先開始 target")
                app_context.services.targets.request_target_scan(target_id)
            start_resident_scheduler_if_needed(request)
        except Exception as exc:
            logger.exception("scan once failed", extra={"target_id": target_id})
            return redirect_with_error("掃描失敗：" + format_failure_message_text(str(exc)))
        return redirect_with_message("已排入掃描", feedback="scan_requested")
