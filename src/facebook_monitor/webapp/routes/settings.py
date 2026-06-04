"""Settings routes。"""

from __future__ import annotations

from typing import Annotated
import platform
import sys

from fastapi import Depends
from fastapi import FastAPI
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.notification_admin import clear_failed_notifications
from facebook_monitor.application.notification_admin import load_notification_outbox_health
from facebook_monitor.application.notification_admin import retry_failed_notifications
from facebook_monitor.application.update_flow import download_and_launch_verified_update
from facebook_monitor.application.update_flow import download_verified_update
from facebook_monitor.application.update_flow import launch_verified_update
from facebook_monitor.core.input_limits import parse_limited_keywords_text
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.core.user_messages import format_notification_event_message
from facebook_monitor.diagnostics.support_bundle import create_support_bundle
from facebook_monitor.notifications.manual_test import send_manual_test_notification
from facebook_monitor.notifications.safe_messages import safe_exception_message
from facebook_monitor.persistence.repositories.app_settings import TargetKeywordDefaultSettings
from facebook_monitor.runtime.build_metadata import collect_build_metadata
from facebook_monitor.updates.release_check import build_idle_update_check
from facebook_monitor.updates.release_check import check_github_release_updates
from facebook_monitor.updates.download import download_and_verify_update
from facebook_monitor.updates.download import reveal_in_file_manager
from facebook_monitor.updates.handoff import pending_update_path
from facebook_monitor.updates.handoff import write_pending_update
from facebook_monitor.updates.capability import resolve_update_capability
from facebook_monitor.updates.capability import UpdateCapability
from facebook_monitor.updates.launcher import launch_temp_updater
from facebook_monitor.webapp.assets import ASSET_VERSION
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_app_theme
from facebook_monitor.webapp.dependencies import get_desktop_sender
from facebook_monitor.webapp.dependencies import get_discord_sender
from facebook_monitor.webapp.dependencies import get_global_notification_settings
from facebook_monitor.webapp.dependencies import get_ntfy_sender
from facebook_monitor.webapp.dependencies import get_profile_dir
from facebook_monitor.webapp.dependencies import get_profile_manager
from facebook_monitor.webapp.dependencies import get_runtime_paths
from facebook_monitor.webapp.dependencies import get_target_keyword_defaults
from facebook_monitor.webapp.request_payloads import json_object_payload
from facebook_monitor.webapp.dependencies import open_profile_options
from facebook_monitor.webapp.dependencies import pause_scheduler_for_profile_use
from facebook_monitor.webapp.dependencies import redirect_settings_with_error
from facebook_monitor.webapp.dependencies import redirect_settings_with_message
from facebook_monitor.webapp.dependencies import resume_scheduler_after_profile_use
from facebook_monitor.webapp.form_models import format_notification_form_error
from facebook_monitor.webapp.form_models import NotificationConfigForm
from facebook_monitor.webapp.profile_session import ProfileSessionError
from facebook_monitor.webapp.runtime_diagnostics import build_runtime_diagnostics_view


