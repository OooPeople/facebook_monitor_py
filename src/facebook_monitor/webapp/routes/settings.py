"""Settings routes。"""

from __future__ import annotations

from dataclasses import dataclass
import platform
import sys
from typing import Annotated

from fastapi import FastAPI
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from facebook_monitor.application.notification_admin import clear_failed_notifications
from facebook_monitor.application.update_flow import download_and_launch_verified_update
from facebook_monitor.application.update_flow import download_verified_update
from facebook_monitor.application.update_flow import launch_verified_update
from facebook_monitor.core.input_limits import parse_limited_keywords_text
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.core.user_messages import format_notification_event_message
from facebook_monitor.diagnostics.support_bundle import create_support_bundle
from facebook_monitor.diagnostics.support_bundle import SupportBundleResult
from facebook_monitor.notifications.safe_messages import safe_exception_message
from facebook_monitor.persistence.repositories.app_settings import TargetKeywordDefaultSettings
from facebook_monitor.runtime.build_metadata import BuildMetadata
from facebook_monitor.runtime.build_metadata import collect_build_metadata
from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.runtime.update_operation_lock import acquire_update_operation_lock
from facebook_monitor.runtime.update_operation_lock import UpdateOperationLockError
from facebook_monitor.updates.release_check import build_idle_update_check
from facebook_monitor.updates.release_check import check_github_release_updates
from facebook_monitor.updates.release_check import UpdateCheckResult
from facebook_monitor.updates.download import download_and_verify_update
from facebook_monitor.updates.download import reveal_in_file_manager
from facebook_monitor.updates.handoff import write_pending_update
from facebook_monitor.updates.capability import resolve_update_capability
from facebook_monitor.updates.capability import UpdateCapability
from facebook_monitor.updates.launcher import launch_temp_updater
from facebook_monitor.webapp.assets import ASSET_VERSION
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_profile_manager
from facebook_monitor.webapp.dependencies import get_runtime_paths
from facebook_monitor.webapp.request_payloads import json_object_payload
from facebook_monitor.webapp.dependencies import open_profile_options
from facebook_monitor.webapp.dependencies import pause_scheduler_for_profile_use
from facebook_monitor.webapp.dependencies import redirect_settings_with_error
from facebook_monitor.webapp.dependencies import redirect_settings_with_message
from facebook_monitor.webapp.dependencies import resume_scheduler_after_profile_use
from facebook_monitor.webapp.dependencies import run_web_app_context_operation
from facebook_monitor.webapp.dependencies import run_web_db_operation
from facebook_monitor.webapp.profile_session import ProfileSessionError
from facebook_monitor.webapp.runtime_diagnostics import build_runtime_diagnostics_view
from facebook_monitor.webapp.settings_view import build_settings_template_context


@dataclass(frozen=True)
class _SettingsUpdateContext:
    """Settings route 執行更新流程時共用的 runtime context。"""

    metadata: BuildMetadata
    paths: RuntimePaths
    update_capability: UpdateCapability

    @property
    def allow_env_repository_override(self) -> bool:
        """source mode 保留測試與 debug repository override。"""

        return not self.metadata.frozen


