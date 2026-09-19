"""Facebook automation durable runtime hold 的安全唯讀投影。

職責：共用 profile identity continuity 與 session sentinel 判斷，僅回傳 bounded
hold code；Web 與 support diagnostics 不直接解析 marker 內容。
"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.application.managed_profile_identity import (
    inspect_managed_profile_identity,
)
from facebook_monitor.runtime.paths import FACEBOOK_AUTOMATION_SESSION_GUARDS_DIR_NAME
from facebook_monitor.worker.facebook_automation_session_guard import (
    derive_facebook_automation_profile_alias,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardError,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardState,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardStore,
)


UNCLEAN_SESSION_HOLD = "unclean_session_hold"
STORAGE_CRITICAL_HOLD = "storage_critical"


def read_facebook_automation_runtime_hold(
    *,
    db_path: Path,
    profile_dir: Path,
    browser_session_active: bool,
) -> str:
    """唯讀判斷 identity/sentinel hold，不回傳 path、UUID 或 session owner。"""

    profile_parent = profile_dir.expanduser().resolve().parent
    data_dir = (
        profile_parent.parent
        if profile_parent.name.casefold() == "profiles"
        else db_path.expanduser().resolve().parent
    )
    inspection = inspect_managed_profile_identity(
        db_path=db_path,
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    if inspection.storage_critical:
        return STORAGE_CRITICAL_HOLD
    identity = inspection.identity
    if identity is None:
        return ""
    store = FacebookAutomationSessionGuardStore(
        data_dir / FACEBOOK_AUTOMATION_SESSION_GUARDS_DIR_NAME,
        profile_alias=derive_facebook_automation_profile_alias(
            identity.profile_scope_key
        ),
    )
    try:
        marker = store.inspect_existing()
    except FacebookAutomationSessionGuardError:
        return STORAGE_CRITICAL_HOLD
    if marker is None:
        return ""
    if (
        marker.state == FacebookAutomationSessionGuardState.NORMAL_SESSION
        and browser_session_active
    ):
        return ""
    if marker.state == FacebookAutomationSessionGuardState.NORMAL_SESSION:
        return UNCLEAN_SESSION_HOLD
    return STORAGE_CRITICAL_HOLD


__all__ = [
    "STORAGE_CRITICAL_HOLD",
    "UNCLEAN_SESSION_HOLD",
    "read_facebook_automation_runtime_hold",
]
