"""Dashboard read model 警告文案組裝。"""

from __future__ import annotations

from facebook_monitor.persistence.invariants import DatabaseInvariantViolation
from facebook_monitor.webapp.dashboard_read_models import DatabaseInvariantWarning
from facebook_monitor.webapp.dashboard_read_models import ProfileSessionWarning


def build_profile_session_warning(
    status: object,
) -> ProfileSessionWarning:
    """將 repository 狀態轉成首頁顯示用警告文案。"""

    if not getattr(status, "needs_login", False):
        return ProfileSessionWarning()
    return ProfileSessionWarning(
        needs_login=True,
        reason=str(getattr(status, "reason", "")),
        message=(
            "Facebook 需要重新登入。請關閉並重新開啟程式，"
            "系統會先開啟 Facebook 登入視窗；完成登入後會自動進入 Web UI。"
        ),
    )


def build_database_invariant_warning(
    violations: tuple[DatabaseInvariantViolation, ...],
) -> DatabaseInvariantWarning:
    """將 DB invariant 結果轉成首頁警告，不洩漏 row id。"""

    if not violations:
        return DatabaseInvariantWarning()
    tables = tuple(sorted({violation.table for violation in violations}))
    table_summary = "、".join(tables[:3])
    extra = f"（{table_summary}）" if table_summary else ""
    return DatabaseInvariantWarning(
        has_violations=True,
        violation_count=len(violations),
        tables=tables,
        message=(
            f"資料庫偵測到 {len(violations)} 個資料 invariant 異常{extra}。"
            "請到設定下載支援包或執行資料檢查工具；系統不會自動修復資料。"
        ),
    )
