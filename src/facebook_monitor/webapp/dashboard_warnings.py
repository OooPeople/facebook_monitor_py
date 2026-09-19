"""Dashboard read model 警告文案組裝。"""

from __future__ import annotations

from datetime import datetime

from facebook_monitor.application.facebook_access_observability import (
    circuit_requires_global_pause,
)
from facebook_monitor.application.facebook_access_observability import (
    FacebookAccessSafeSnapshot,
)
from facebook_monitor.core.facebook_access import FACEBOOK_ACCESS_PERSISTENCE_UNCERTAIN_REASON
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.persistence.invariants import DatabaseInvariantViolation
from facebook_monitor.webapp.dashboard_read_models import DatabaseInvariantWarning
from facebook_monitor.webapp.dashboard_read_models import FacebookAccessCircuitBanner
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
            f"資料庫偵測到 {len(violations)} 個資料 invariant 異常{extra}。"
            "請到設定下載支援包或執行資料檢查工具；系統不會自動修復資料。"
        ),
    )


def build_facebook_access_circuit_banner(
    snapshot: FacebookAccessSafeSnapshot,
) -> FacebookAccessCircuitBanner:
    """將 profile circuit 安全摘要轉成非 target 專屬的全域 banner。"""

    if not circuit_requires_global_pause(snapshot):
        return FacebookAccessCircuitBanner()
    if snapshot.state == "unclean_session_hold":
        last_probe_result_label = _last_probe_result_label(snapshot.last_probe_result)
        return FacebookAccessCircuitBanner(
            visible=True,
            title="上次 Facebook 自動化工作未正常結束",
            message=(
                "系統發現前一次受管 profile 的自動化 session 未乾淨關閉，"
                "已在開啟 Facebook 前安全暫停。這不代表已偵測到 Facebook 封鎖。"
            ),
            profile_scope=snapshot.profile_scope,
            state=snapshot.state,
            reason=snapshot.reason,
            cooldown_active=snapshot.cooldown_active,
            cooldown_until=snapshot.cooldown_until,
            probe_pending=snapshot.probe_pending,
            last_probe_result=snapshot.last_probe_result,
            last_probe_result_label=last_probe_result_label,
            recovery_enabled=snapshot.recovery_available,
            recovery_disabled_reason=snapshot.recovery_disabled_reason,
            recovery_status_message=_session_recovery_status_message(snapshot),
        )
    if snapshot.state == "storage_critical":
        return FacebookAccessCircuitBanner(
            visible=True,
            title="Facebook 自動化安全儲存狀態異常",
            message=(
                "系統無法安全確認受管 profile 的自動化 session 狀態，"
                "已在開啟 Facebook 前停止自動存取。"
            ),
            profile_scope=snapshot.profile_scope,
            state=snapshot.state,
            reason=snapshot.reason,
            recovery_disabled_reason=snapshot.recovery_disabled_reason,
            recovery_status_message=(
                "儲存狀態異常時不能執行恢復檢查，請先檢查 runtime diagnostics。"
            ),
        )
    state_label = "安全暫停" if snapshot.state == "open" else "單次恢復檢查中"
    reason_label = {
        FACEBOOK_TEMPORARY_BLOCK_REASON: "Facebook 暫時限制存取",
        FACEBOOK_ACCESS_PERSISTENCE_UNCERTAIN_REASON: "Facebook 存取狀態尚未確認",
        "unrecognized_code": "未識別的安全暫停原因",
    }.get(snapshot.reason, "安全保護已啟用")
    cooldown_message = _cooldown_message(snapshot)
    last_probe_result_label = _last_probe_result_label(snapshot.last_probe_result)
    return FacebookAccessCircuitBanner(
        visible=True,
        title="Facebook 自動存取已安全暫停",
        message=(
            f"目前受管 profile：{state_label}；原因：{reason_label}。"
            f"{cooldown_message} 系統會保持自動存取停止，請勿反覆重新整理 Facebook。"
        ),
        profile_scope=snapshot.profile_scope,
        state=snapshot.state,
        reason=snapshot.reason,
        cooldown_active=snapshot.cooldown_active,
        cooldown_until=snapshot.cooldown_until,
        probe_pending=snapshot.probe_pending,
        last_probe_result=snapshot.last_probe_result,
        last_probe_result_label=last_probe_result_label,
        recovery_enabled=snapshot.recovery_available,
        recovery_disabled_reason=snapshot.recovery_disabled_reason,
        recovery_status_message=_recovery_status_message(snapshot),
    )


