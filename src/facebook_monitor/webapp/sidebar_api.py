"""Sidebar API payload parsing and safe error mapping。

職責：集中 sidebar route 的 JSON payload 解析與安全繁中錯誤訊息，避免 route
同時承擔 HTTP wiring、DB operation 與錯誤文字映射。
"""

from __future__ import annotations

from fastapi import HTTPException

from facebook_monitor.webapp.form_models import format_notification_form_error

_SIDEBAR_SAFE_ECHO_FRAGMENTS = ("不可超過", "最多")
_SIDEBAR_ERROR_RULES: tuple[tuple[tuple[str, ...], str, bool], ...] = (
    (("群組名稱不可空白",), "群組名稱不可空白", True),
    (
        ("找不到指定的 sidebar 群組", "sidebar group not found"),
        "找不到指定的 sidebar 群組",
        True,
    ),
    (("群組內仍有 target",), "群組內仍有 target，請先移出後再刪除", True),
    (("重複群組區塊",), "排序資料不可包含重複群組區塊", True),
    (("重複群組",), "群組排序不可包含重複群組", True),
    (
        ("grouped placement",),
        "已有群組排序狀態，請使用調整順序後的確認保存",
        True,
    ),
    (
        ("sidebar group",),
        "群組排序資料與目前群組不一致，請重新整理後再試",
        False,
    ),
    (("重複 target",), "排序資料不可包含重複 target", True),
    (
        ("所有 target", "剛好包含所有 target"),
        "排序資料與目前 target 清單不一致，請重新整理後再試",
        True,
    ),
    (("id 不可空白",), "排序資料包含空白 id，請重新整理後再試", True),
    (("至少需要選擇",), "至少需要選擇一個套用區段", True),
    (("未知的群組模板套用區段",), "未知的群組模板套用區段", True),
)
_SIDEBAR_DEFAULT_ERROR_DETAIL = "sidebar 資料無法儲存，請重新整理後再試"


def sidebar_error_detail(exc: ValueError) -> str:
    """回傳不含內部路徑、SQL 或 secret 的 sidebar API 錯誤訊息。"""

    message = str(exc)
    notification_error = format_notification_form_error(exc)
    if notification_error != message:
        return notification_error
    if _contains_any(message, _SIDEBAR_SAFE_ECHO_FRAGMENTS):
        return message
    return _sidebar_rule_error_detail(message) or _SIDEBAR_DEFAULT_ERROR_DETAIL


def _sidebar_rule_error_detail(message: str) -> str:
    """依 sidebar error fragment 規則回傳安全錯誤訊息。"""

    lower_message = message.lower()
    for fragments, detail, case_sensitive in _SIDEBAR_ERROR_RULES:
        haystack = message if case_sensitive else lower_message
        if _contains_any(haystack, fragments):
            return detail
    return ""


def _contains_any(message: str, fragments: tuple[str, ...]) -> bool:
    """判斷訊息是否包含任一 fragment。"""

    return any(fragment in message for fragment in fragments)


def string_list(value: object) -> list[str]:
    """將 payload 欄位轉為字串清單。"""

    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="欄位必須是清單")
    return [str(item) for item in value]


def grouped_target_ids(value: object) -> list[tuple[str | None, list[str]]]:
    """解析 grouped placements payload。"""

    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="groups 必須是清單")
    groups: list[tuple[str | None, list[str]]] = []
    for item in value:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="group placement 必須是物件")
        raw_group_id = item.get("group_id")
        group_id = str(raw_group_id).strip() if raw_group_id is not None else None
        if group_id == "":
            group_id = None
        groups.append((group_id, string_list(item.get("target_ids"))))
    return groups
