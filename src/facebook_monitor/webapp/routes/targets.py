"""Target management routes。"""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import DEFAULT_WEBUI_FIXED_REFRESH_SECONDS
from facebook_monitor.application.target_route_service import DetectedCommentsTargetRoute
from facebook_monitor.application.target_route_service import detect_target_route_from_url
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.facebook.group_metadata import GroupMetadataError
from facebook_monitor.facebook.route_detection import RouteDetectionError
from facebook_monitor.facebook.route_detection import clean_facebook_page_title
from facebook_monitor.notifications.manual_test import send_manual_test_notification
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_desktop_sender
from facebook_monitor.webapp.dependencies import get_discord_sender
from facebook_monitor.webapp.dependencies import get_global_notification_settings
from facebook_monitor.webapp.dependencies import get_ntfy_sender
from facebook_monitor.webapp.dependencies import get_group_name_resolver
from facebook_monitor.webapp.dependencies import get_profile_dir
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import redirect_new_target_with_error
from facebook_monitor.webapp.dependencies import redirect_with_error
from facebook_monitor.webapp.dependencies import redirect_with_message
from facebook_monitor.webapp.dependencies import run_with_temporary_profile_access
from facebook_monitor.webapp.dependencies import start_resident_scheduler_if_needed
from facebook_monitor.webapp.form_models import TargetConfigForm
from facebook_monitor.webapp.profile_session import ProfileSessionError


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
            },
        )

    @app.post("/targets")
    async def create_target(
        request: Request,
        group_url: Annotated[str, Form()],
        display_name: Annotated[str, Form()] = "",
        include_keywords: Annotated[str, Form()] = "",
        exclude_keywords: Annotated[str, Form()] = "",
        refresh_mode: Annotated[str, Form()] = "fixed",
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
            config_form = TargetConfigForm(
                include_keywords=include_keywords,
                exclude_keywords=exclude_keywords,
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
            route = detect_target_route_from_url(group_url.strip())
            if isinstance(route, DetectedCommentsTargetRoute):
                resolved_group_name = ""
                if not custom_name:
                    resolved_group_name = await run_with_temporary_profile_access(
                        request,
                        lambda: get_group_name_resolver(request)(
                            get_profile_dir(request),
                            route.group_canonical_url,
                        ),
                    )
                with SqliteApplicationContext(get_db_path(request)) as app_context:
                    app_context.services.targets.upsert_comments_target(
                        config_form.to_comments_upsert_request(
                            group_id=route.group_id,
                            parent_post_id=route.parent_post_id,
                            canonical_url=route.canonical_url,
                            name=custom_name,
                            group_name=resolved_group_name,
                        )
                    )
            else:
                resolved_group_name = ""
                if not custom_name:
                    resolved_group_name = await run_with_temporary_profile_access(
                        request,
                        lambda: get_group_name_resolver(request)(
                            get_profile_dir(request),
                            route.canonical_url,
                        ),
                    )
                with SqliteApplicationContext(get_db_path(request)) as app_context:
                    app_context.services.targets.upsert_group_posts_target(
                        config_form.to_group_posts_upsert_request(
                            group_id=route.group_id,
                            canonical_url=route.canonical_url,
                            name=custom_name,
                            group_name=resolved_group_name,
                        )
                    )
        except RouteDetectionError as exc:
            return redirect_new_target_with_error(str(exc))
        except GroupMetadataError as exc:
            return redirect_new_target_with_error(str(exc))
        except ProfileSessionError as exc:
            return redirect_new_target_with_error(str(exc))
        except Exception as exc:
            return redirect_new_target_with_error(f"新增失敗: {exc}")
        return redirect_with_message("target 已新增")

    @app.post("/targets/{target_id}/config")
    async def update_config(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
        include_keywords: Annotated[str, Form()] = "",
        exclude_keywords: Annotated[str, Form()] = "",
        refresh_mode: Annotated[str, Form()] = "fixed",
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
        """更新 target 所屬社團設定。"""

        try:
            config_form = TargetConfigForm(
                include_keywords=include_keywords,
                exclude_keywords=exclude_keywords,
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
            return redirect_with_error(f"設定更新失敗: {exc}", return_to=return_to)
        return redirect_with_message("設定已更新", return_to=return_to)

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
    ) -> RedirectResponse:
        """依 target 設定 modal 目前欄位送出一則測試通知，不保存設定。"""

        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                target = app_context.repositories.targets.get(target_id)
                if target is None:
                    raise ValueError("target 不存在")
            config = TargetConfig(
                group_id=target.group_id,
                enable_desktop_notification=enable_desktop_notification == "on",
                enable_ntfy=enable_ntfy == "on",
                ntfy_topic=ntfy_topic.strip(),
                enable_discord_notification=enable_discord_notification == "on",
                discord_webhook=discord_webhook.strip(),
            )
            results = await run_in_threadpool(
                send_manual_test_notification,
                config=config,
                ntfy_sender=get_ntfy_sender(request),
                desktop_sender=get_desktop_sender(request),
                discord_sender=get_discord_sender(request),
            )
        except Exception as exc:
            return redirect_with_error(f"測試通知失敗: {exc}", return_to=return_to)
        return redirect_with_message("測試通知結果：" + " / ".join(results), return_to=return_to)

    @app.post("/targets/{target_id}/start")
    async def restart_target_monitoring_route(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """重新開始單一 target，清 seen scope 並要求立即掃描。"""

        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                app_context.services.targets.restart_target_monitoring(target_id)
            get_scheduler_manager(request).wake()
        except Exception as exc:
            return redirect_with_error(f"啟動失敗: {exc}", return_to=return_to)
        return redirect_with_message("target 已開始", return_to=return_to)

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
            return redirect_with_error(f"停止失敗: {exc}", return_to=return_to)
        return redirect_with_message("target 已停止", return_to=return_to)

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
            return redirect_with_error(f"刪除失敗: {exc}", return_to=return_to)
        return redirect_with_message("target 已刪除", return_to=return_to)

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
        except Exception:
            return redirect_with_error("掃描失敗，請查看 terminal 或 scan_runs")
        return redirect_with_message("已排入掃描")
