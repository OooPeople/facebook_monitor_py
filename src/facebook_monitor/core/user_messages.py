"""使用者可見訊息格式化規則。

職責：把 worker、通知與 runtime 的內部 reason / status / exception 摘要
轉成繁體中文文案，避免 Web UI 直接顯示第三方套件的英文例外。
"""

from __future__ import annotations

import re

from facebook_monitor.core.redaction import redact_sensitive_text
from facebook_monitor.core.scan_failures import CHECKPOINT_REQUIRED_REASON
from facebook_monitor.core.scan_failures import CONTENT_UNAVAILABLE_REASON
from facebook_monitor.core.scan_failures import EXTRACTOR_EMPTY_REASON
from facebook_monitor.core.scan_failures import LOGIN_REQUIRED_REASON
from facebook_monitor.core.scan_failures import PAGE_LOAD_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import PROFILE_LOCKED_REASON
from facebook_monitor.core.scan_failures import PROFILE_MISSING_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import SCAN_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import SESSION_INVALID_REASON
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.core.scan_failures import STALE_RUNNING_REASON
from facebook_monitor.core.scan_failures import TARGET_ARGUMENT_CONFLICT_REASON
from facebook_monitor.core.scan_failures import TARGET_INVALID_REASON
from facebook_monitor.core.scan_failures import TARGET_KIND_UNSUPPORTED_REASON
from facebook_monitor.core.scan_failures import TARGET_MISSING_REASON
from facebook_monitor.core.scan_failures import TARGET_STOPPED_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON


_CODED_MESSAGE_RE = re.compile(r"^([a-z][a-z0-9_]*)(?::\s*(.*))?$")
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_FAILURE_REASON_LABELS = {
    CONTENT_UNAVAILABLE_REASON: "連結已失效",
    LOGIN_REQUIRED_REASON: "需要重新登入",
    CHECKPOINT_REQUIRED_REASON: "需要完成 Facebook 驗證",
    SESSION_INVALID_REASON: "Facebook 工作階段失效",
    PROFILE_LOCKED_REASON: "瀏覽器設定檔使用中",
    PROFILE_MISSING_REASON: "瀏覽器設定檔不存在",
    SCHEDULER_RUNTIME_REASON: "背景掃描執行錯誤",
    EXTRACTOR_EMPTY_REASON: "未抽取到可用項目",
    SCAN_TIMEOUT_REASON: "掃描逾時",
    PAGE_LOAD_TIMEOUT_REASON: "頁面載入逾時",
    SORT_ADJUST_UNCONFIRMED_REASON: "調整排序失敗",
    "scheduler_stopping": "背景掃描正在停止",
    TARGET_STOPPED_REASON: "監視項目已停止",
    TARGET_MISSING_REASON: "找不到監視項目",
    TARGET_INVALID_REASON: "監視項目設定無效",
    TARGET_KIND_UNSUPPORTED_REASON: "監視項目類型不支援",
    TARGET_ARGUMENT_CONFLICT_REASON: "監視項目參數衝突",
    UNKNOWN_REASON: "未分類錯誤",
    STALE_RUNNING_REASON: "掃描狀態逾時",
    "stale_queued_recovered": "排隊狀態已回復",
}

