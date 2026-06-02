"""Sidebar API payload parsing and safe error mapping。

職責：集中 sidebar route 的 JSON payload 解析與安全繁中錯誤訊息，避免 route
同時承擔 HTTP wiring、DB operation 與錯誤文字映射。
"""

from __future__ import annotations

from fastapi import HTTPException

from facebook_monitor.webapp.form_models import format_notification_form_error


def sidebar_error_detail(exc: ValueError) -> str:
    """回傳不含內部路徑、SQL 或 secret 的 sidebar API 錯誤訊息。"""

    message = str(exc)
    notification_error = format_notification_form_error(exc)
    if notification_error != message:
        return notification_error
    if "不可超過" in message or "最多" in message:
        return message
    if "群組名稱不可空白" in message:
        return "群組名稱不可空白"
    if "找不到指定的 sidebar 群組" in message or "sidebar group not found" in message:
        return "找不到指定的 sidebar 群組"
    if "群組內仍有 target" in message:
        return "群組內仍有 target，請先移出後再刪除"
    if "重複群組區塊" in message:
        return "排序資料不可包含重複群組區塊"
    if "重複群組" in message:
        return "群組排序不可包含重複群組"
    if "grouped placement" in message:
        return "已有群組排序狀態，請使用調整順序後的確認保存"
    if "sidebar group" in message.lower():
        return "群組排序資料與目前群組不一致，請重新整理後再試"
    if "重複 target" in message:
        return "排序資料不可包含重複 target"
    if "所有 target" in message or "剛好包含所有 target" in message:
        return "排序資料與目前 target 清單不一致，請重新整理後再試"
    if "id 不可空白" in message:
        return "排序資料包含空白 id，請重新整理後再試"
    if "至少需要選擇" in message:
        return "至少需要選擇一個套用區段"
    if "未知的群組模板套用區段" in message:
        return "未知的群組模板套用區段"
    return "sidebar 資料無法儲存，請重新整理後再試"


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
