"""Settings page read model helpers。

職責：集中 settings template context 的 read-side 組裝，讓 route 保留 query、
form 與 redirect / JSON response wiring。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from facebook_monitor.application.notification_admin import load_notification_outbox_health
from facebook_monitor.updates.pending_update_models import pending_update_path
from facebook_monitor.updates.release_check import UpdateCheckResult
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_profile_dir
from facebook_monitor.webapp.dependencies import load_app_theme
from facebook_monitor.webapp.dependencies import load_target_keyword_defaults
from facebook_monitor.webapp.dependencies import run_web_read_operation


async def build_settings_template_context(
    request: Request,
    update_context: Any,
    update_check: UpdateCheckResult,
    *,
    message: str,
    feedback: str,
    error: str,
) -> dict[str, object]:
    """組出 settings template 既有 context，集中頁面 read model 來源。"""

    db_path = get_db_path(request)
    target_keyword_defaults = await load_target_keyword_defaults(request)
    notification_outbox_health = await run_web_read_operation(
        lambda: load_notification_outbox_health(db_path),
        operation_name="settings.notification_outbox_health",
    )
    initial_theme = await load_app_theme(request)
    return {
        "message": message,
        "feedback": feedback,
        "error": error,
        "profile_dir": str(get_profile_dir(request)),
        "target_keyword_defaults": target_keyword_defaults,
        "notification_outbox_health": notification_outbox_health,
        "update_check": update_check,
        "update_download_supported": update_context.update_capability.download_supported,
        "update_apply_supported": update_context.update_capability.apply_supported,
        "update_unsupported_reason": update_context.update_capability.unsupported_reason,
        "pending_update_available": pending_update_path(
            update_context.paths.runtime_dir
        ).is_file(),
        "initial_theme": initial_theme,
    }