_FAILURE_REASON_DETAILS = {
    CONTENT_UNAVAILABLE_REASON: "Facebook 顯示目前無法查看此內容，可能已刪除或權限變更。",
    LOGIN_REQUIRED_REASON: "Facebook 要求重新登入，請到設定頁開啟登入視窗完成登入。",
    CHECKPOINT_REQUIRED_REASON: "Facebook 要求完成身分或安全性驗證，請到設定頁開啟登入視窗處理。",
    SESSION_INVALID_REASON: "Facebook 工作階段已失效，請重新登入後再掃描。",
    PROFILE_LOCKED_REASON: "瀏覽器設定檔目前被其他視窗或程序使用中，請關閉其他自動化瀏覽器或登入視窗後再試。",
    PROFILE_MISSING_REASON: "找不到自動化瀏覽器設定檔，請先到設定頁開啟 Facebook 登入視窗。",
    SCHEDULER_RUNTIME_REASON: "背景掃描程序或瀏覽器 context 發生錯誤，系統會重啟執行環境並重試。",
    EXTRACTOR_EMPTY_REASON: "頁面載入完成但沒有抽取到可辨識的貼文或留言，系統會重啟頁面並重試。",
    SCAN_TIMEOUT_REASON: "本輪掃描超過設定時間，系統已中止本輪並會重啟頁面重試。",
    PAGE_LOAD_TIMEOUT_REASON: "頁面載入、重新導向或重新整理時中斷，掃描中的頁面內容已失效；請稍後重試。",
    SORT_ADJUST_UNCONFIRMED_REASON: "連續多輪未能確認 Facebook 排序已切到最新，系統會重啟頁面並重試。",
    "scheduler_stopping": "背景掃描服務正在停止，本輪掃描已取消。",
    TARGET_STOPPED_REASON: "監視項目在掃描期間被停止，本輪掃描已取消。",
    TARGET_MISSING_REASON: "掃描前找不到這個監視項目，可能已被刪除。",
    TARGET_INVALID_REASON: "監視項目設定不完整或與目前頁面不一致，請重新確認設定。",
    TARGET_KIND_UNSUPPORTED_REASON: "目前背景掃描不支援這個監視項目類型。",
    TARGET_ARGUMENT_CONFLICT_REASON: "監視項目參數互相衝突，請只指定一種監視項目。",
    UNKNOWN_REASON: "發生未分類錯誤，請查看 log 或稍後重試。",
    STALE_RUNNING_REASON: "背景掃描心跳已逾時，系統已記錄本輪失敗並會重啟頁面重試。",
    "stale_queued_recovered": "監視項目排隊等待過久，系統已將它回復為可再次排程。",
}

_RUNTIME_SKIP_MESSAGES = {
    "scan_guard_skipped": "掃描已略過",
    "target_already_running": "監視項目已在掃描中",
    "stale_queued_recovered": "監視項目排隊等待過久，系統已回復排程狀態。",
}

_NOTIFICATION_STATUS_LABELS = {
    "sent": "已送出",
    "failed": "失敗",
    "skipped": "已略過",
}

_NOTIFICATION_MESSAGE_LABELS = {
    "sent": "已送出",
    "retry_sent": "重試後已送出",
    "desktop_sent": "桌面通知已送出",
    "ntfy_sent": "ntfy 通知已送出",
    "discord_sent": "Discord 通知已送出",
    "desktop_skipped": "桌面通知已略過",
    "ntfy_skipped": "ntfy 主題未設定，已略過",
    "discord_skipped": "Discord webhook 未設定，已略過",
    "desktop_failed": "桌面通知發送失敗",
    "ntfy_failed": "ntfy 發送失敗",
    "discord_failed": "Discord 發送失敗",
    "ntfy topic is empty": "ntfy 主題未設定",
    "discord_webhook_invalid": "Discord webhook URL 格式不正確",
    "desktop_failed: unsupported platform": "目前平台不支援桌面通知",
    "failed_result": "通知服務回傳失敗",
    "previous_down": "前次通知失敗",
    "first_down": "首次通知失敗",
    "notification_skipped: no channel enabled": "未啟用任何通知通道",
}

