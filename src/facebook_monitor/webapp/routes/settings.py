"""Settings routes。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
import sys

from fastapi import FastAPI
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from facebook_monitor.application.context import SqliteApplicationContext
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
from facebook_monitor.updates.launcher import find_bundled_updater
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
from facebook_monitor.webapp.dependencies import open_profile_options
from facebook_monitor.webapp.dependencies import pause_scheduler_for_profile_use
from facebook_monitor.webapp.dependencies import redirect_settings_with_error
from facebook_monitor.webapp.dependencies import redirect_settings_with_message
from facebook_monitor.webapp.dependencies import resume_scheduler_after_profile_use
from facebook_monitor.webapp.form_models import NotificationConfigForm
from facebook_monitor.webapp.profile_session import ProfileSessionError
from facebook_monitor.webapp.runtime_diagnostics import build_runtime_diagnostics_view


def register_settings_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    """註冊 settings / notification / profile routes。"""

    @app.get("/settings")
    async def settings(request: Request) -> object:
        """顯示全域設定頁。"""

        message = request.query_params.get("message", "")
        error = request.query_params.get("error", "")
        metadata = collect_build_metadata(asset_version=ASSET_VERSION)
        paths = get_runtime_paths(request)
        update_check = build_idle_update_check(
            current_version=metadata.app_version,
            channel="stable",
        )
        if request.query_params.get("update_check") == "1":
            update_check = await check_github_release_updates(
                current_version=metadata.app_version,
                channel="stable",
            )
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "message": message,
                "error": error,
                "profile_dir": str(get_profile_dir(request)),
                "notification_settings": get_global_notification_settings(request),
                "target_keyword_defaults": get_target_keyword_defaults(request),
                "runtime_diagnostics": build_runtime_diagnostics_view(request.app.state),
                "update_check": update_check,
                "update_download_supported": _update_download_supported(
                    packaging_mode=metadata.packaging_mode,
                    frozen=metadata.frozen,
                    app_base_dir=paths.app_base_dir,
                ),
                "pending_update_available": pending_update_path(paths.runtime_dir).is_file(),
                "initial_theme": get_app_theme(request),
            },
        )

    @app.post("/settings/theme")
    async def update_theme(request: Request) -> dict[str, str]:
        """保存 Web UI theme preference，避免 auto-port 時遺失主題。"""

        payload = await request.json()
        theme = str(payload.get("theme", "")).strip()
        if theme not in {"light", "dark"}:
            raise HTTPException(status_code=400, detail="invalid theme")
        with SqliteApplicationContext(get_db_path(request)) as app_context:
            saved_theme = app_context.repositories.app_settings.save_theme(theme)
        return {"theme": saved_theme}

    @app.post("/settings/notifications")
    async def update_global_notifications(
        request: Request,
        enable_desktop_notification: Annotated[str | None, Form()] = None,
        enable_ntfy: Annotated[str | None, Form()] = None,
        ntfy_topic: Annotated[str, Form()] = "",
        enable_discord_notification: Annotated[str | None, Form()] = None,
        discord_webhook: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """更新 Web UI 通知預設值。"""

        settings = NotificationConfigForm(
            enable_desktop_notification=enable_desktop_notification,
            enable_ntfy=enable_ntfy,
            ntfy_topic=ntfy_topic,
            enable_discord_notification=enable_discord_notification,
            discord_webhook=discord_webhook,
        ).to_global_settings()
        with SqliteApplicationContext(get_db_path(request)) as app_context:
            app_context.repositories.global_notification_settings.save(settings)
        return redirect_settings_with_message("通知預設值已保存")

    @app.post("/settings/target-keywords")
    async def update_target_keyword_defaults(
        request: Request,
        exclude_keywords: Annotated[str, Form()] = "",
        exclude_ignore_phrases: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """更新新增 target 時套用的關鍵字預設值。"""

        settings = TargetKeywordDefaultSettings(
            exclude_keywords_text=exclude_keywords,
            exclude_ignore_phrases_text=exclude_ignore_phrases,
        )
        with SqliteApplicationContext(get_db_path(request)) as app_context:
            app_context.repositories.app_settings.save_target_keyword_defaults(settings)
        return redirect_settings_with_message("關鍵字預設值已保存")

    @app.post("/settings/updates/download")
    async def download_update(request: Request) -> RedirectResponse:
        """下載並驗證 Windows portable 更新包，但不套用更新。"""

        metadata = collect_build_metadata(asset_version=ASSET_VERSION)
        if not _update_download_supported(
            packaging_mode=metadata.packaging_mode,
            frozen=metadata.frozen,
            app_base_dir=get_runtime_paths(request).app_base_dir,
        ):
            return redirect_settings_with_error(
                "目前執行環境不是 Windows PyInstaller 打包版，僅支援檢查更新"
            )
        update_check = await check_github_release_updates(
            current_version=metadata.app_version,
            channel="stable",
        )
        if not update_check.update_available:
            reason = update_check.failure_reason or update_check.status
            return redirect_settings_with_error(f"沒有可下載的更新：{reason}")
        paths = get_runtime_paths(request)
        result = await download_and_verify_update(
            update_check=update_check,
            updates_dir=paths.updates_dir,
        )
        if not result.verified:
            return redirect_settings_with_error(
                f"更新下載或驗證失敗：{result.failure_reason}"
            )
        try:
            write_pending_update(
                update_check=update_check,
                download_result=result,
                paths=paths,
            )
        except ValueError as exc:
            return redirect_settings_with_error(f"更新交接檔建立失敗：{exc}")
        file_path = result.file_path
        opened = reveal_in_file_manager(file_path) if file_path is not None else False
        suffix = "，已開啟下載資料夾" if opened else ""
        return redirect_settings_with_message(
            "更新下載完成並已驗證；已建立交接檔："
            f"{pending_update_path(paths.runtime_dir)}{suffix}"
        )

    @app.post("/settings/updates/apply")
    async def apply_update(request: Request) -> RedirectResponse:
        """啟動 temp updater，讓它等待主程式退出後套用已驗證更新。"""

        metadata = collect_build_metadata(asset_version=ASSET_VERSION)
        if not _update_download_supported(
            packaging_mode=metadata.packaging_mode,
            frozen=metadata.frozen,
            app_base_dir=get_runtime_paths(request).app_base_dir,
        ):
            return redirect_settings_with_error(
                "目前執行環境不是 Windows PyInstaller 打包版，僅支援檢查更新"
            )
        paths = get_runtime_paths(request)
        result = launch_temp_updater(paths=paths)
        if not result.launched:
            return redirect_settings_with_error(f"無法啟動更新器：{result.message}")
        shutdown_requested = _request_app_shutdown(request)
        if shutdown_requested:
            return redirect_settings_with_message(
                "更新器已啟動，程式即將關閉並套用更新"
            )
        return redirect_settings_with_message(
            "更新器已啟動；請從右下角 tray 選單完整退出程式後套用更新"
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
        enable_desktop_notification: Annotated[str | None, Form()] = None,
        enable_ntfy: Annotated[str | None, Form()] = None,
        ntfy_topic: Annotated[str, Form()] = "",
        enable_discord_notification: Annotated[str | None, Form()] = None,
        discord_webhook: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """依 settings 頁目前表單欄位送出一則測試通知，不保存設定。"""

        config = NotificationConfigForm(
            enable_desktop_notification=enable_desktop_notification,
            enable_ntfy=enable_ntfy,
            ntfy_topic=ntfy_topic,
            enable_discord_notification=enable_discord_notification,
            discord_webhook=discord_webhook,
        ).to_target_config(target_id="global-notification-test")
        try:
            results = await run_in_threadpool(
                send_manual_test_notification,
                config=config,
                ntfy_sender=get_ntfy_sender(request),
                desktop_sender=get_desktop_sender(request),
                discord_sender=get_discord_sender(request),
            )
        except Exception as exc:
            return redirect_settings_with_error(
                "測試通知失敗："
                + safe_exception_message("notification_test_failed", exc)
            )
        return redirect_settings_with_message("測試通知結果：" + " / ".join(results))

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
            return redirect_settings_with_error(str(exc))
        return redirect_settings_with_message("Facebook 設定視窗已開啟")

    @app.post("/settings/facebook/close")
    async def close_facebook_profile(request: Request) -> RedirectResponse:
        """關閉 Facebook automation profile 設定視窗。"""

        await run_in_threadpool(get_profile_manager(request).close)
        resume_scheduler_after_profile_use(request)
        return redirect_settings_with_message("Facebook 設定視窗已關閉")


def _update_download_supported(
    *,
    packaging_mode: str,
    frozen: bool,
    app_base_dir: object,
) -> bool:
    """只有 Windows frozen / PyInstaller onedir 且含 updater exe 才可套用更新。"""

    normalized = packaging_mode.strip().casefold()
    if not _is_windows():
        return False
    if not (frozen or normalized.startswith("pyinstaller")):
        return False
    return find_bundled_updater(Path(str(app_base_dir))) is not None


def _is_windows() -> bool:
    """集中平台判斷，方便測試替換。"""

    return sys.platform == "win32"


def _request_app_shutdown(request: Request) -> bool:
    """要求 launcher 關閉 Web UI；測試或舊啟動路徑未提供時回 False。"""

    shutdown = getattr(request.app.state, "request_shutdown", None)
    if not callable(shutdown):
        return False
    shutdown()
    return True