def register_settings_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    """註冊 settings / notification / profile routes。"""

    @app.get("/settings")
    async def settings(request: Request) -> object:
        """顯示全域設定頁。"""

        message = request.query_params.get("message", "")
        feedback = request.query_params.get("feedback", "")
        error = request.query_params.get("error", "")
        update_context = _build_settings_update_context(request)
        update_check = await _load_settings_update_check(request, update_context)
        return templates.TemplateResponse(
            request,
            "settings.html",
            await build_settings_template_context(
                request,
                update_context,
                update_check,
                message=message,
                feedback=feedback,
                error=error,
            ),
        )

    @app.post("/settings/theme")
    async def update_theme(request: Request) -> dict[str, str]:
        """保存 Web UI theme preference，避免 auto-port 時遺失主題。"""

        payload = await json_object_payload(request)
        theme = str(payload.get("theme", "")).strip()
        if theme not in {"light", "dark"}:
            raise HTTPException(status_code=400, detail="invalid theme")
        return {"theme": await _save_app_theme(request, theme)}

    @app.post("/settings/target-keywords")
    async def update_target_keyword_defaults(
        request: Request,
        exclude_keywords: Annotated[str, Form()] = "",
        exclude_ignore_phrases: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """更新新增 target 時套用的關鍵字預設值。"""

        try:
            settings = _parse_target_keyword_defaults(
                exclude_keywords=exclude_keywords,
                exclude_ignore_phrases=exclude_ignore_phrases,
            )
        except ValueError as exc:
            return redirect_settings_with_error(str(exc))
        await _save_target_keyword_defaults(request, settings)
        return redirect_settings_with_message(
            "關鍵字預設值已保存",
            feedback="target_keyword_defaults_saved",
        )

    @app.post("/settings/updates/download")
    async def download_update(request: Request) -> RedirectResponse:
        """下載並驗證更新包；不支援自動套用的平台只保留下載結果。"""

        update_context = _build_settings_update_context(request)
        if not update_context.update_capability.download_supported:
            return redirect_settings_with_error(
                update_context.update_capability.unsupported_reason
            )
        outcome = await download_verified_update(
            current_version=update_context.metadata.app_version,
            paths=update_context.paths,
            update_capability=update_context.update_capability,
            allow_env_repository_override=update_context.allow_env_repository_override,
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

        update_context = _build_settings_update_context(request)
        if not update_context.update_capability.apply_supported:
            return redirect_settings_with_error(
                update_context.update_capability.unsupported_reason
            )
        outcome = launch_verified_update(
            paths=update_context.paths,
            update_capability=update_context.update_capability,
            launch_updater=launch_temp_updater,
            request_shutdown=lambda: _request_app_shutdown(request),
        )
        if not outcome.ok:
            return redirect_settings_with_error(outcome.message)
        return redirect_settings_with_message(outcome.message)

    @app.post("/settings/updates/download-and-apply")
    async def download_and_apply_update(request: Request) -> JSONResponse:
        """下載、驗證並啟動 updater；供 settings 頁 modal 流程使用。"""

        update_context = _build_settings_update_context(request)
        if not update_context.update_capability.apply_supported:
            return _update_json_error(
                update_context.update_capability.unsupported_reason,
                stage="environment",
            )
        outcome = await download_and_launch_verified_update(
            current_version=update_context.metadata.app_version,
            paths=update_context.paths,
            update_capability=update_context.update_capability,
            allow_env_repository_override=update_context.allow_env_repository_override,
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

    @app.post("/settings/notifications/clear-failed")
    async def clear_failed_notification_outbox_route(request: Request) -> RedirectResponse:
        """手動清除 failed notification outbox rows，不影響 pending rows。"""

        try:
            cleared_count = await _clear_failed_notifications_for_settings(request)
        except Exception as exc:
            return redirect_settings_with_error(
                "清除失敗通知失敗："
                + format_notification_event_message(
                    safe_exception_message("notification_clear_failed", exc)
                )
            )
        return redirect_settings_with_message(
            f"已清除失敗通知 {cleared_count} 筆",
            feedback="notification_clear_failed_finished",
        )

    @app.post("/settings/support-bundle")
    async def download_support_bundle(request: Request) -> object:
        """建立並下載 redacted support bundle。"""

        try:
            result = await _create_support_bundle_for_settings(request)
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
            await _open_facebook_profile_for_settings(request)
        except ProfileSessionError as exc:
            return redirect_settings_with_error(format_failure_message_text(str(exc)))
        return redirect_settings_with_message("Facebook 設定視窗已開啟")

    @app.post("/settings/facebook/close")
    async def close_facebook_profile(request: Request) -> RedirectResponse:
        """關閉 Facebook automation profile 設定視窗。"""

        await _close_facebook_profile_for_settings(request)
        return redirect_settings_with_message("Facebook 設定視窗已關閉")


def _build_settings_update_context(request: Request) -> _SettingsUpdateContext:
    """收集 settings 頁所有更新流程共用的 build、path 與 capability。"""

    metadata = collect_build_metadata(asset_version=ASSET_VERSION)
    paths = get_runtime_paths(request)
    return _SettingsUpdateContext(
        metadata=metadata,
        paths=paths,
        update_capability=_resolve_update_capability(
            packaging_mode=metadata.packaging_mode,
            frozen=metadata.frozen,
            app_base_dir=paths.app_base_dir,
            data_dir=paths.data_dir,
            db_path=paths.db_path,
        ),
    )


async def _load_settings_update_check(
    request: Request,
    update_context: _SettingsUpdateContext,
) -> UpdateCheckResult:
    """依 query 決定 settings 頁只顯示 idle 狀態或實際檢查 GitHub release。"""

    update_check = build_idle_update_check(
        current_version=update_context.metadata.app_version,
        channel="stable",
        allow_env_repository_override=update_context.allow_env_repository_override,
    )
    if request.query_params.get("update_check") != "1":
        return update_check
    try:
        with acquire_update_operation_lock(update_context.paths.runtime_dir, "settings-check"):
            return await check_github_release_updates(
                current_version=update_context.metadata.app_version,
                channel="stable",
                allow_env_repository_override=update_context.allow_env_repository_override,
            )
    except UpdateOperationLockError:
        return update_check


async def _save_app_theme(request: Request, theme: str) -> str:
    """保存 settings 頁 theme preference 並回傳實際寫入值。"""

    return await run_web_app_context_operation(
        request,
        lambda app_context: app_context.repositories.app_settings.save_theme(theme),
        operation_name="settings.save_theme",
    )


def _parse_target_keyword_defaults(
    *,
    exclude_keywords: str,
    exclude_ignore_phrases: str,
) -> TargetKeywordDefaultSettings:
    """驗證並建立新增 target 時套用的關鍵字預設設定。"""

    parse_limited_keywords_text(exclude_keywords, field_label="排除關鍵字預設")
    parse_limited_keywords_text(
        exclude_ignore_phrases,
        field_label="排除字忽略片語預設",
    )
    return TargetKeywordDefaultSettings(
        exclude_keywords_text=exclude_keywords,
        exclude_ignore_phrases_text=exclude_ignore_phrases,
    )


async def _save_target_keyword_defaults(
    request: Request,
    settings: TargetKeywordDefaultSettings,
) -> None:
    """保存 settings 頁 target keyword defaults。"""

    await run_web_app_context_operation(
        request,
        lambda app_context: app_context.repositories.app_settings.save_target_keyword_defaults(
            settings
        ),
        operation_name="settings.save_target_keyword_defaults",
    )


async def _clear_failed_notifications_for_settings(request: Request) -> int:
    """清除 settings 頁允許的 failed notification outbox rows。"""

    db_path = get_db_path(request)
    return await run_web_db_operation(
        lambda: clear_failed_notifications(db_path=db_path),
        operation_name="settings.clear_failed_notifications",
    )


async def _create_support_bundle_for_settings(request: Request) -> SupportBundleResult:
    """建立 settings 頁下載用的 redacted support bundle。"""

    paths = get_runtime_paths(request)
    metadata = collect_build_metadata(asset_version=ASSET_VERSION)
    diagnostics = build_runtime_diagnostics_view(request.app.state)
    return await run_in_threadpool(
        create_support_bundle,
        paths=paths,
        runtime_diagnostics_text=diagnostics.copy_text,
        app_metadata={
            "app_version": metadata.app_version,
            "asset_version": metadata.asset_version,
            "packaging_mode": metadata.packaging_mode,
            "python_version": metadata.python_version,
        },
        scheduler_state=_support_bundle_scheduler_state(request.app.state),
    )


def _support_bundle_scheduler_state(app_state: object) -> dict[str, object]:
    """整理 support bundle 使用的 scheduler state，不觸發任何啟停動作。"""

    scheduler_manager = getattr(app_state, "scheduler_manager", None)
    if scheduler_manager is None:
        return {}
    try:
        state = scheduler_manager.state()
    except Exception:
        return {}
    lifecycle_state = getattr(state, "lifecycle_state", "")
    return {
        "running": bool(getattr(state, "running", False)),
        "interval_seconds": getattr(state, "interval_seconds", 0),
        "lifecycle_state": getattr(lifecycle_state, "value", str(lifecycle_state)),
        "last_cycle_at": getattr(state, "last_cycle_at", ""),
        "last_error": getattr(state, "last_error", ""),
        "max_concurrent_scans": getattr(state, "max_concurrent_scans", 0),
        "current_running_count": getattr(state, "current_running_count", 0),
        "current_queued_count": getattr(state, "current_queued_count", 0),
        "queue_length": getattr(state, "queue_length", 0),
        "queued_target_ids": tuple(getattr(state, "queued_target_ids", ())),
        "worker_ids": tuple(getattr(state, "worker_ids", ())),
        "page_pool_size": getattr(state, "page_pool_size", 0),
        "last_opened_page_count": getattr(state, "last_opened_page_count", 0),
        "last_reused_page_count": getattr(state, "last_reused_page_count", 0),
        "last_closed_page_count": getattr(state, "last_closed_page_count", 0),
        "resident_browser_alive": bool(getattr(state, "resident_browser_alive", False)),
        "recovered_runtime_count": getattr(state, "recovered_runtime_count", 0),
        "notification_dispatch_count": getattr(state, "notification_dispatch_count", 0),
        "worker_health_ok": bool(getattr(state, "worker_health_ok", True)),
    }


async def _open_facebook_profile_for_settings(request: Request) -> None:
    """暫停 scheduler 後開啟 settings 頁管理的 Facebook profile 視窗。"""

    pause_scheduler_for_profile_use(request)
    try:
        await run_in_threadpool(
            get_profile_manager(request).open,
            open_profile_options(request),
        )
    except Exception:
        resume_scheduler_after_profile_use(request)
        raise


async def _close_facebook_profile_for_settings(request: Request) -> None:
    """關閉 settings 頁管理的 Facebook profile 視窗並恢復 scheduler。"""

    await run_in_threadpool(get_profile_manager(request).close)
    resume_scheduler_after_profile_use(request)


def _resolve_update_capability(
    *,
    packaging_mode: str,
    frozen: bool,
    app_base_dir: object,
    data_dir: object | None = None,
    db_path: object | None = None,
) -> UpdateCapability:
    """依 settings 測試替換後的平台判斷委派 updates capability。"""

    return resolve_update_capability(
        packaging_mode=packaging_mode,
        frozen=frozen,
        app_base_dir=app_base_dir,
        data_dir=data_dir,
        db_path=db_path,
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