def _cooldown_message(snapshot: FacebookAccessSafeSnapshot) -> str:
    """格式化 circuit cooldown，不依賴 client clock。"""

    if not snapshot.cooldown_until:
        return "目前沒有可自動恢復的時間"
    try:
        cooldown_until = datetime.fromisoformat(snapshot.cooldown_until)
    except ValueError:
        return "最早可檢查時間暫時無法顯示"
    formatted = format_datetime_for_ui(cooldown_until)
    if snapshot.cooldown_active:
        return f"最早可檢查時間：{formatted}"
    return f"冷卻時間已於 {formatted} 結束，仍需安全恢復流程確認"


def _last_probe_result_label(result: str) -> str:
    """將 last probe result stable enum 轉成使用者可讀摘要。"""

    return {
        "success": "最近一次恢復檢查：成功",
        "blocked": "最近一次恢復檢查：仍受到 Facebook 限制",
        "inconclusive": "最近一次恢復檢查：結果無法確認",
        "cancelled": "最近一次恢復檢查：已取消",
    }.get(result, "")


def _recovery_status_message(snapshot: FacebookAccessSafeSnapshot) -> str:
    """依 readiness 顯示安全且可行動的 manual recovery 說明。"""

    if snapshot.recovery_available:
        return "冷卻時間已結束，可執行一次安全恢復檢查。"
    return {
        "probe_in_progress": "正在執行一次安全恢復檢查；其他自動存取仍保持暫停。",
        "probe_pending": "安全恢復檢查已排程，正在等待 scheduler 執行。",
        "cooldown_active": "冷卻時間尚未結束，最早可檢查時間後才可執行。",
        "comments_recovery_recipe_unavailable": (
            "留言監視尚未有核准的安全恢復流程，目前不能執行恢復檢查。"
        ),
        "recovery_recipe_unavailable": "目前沒有適用的安全恢復流程。",
        "trigger_target_unavailable": (
            "原觸發 target 已停用、暫停或不存在，無法作為安全檢查對象。"
        ),
        "unclean_session_healthcheck_unavailable": (
            "目前尚未有核准的非預期中斷健康檢查流程。"
        ),
        "storage_critical": "儲存狀態異常時不能執行恢復檢查。",
        "circuit_state_not_open": "目前狀態不能提出新的恢復檢查。",
    }.get(snapshot.recovery_disabled_reason, "恢復檢查目前不可用。")


def _session_recovery_status_message(snapshot: FacebookAccessSafeSnapshot) -> str:
    """顯示 stale-session quiet gap 與一次性 healthcheck 狀態。"""

    if snapshot.recovery_available:
        return "安靜期已結束，請選擇一個 active target 執行一次健康檢查。"
    return {
        "probe_pending": "非預期中斷健康檢查已排程。",
        "probe_in_progress": "正在執行一次非預期中斷健康檢查。",
        "unclean_session_quiet_period": "安靜期尚未結束，期間不會開啟 Facebook。",
        "unclean_session_target_unavailable": "目前沒有可用的 active target 作為健康檢查對象。",
        "unclean_session_healthcheck_unavailable": "非預期中斷健康檢查目前不可用。",
    }.get(snapshot.recovery_disabled_reason, "非預期中斷健康檢查目前不可用。")
