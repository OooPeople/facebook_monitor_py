"""Settings Web use cases。

職責：承接 settings route 內需要協調 app state、threadpool 與 Web 依賴的
操作，讓 route handler 專注 HTTP redirect / response。
"""

from __future__ import annotations

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from facebook_monitor.application.notification_admin import clear_failed_notifications
from facebook_monitor.core.input_limits import parse_limited_keywords_text
from facebook_monitor.diagnostics.support_bundle import create_support_bundle
from facebook_monitor.diagnostics.support_bundle import SupportBundleResult
from facebook_monitor.persistence.repositories.app_settings import TargetKeywordDefaultSettings
from facebook_monitor.runtime.build_metadata import collect_build_metadata
from facebook_monitor.webapp.assets import ASSET_VERSION
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_profile_manager
from facebook_monitor.webapp.dependencies import get_runtime_paths
from facebook_monitor.webapp.dependencies import open_profile_options
from facebook_monitor.webapp.dependencies import pause_scheduler_for_profile_use
from facebook_monitor.webapp.dependencies import resume_scheduler_after_profile_use
from facebook_monitor.webapp.dependencies import run_web_app_context_operation
from facebook_monitor.webapp.dependencies import run_web_db_operation
from facebook_monitor.webapp.runtime_diagnostics import build_runtime_diagnostics_view


async def save_theme_preference_for_settings(request: Request, theme: str) -> str:
    """保存 settings 頁 theme preference 並回傳實際寫入值。"""

    return await run_web_app_context_operation(
        request,
        lambda app_context: app_context.repositories.app_settings.save_theme(theme),
        operation_name="settings.save_theme",
    )


def parse_target_keyword_defaults_for_settings(
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


async def save_target_keyword_defaults_for_settings(
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


async def clear_failed_notifications_for_settings(request: Request) -> int:
    """清除 settings 頁允許的 failed notification outbox rows。"""

    db_path = get_db_path(request)
    return await run_web_db_operation(
        lambda: clear_failed_notifications(db_path=db_path),
        operation_name="settings.clear_failed_notifications",
    )


async def create_support_bundle_for_settings(request: Request) -> SupportBundleResult:
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
        scheduler_state=support_bundle_scheduler_state(request.app.state),
    )


def support_bundle_scheduler_state(app_state: object) -> dict[str, object]:
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


async def open_facebook_profile_for_settings(request: Request) -> None:
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


async def close_facebook_profile_for_settings(request: Request) -> None:
    """關閉 settings 頁管理的 Facebook profile 視窗並恢復 scheduler。"""

    await run_in_threadpool(get_profile_manager(request).close)
    resume_scheduler_after_profile_use(request)