def register_settings_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    """註冊 settings / notification / profile routes。"""

    @app.get("/settings")
    async def settings(request: Request) -> object:
        """顯示全域設定頁。"""

        message = request.query_params.get("message", "")
        feedback = request.query_params.get("feedback", "")
        error = request.query_params.get("error", "")
        metadata = collect_build_metadata(asset_version=ASSET_VERSION)
        paths = get_runtime_paths(request)
        update_capability = _resolve_update_capability(
            packaging_mode=metadata.packaging_mode,
            frozen=metadata.frozen,
            app_base_dir=paths.app_base_dir,
        )
        update_check = build_idle_update_check(
            current_version=metadata.app_version,
            channel="stable",
            allow_env_repository_override=not metadata.frozen,
        )
        if request.query_params.get("update_check") == "1":
            update_check = await check_github_release_updates(
                current_version=metadata.app_version,
                channel="stable",
                allow_env_repository_override=not metadata.frozen,
            )
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "message": message,
                "feedback": feedback,
                "error": error,
                "profile_dir": str(get_profile_dir(request)),
                "notification_settings": get_global_notification_settings(request),
                "target_keyword_defaults": get_target_keyword_defaults(request),
                "runtime_diagnostics": build_runtime_diagnostics_view(request.app.state),
                "notification_outbox_health": load_notification_outbox_health(
                    get_db_path(request)
                ),
                "update_check": update_check,
                "update_download_supported": update_capability.download_supported,
                "update_apply_supported": update_capability.apply_supported,
                "update_unsupported_reason": update_capability.unsupported_reason,
                "pending_update_available": pending_update_path(paths.runtime_dir).is_file(),
                "initial_theme": get_app_theme(request),
            },
        )

    @app.post("/settings/theme")
    async def update_theme(request: Request) -> dict[str, str]:
        """保存 Web UI theme preference，避免 auto-port 時遺失主題。"""

        payload = await json_object_payload(request)
        theme = str(payload.get("theme", "")).strip()
        if theme not in {"light", "dark"}:
            raise HTTPException(status_code=400, detail="invalid theme")
        with SqliteApplicationContext(get_db_path(request)) as app_context:
            saved_theme = app_context.repositories.app_settings.save_theme(theme)
        return {"theme": saved_theme}

    @app.post("/settings/notifications")
    async def update_global_notifications(
        request: Request,
        notification_form: Annotated[
            NotificationConfigForm,
            Depends(NotificationConfigForm.as_form),
        ],
    ) -> RedirectResponse:
        """更新 Web UI 通知預設值。"""

        with SqliteApplicationContext(get_db_path(request)) as app_context:
            current_settings = app_context.repositories.global_notification_settings.get()
            try:
                settings = notification_form.to_global_settings(
                    existing_ntfy_topic=current_settings.ntfy_topic,
                    existing_discord_webhook=current_settings.discord_webhook,
                )
            except ValueError as exc:
                return redirect_settings_with_error(format_notification_form_error(exc))
            app_context.repositories.global_notification_settings.save(settings)
        return redirect_settings_with_message(
            "通知預設值已保存",
            feedback="notification_defaults_saved",
        )

    @app.post("/settings/target-keywords")
    async def update_target_keyword_defaults(
        request: Request,
        exclude_keywords: Annotated[str, Form()] = "",
        exclude_ignore_phrases: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """更新新增 target 時套用的關鍵字預設值。"""

        try:
            parse_limited_keywords_text(exclude_keywords, field_label="排除關鍵字預設")
            parse_limited_keywords_text(
                exclude_ignore_phrases,
                field_label="排除字忽略片語預設",
            )
        except ValueError as exc:
            return redirect_settings_with_error(str(exc))
        settings = TargetKeywordDefaultSettings(
            exclude_keywords_text=exclude_keywords,
            exclude_ignore_phrases_text=exclude_ignore_phrases,
        )
        with SqliteApplicationContext(get_db_path(request)) as app_context:
            app_context.repositories.app_settings.save_target_keyword_defaults(settings)
        return redirect_settings_with_message(
            "關鍵字預設值已保存",
            feedback="target_keyword_defaults_saved",
        )

    @app.post("/settings/updates/download")
    async def download_update(request: Request) -> RedirectResponse:
        """下載並驗證更新包；不支援自動套用的平台只保留下載結果。"""

        metadata = collect_build_metadata(asset_version=ASSET_VERSION)
        paths = get_runtime_paths(request)
        update_capability = _resolve_update_capability(
            packaging_mode=metadata.packaging_mode,
            frozen=metadata.frozen,
            app_base_dir=paths.app_base_dir,
        )
        if not update_capability.download_supported:
            return redirect_settings_with_error(
                update_capability.unsupported_reason
            )
        outcome = await download_verified_update(
            current_version=metadata.app_version,
            paths=paths,
            update_capability=update_capability,
            allow_env_repository_override=not metadata.frozen,
            check_updates=check_github_release_updates,
            download_update=download_and_verify_update,
            reveal_file_manager=reveal_in_file_manager,
            write_pending_update=write_pending_update,
            reveal_download=True,
        )
        if not outcome.ok:
            return redirect_settings_with_error(outcome.message)
        return redirect_settings_with_message(outcome.message)

    @app.post("/settings/updates/apply")
    async def apply_update(request: Request) -> RedirectResponse:
        """啟動 temp updater，讓它等待主程式退出後套用已驗證更新。"""

        metadata = collect_build_metadata(asset_version=ASSET_VERSION)
        paths = get_runtime_paths(request)
        update_capability = _resolve_update_capability(
            packaging_mode=metadata.packaging_mode,
            frozen=metadata.frozen,
            app_base_dir=paths.app_base_dir,
        )
        if not update_capability.apply_supported:
            return redirect_settings_with_error(update_capability.unsupported_reason)
        outcome = launch_verified_update(
            paths=paths,
            update_capability=update_capability,
            launch_updater=launch_temp_updater,
            request_shutdown=lambda: _request_app_shutdown(request),
        )
        if not outcome.ok:
            return redirect_settings_with_error(outcome.message)
        return redirect_settings_with_message(outcome.message)

    @app.post("/settings/updates/download-and-apply")
    async def download_and_apply_update(request: Request) -> JSONResponse:
        """下載、驗證並啟動 updater；供 settings 頁 modal 流程使用。"""

        metadata = collect_build_metadata(asset_version=ASSET_VERSION)
        paths = get_runtime_paths(request)
        update_capability = _resolve_update_capability(
            packaging_mode=metadata.packaging_mode,
            frozen=metadata.frozen,
            app_base_dir=paths.app_base_dir,
        )
        if not update_capability.apply_supported:
            return _update_json_error(update_capability.unsupported_reason, stage="environment")
        outcome = await download_and_launch_verified_update(
            current_version=metadata.app_version,
            paths=paths,
            update_capability=update_capability,
            allow_env_repository_override=not metadata.frozen,
            check_updates=check_github_release_updates,
            download_update=download_and_verify_update,
            write_pending_update=write_pending_update,
            launch_updater=launch_temp_updater,
            request_shutdown=lambda: _request_app_shutdown(request),
        )
        if not outcome.ok:
            return _update_json_error(outcome.message, stage=outcome.stage)
        return JSONResponse(
            {
                "ok": True,
                "stage": outcome.stage,
                "message": outcome.message,
                "latest_version": outcome.latest_version,
                "shutdown_requested": outcome.shutdown_requested,
            }
        )

    @app.post("/settings/notifications/apply-to-targets")
    async def apply_global_notifications_to_targets(request: Request) -> RedirectResponse:
        """將通知預設值套用到所有 target 設定。"""

        with SqliteApplicationContext(get_db_path(request)) as app_context:
            settings = app_context.repositories.global_notification_settings.get()
            count = app_context.services.targets.apply_global_notification_settings(settings)
        return redirect_settings_with_message(f"已套用通知預設值到 {count} 個 target 設定")

    @app.post("/settings/notifications/test")
    async def test_global_notifications(
        request: Request,
        notification_form: Annotated[
            NotificationConfigForm,
            Depends(NotificationConfigForm.as_form),
        ],
    ) -> RedirectResponse:
        """依 settings 頁目前表單欄位送出一則測試通知，不保存設定。"""

        try:
            current_settings = get_global_notification_settings(request)
            config = notification_form.to_target_config(
                target_id="global-notification-test",
                existing_ntfy_topic=current_settings.ntfy_topic,
                existing_discord_webhook=current_settings.discord_webhook,
            )
            results = await run_in_threadpool(
                send_manual_test_notification,
                config=config,
                ntfy_sender=get_ntfy_sender(request),
                desktop_sender=get_desktop_sender(request),
                discord_sender=get_discord_sender(request),
            )
        except ValueError as exc:
            return redirect_settings_with_error(
                "測試通知失敗：" + format_notification_form_error(exc)
            )
        except Exception as exc:
            return redirect_settings_with_error(
                "測試通知失敗："
                + format_notification_event_message(
                    safe_exception_message("notification_test_failed", exc)
                )
            )
        localized_results = [
            format_notification_event_message(result)
            for result in results
        ]
        return redirect_settings_with_message("測試通知結果：" + " / ".join(localized_results))

    @app.post("/settings/notifications/retry-failed")
    async def retry_failed_notification_outbox_route(request: Request) -> RedirectResponse:
        """手動重試 failed notification outbox rows。"""

        try:
            dispatched_count = await run_in_threadpool(
                retry_failed_notifications,
                db_path=get_db_path(request),
                ntfy_sender=get_ntfy_sender(request),
                desktop_sender=get_desktop_sender(request),
                discord_sender=get_discord_sender(request),
            )
        except Exception as exc:
            return redirect_settings_with_error(
                "重試通知失敗："
                + format_notification_event_message(
                    safe_exception_message("notification_retry_failed", exc)
                )
            )
        return redirect_settings_with_message(
            f"已重試 failed 通知 {dispatched_count} 筆",
            feedback="notification_retry_finished",
        )

    @app.post("/settings/notifications/clear-failed")
    async def clear_failed_notification_outbox_route(request: Request) -> RedirectResponse:
        """手動清除 failed notification outbox rows，不影響 pending rows。"""

        try:
            cleared_count = await run_in_threadpool(
                clear_failed_notifications,
                db_path=get_db_path(request),
            )
        except Exception as exc:
            return redirect_settings_with_error(
                "清除 failed 通知失敗："
                + format_notification_event_message(
                    safe_exception_message("notification_clear_failed", exc)
                )
            )
        return redirect_settings_with_message(
            f"已清除 failed 通知 {cleared_count} 筆",
            feedback="notification_clear_failed_finished",
        )

    @app.post("/settings/support-bundle")
    async def download_support_bundle(request: Request) -> object:
        """建立並下載 redacted support bundle。"""

        try:
            paths = get_runtime_paths(request)
            metadata = collect_build_metadata(asset_version=ASSET_VERSION)
            diagnostics = build_runtime_diagnostics_view(request.app.state)
            result = await run_in_threadpool(
                create_support_bundle,
                paths=paths,
                runtime_diagnostics_text=diagnostics.copy_text,
                app_metadata={
                    "app_version": metadata.app_version,
                    "asset_version": metadata.asset_version,
                    "packaging_mode": metadata.packaging_mode,
                    "python_version": metadata.python_version,
                },
            )
        except Exception as exc:
            return redirect_settings_with_error(
                "支援診斷包建立失敗：" + format_failure_message_text(str(exc))
            )
        return FileResponse(
            result.path,
            media_type="application/zip",
            filename=result.filename,
        )

    @app.post("/settings/facebook/open")
    async def open_facebook_profile(request: Request) -> RedirectResponse:
        """開啟 Facebook automation profile 設定視窗。"""

        try:
            pause_scheduler_for_profile_use(request)
            try:
                await run_in_threadpool(
                    get_profile_manager(request).open,
                    open_profile_options(request),
                )
            except Exception:
                resume_scheduler_after_profile_use(request)
                raise
        except ProfileSessionError as exc:
            return redirect_settings_with_error(format_failure_message_text(str(exc)))
        return redirect_settings_with_message("Facebook 設定視窗已開啟")

    @app.post("/settings/facebook/close")
    async def close_facebook_profile(request: Request) -> RedirectResponse:
        """關閉 Facebook automation profile 設定視窗。"""

        await run_in_threadpool(get_profile_manager(request).close)
        resume_scheduler_after_profile_use(request)
        return redirect_settings_with_message("Facebook 設定視窗已關閉")


def _resolve_update_capability(
    *,
    packaging_mode: str,
    frozen: bool,
    app_base_dir: object,
) -> UpdateCapability:
    """依 settings 測試替換後的平台判斷委派 updates capability。"""

    return resolve_update_capability(
        packaging_mode=packaging_mode,
        frozen=frozen,
        app_base_dir=app_base_dir,
        system=_current_update_system(),
        machine=_current_update_machine(),
    )


def _is_windows() -> bool:
    """集中平台判斷，方便測試替換。"""

    return sys.platform == "win32"


def _is_macos() -> bool:
    """集中 macOS 平台判斷，方便測試替換。"""

    return sys.platform == "darwin"


def _current_update_system() -> str:
    """依 settings route 的平台 seam 回傳 capability 判斷用平台名稱。"""

    if _is_macos():
        return "darwin"
    if _is_windows():
        return "win32"
    return sys.platform


def _current_update_machine() -> str:
    """依 settings route 的平台 seam 回傳 capability 判斷用 CPU 架構。"""

    return platform.machine()


def _request_app_shutdown(request: Request) -> bool:
    """要求 launcher 關閉 Web UI；測試或舊啟動路徑未提供時回 False。"""

    shutdown = getattr(request.app.state, "request_shutdown", None)
    if not callable(shutdown):
        return False
    shutdown()
    return True


def _update_json_error(message: str, *, stage: str) -> JSONResponse:
    """回傳 settings updater modal 可直接顯示的錯誤。"""

    return JSONResponse(
        {
            "ok": False,
            "stage": stage,
            "error": message,
        },
        status_code=400,
    )