_UPDATE_REASON_LABELS = {
    "not_checked": "尚未檢查更新",
    "current": "目前已是最新版本",
    "available": "有可用更新",
    "asset_missing": "缺少符合目前平台的更新檔",
    "asset_version_mismatch": "更新檔版本與 release tag 不一致",
    "platform_unsupported": "目前平台沒有對應的更新檔",
    "sha256_asset_missing": "缺少 SHA256 驗證檔",
    "sha256_asset_url_missing": "缺少 SHA256 驗證檔下載網址",
    "sha256_mismatch": "更新檔 SHA256 驗證失敗",
    "sha256_sidecar_manifest_mismatch": "SHA256 驗證檔與 signed manifest 不一致",
    "manifest_file_missing": "缺少 signed release manifest",
    "manifest_asset_url_missing": "缺少 signed release manifest 下載網址",
    "manifest_signature_asset_missing": "缺少 release manifest 簽章",
    "manifest_signature_asset_url_missing": "缺少 release manifest 簽章下載網址",
    "manifest_signature_invalid": "release manifest 簽章無效",
    "manifest_key_untrusted": "release manifest 簽章 key 不受信任",
    "manifest_version_mismatch": "release manifest 版本不一致",
    "manifest_repository_mismatch": "release manifest repository 不一致",
    "manifest_asset_missing": "release manifest 缺少目前平台更新檔",
    "manifest_asset_sha256_invalid": "release manifest 內的更新檔 hash 無效",
    "manifest_asset_size_mismatch": "release manifest 內的更新檔大小不一致",
    "update_not_available": "目前沒有可下載的更新",
    "asset_download_url_missing": "缺少更新檔下載網址",
    "release_download_url_must_be_https": "更新檔下載網址必須使用 HTTPS",
    "release_download_url_host_not_allowed": "更新檔下載來源不在 GitHub allowlist",
    "release_download_url_repository_mismatch": "更新檔下載 repository 與設定不一致",
    "release_download_url_asset_mismatch": "更新檔下載檔名與 release metadata 不一致",
    "invalid_asset_name": "更新檔名不安全",
    "download_path_unsafe": "更新檔下載路徑不安全",
    "download_too_large": "更新檔超過大小限制",
    "download_http_404": "更新檔下載位址不存在",
    "updater_missing": "找不到內建更新器",
    "pending_update_missing": "找不到更新交接檔",
    "launch_failed": "無法啟動更新器",
    "launched": "更新器已啟動",
    "app launched": "新版程式已啟動",
}


def split_coded_message(value: str) -> tuple[str, str]:
    """拆出 `reason: detail` 形式的內部訊息。"""

    text = str(value or "").strip()
    match = _CODED_MESSAGE_RE.match(text)
    if not match:
        return "", text
    code = match.group(1)
    detail = match.group(2) or ""
    if code in _FAILURE_REASON_LABELS or code in _RUNTIME_SKIP_MESSAGES:
        return code, detail
    return "", text


def format_failure_reason(reason: str) -> str:
    """把 failed scan reason 轉成 UI 可讀文字。"""

    return _FAILURE_REASON_LABELS.get(str(reason or ""), str(reason or "") or "(未知)")


def format_failure_message(reason: str, detail: str = "") -> str:
    """把失敗 reason 與原始 detail 轉成使用者可見中文訊息。"""

    code = str(reason or "").strip()
    label = format_failure_reason(code)
    resolved_detail = _localized_failure_detail(code, detail)
    if not resolved_detail:
        return label
    if resolved_detail == label:
        return label
    return f"{label}：{resolved_detail}"


def format_failure_retry_exhausted_message(
    reason: str,
    *,
    retry_streak: int,
    retry_limit: int,
) -> str:
    """建立連續可重試失敗達上限後的使用者可見訊息。"""

    label = format_failure_reason(reason)
    limit = max(retry_limit, retry_streak, 1)
    return f"{label}：已連續 {limit} 次失敗，系統已停止此監視項目。"


def format_failure_message_text(value: str) -> str:
    """格式化既有 `reason: detail` 或第三方 raw exception 訊息。"""

    code, detail = split_coded_message(value)
    if code:
        return format_failure_message(code, detail)
    text = str(value or "").strip()
    localized = _localized_exception_text(text)
    if localized:
        return localized
    if _looks_like_raw_english(text):
        return _FAILURE_REASON_DETAILS[UNKNOWN_REASON]
    return redact_sensitive_text(text)


def format_runtime_skip_message(value: str) -> str:
    """把 runtime skip reason 轉成 UI 可讀文字。"""

    text = str(value or "").strip()
    if not text:
        return ""
    code, detail = split_coded_message(text)
    if code == "stale_queued_recovered":
        return _RUNTIME_SKIP_MESSAGES[code]
    if code == "scan_guard_skipped":
        if "target_already_running" in detail:
            return "監視項目已在掃描中，本輪排程已略過。"
        return "掃描 guard 已略過本輪排程。"
    if text in _RUNTIME_SKIP_MESSAGES:
        return _RUNTIME_SKIP_MESSAGES[text]
    if _looks_like_raw_english(text):
        return "本輪掃描已略過。"
    return redact_sensitive_text(text)


