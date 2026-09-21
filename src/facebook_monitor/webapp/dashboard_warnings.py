"""Dashboard read model 警告文案組裝。"""

from __future__ import annotations

from datetime import datetime

from facebook_monitor.core.facebook_temporary_block import (
    TemporaryBlockWarningSnapshot,
)
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.invariants import DatabaseInvariantViolation
from facebook_monitor.webapp.dashboard_read_models import DatabaseInvariantWarning
from facebook_monitor.webapp.dashboard_read_models import (
    FacebookTemporaryBlockWarningBanner,
)
from facebook_monitor.webapp.dashboard_read_models import ProfileSessionWarning
from facebook_monitor.webapp.time_presenters import format_datetime_for_ui


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
            f"目前畫面讀取範圍偵測到 {len(violations)} 個資料 invariant 異常{extra}。"
            "請到設定下載支援包或執行資料檢查工具；系統不會自動修復資料。"
        ),
    )


def build_facebook_temporary_block_warning(
    snapshot: TemporaryBlockWarningSnapshot | None,
    *,
    now: datetime | None = None,
) -> FacebookTemporaryBlockWarningBanner:
    """將有效 singleton warning 轉成 advisory banner 與 Start confirmation state。"""

    observed_at = now or utc_now()
    if snapshot is None or not snapshot.is_active(observed_at):
        return FacebookTemporaryBlockWarningBanner()
    warning_until = snapshot.warning_until.isoformat()
    formatted_until = format_datetime_for_ui(snapshot.warning_until)
    return FacebookTemporaryBlockWarningBanner(
        active=True,
        title="Facebook 暫時限制存取警告",
        message=(
            "系統已停止當下所有 Facebook 工作。警告期間仍可按「開始」並確認風險；"
            "繼續可能無法取得內容，也可能遭到更久封鎖。"
            f"警告顯示至 {formatted_until}。"
        ),
        warning_until=warning_until,
        generation=snapshot.generation,
    )