def format_notification_status_label(status: str) -> str:
    """把 notification status enum value 轉成中文。"""

    return _NOTIFICATION_STATUS_LABELS.get(str(status or ""), str(status or ""))


def format_notification_event_message(value: str) -> str:
    """把 notification event message 轉成中文摘要。"""

    text = str(value or "").strip()
    if not text:
        return ""
    if text in _NOTIFICATION_MESSAGE_LABELS:
        return _NOTIFICATION_MESSAGE_LABELS[text]
    if text.startswith("unexpected status code:"):
        status_code = text.removeprefix("unexpected status code:").strip()
        return f"通知服務回傳非預期狀態碼 {status_code}"
    if text.startswith("ntfy_failed:"):
        return "ntfy 發送失敗"
    if text.startswith("discord_failed:429"):
        return "Discord 發送受限，稍後可重試"
    if text.startswith("discord_failed:"):
        status_code = text.removeprefix("discord_failed:").split(maxsplit=1)[0]
        return f"Discord 發送失敗，狀態碼 {status_code}" if status_code else "Discord 發送失敗"
    if text.startswith("desktop_failed:"):
        return "桌面通知發送失敗"
    if text.startswith("notification_test_failed:"):
        return "通知測試發生錯誤"
    if text.endswith("_dispatch_failed:RuntimeError") or "_dispatch_failed:" in text:
        return "通知發送失敗"
    if _looks_like_raw_english(text):
        return "通知處理失敗"
    return redact_sensitive_text(text)


def format_update_reason_message(value: str) -> str:
    """把 updater / release check reason 轉成中文摘要。"""

    text = str(value or "").strip()
    if not text:
        return ""
    if text in _UPDATE_REASON_LABELS:
        return _UPDATE_REASON_LABELS[text]
    if text.startswith("network_error:"):
        return "無法連線到更新服務"
    if text.startswith("download_error:"):
        return "下載更新時連線失敗"
    if text.startswith("download_io_error:"):
        return "寫入更新檔失敗"
    if text.startswith("http_"):
        status_code = text.removeprefix("http_")
        return f"更新服務回傳狀態碼 {status_code}"
    if text.startswith("download_http_"):
        status_code = text.removeprefix("download_http_")
        return f"下載更新檔失敗，狀態碼 {status_code}"
    if _looks_like_raw_english(text):
        return "更新流程失敗，請查看 log 或稍後重試。"
    return redact_sensitive_text(text)


def _localized_failure_detail(reason: str, detail: str) -> str:
    """依 reason 優先給穩定中文說明，避免 raw exception 外漏。"""

    if reason in _FAILURE_REASON_DETAILS:
        return _FAILURE_REASON_DETAILS[reason]
    localized = _localized_exception_text(detail)
    if localized:
        return localized
    text = str(detail or "").strip()
    if _looks_like_raw_english(text):
        return _FAILURE_REASON_DETAILS[UNKNOWN_REASON]
    return redact_sensitive_text(text)


def _localized_exception_text(value: str) -> str:
    """辨識常見第三方 raw exception，回傳中文摘要。"""

    text = str(value or "").strip()
    lower = text.lower()
    if not text:
        return ""
    if (
        "execution context was destroyed" in lower
        or "most likely because of a navigation" in lower
    ):
        return _FAILURE_REASON_DETAILS[PAGE_LOAD_TIMEOUT_REASON]
    if "target page, context or browser has been closed" in lower:
        return "瀏覽器頁面或 context 已關閉，本輪掃描無法繼續。"
    if "user data directory is already in use" in lower or "processsingleton" in lower:
        return _FAILURE_REASON_DETAILS[PROFILE_LOCKED_REASON]
    if "net::" in lower:
        return "頁面網路載入失敗，請稍後重試。"
    if "timeout" in lower:
        return _FAILURE_REASON_DETAILS[PAGE_LOAD_TIMEOUT_REASON]
    return ""


def _looks_like_raw_english(value: str) -> bool:
    """粗略判斷是否為未整理的英文原始訊息。"""

    text = str(value or "").strip()
    return bool(text and _ASCII_LETTER_RE.search(text) and not _CJK_RE.search(text))
