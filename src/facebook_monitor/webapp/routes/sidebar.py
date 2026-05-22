"""Sidebar layout write API routes。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.form_models import format_notification_form_error
from facebook_monitor.webapp.form_models import TargetConfigForm
from facebook_monitor.webapp.request_payloads import json_object_payload


def register_sidebar_routes(app: FastAPI) -> None:
    """註冊 sidebar layout、group 與 group template routes。"""

    @app.post("/api/sidebar/order")
    async def save_sidebar_order(request: Request) -> dict[str, object]:
        """保存平面 target order，供排序第一階段與 fallback 使用。"""

        payload = await json_object_payload(request)
        target_ids = _string_list(payload.get("target_ids"))
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                updated_count = app_context.services.sidebar_layout.save_target_order(target_ids)
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {"ok": True, "updated_count": updated_count}

    @app.post("/api/sidebar/groups")
    async def create_sidebar_group(request: Request) -> dict[str, object]:
        """建立 sidebar UI group。"""

        payload = await json_object_payload(request)
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                group = app_context.services.sidebar_layout.create_group(str(payload.get("name", "")))
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {"ok": True, "group_id": group.id, "name": group.name}

    @app.patch("/api/sidebar/groups/{group_id}")
    async def update_sidebar_group(request: Request, group_id: str) -> dict[str, object]:
        """更新 sidebar group name 或 collapsed 狀態。"""

        payload = await json_object_payload(request)
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                group = app_context.repositories.sidebar_layout.get_group(group_id)
                if group is None:
                    raise HTTPException(status_code=404, detail="找不到指定的 sidebar 群組")
                if "name" in payload:
                    group = app_context.services.sidebar_layout.rename_group(
                        group_id,
                        str(payload.get("name", "")),
                    )
                if "collapsed" in payload:
                    group = app_context.services.sidebar_layout.set_group_collapsed(
                        group_id,
                        bool(payload.get("collapsed")),
                    )
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {"ok": True, "group_id": group.id, "name": group.name, "collapsed": group.collapsed}

    @app.delete("/api/sidebar/groups/{group_id}")
    async def delete_sidebar_group(request: Request, group_id: str) -> dict[str, object]:
        """刪除空 sidebar group。"""

        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                app_context.services.sidebar_layout.delete_empty_group(group_id)
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {"ok": True}

    @app.post("/api/sidebar/groups/order")
    async def save_sidebar_group_order(request: Request) -> dict[str, object]:
        """保存 sidebar group order。"""

        payload = await json_object_payload(request)
        group_ids = _string_list(payload.get("group_ids"))
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                app_context.services.sidebar_layout.save_group_order(group_ids)
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {"ok": True, "updated_count": len(group_ids)}

    @app.post("/api/sidebar/layout")
    async def save_sidebar_layout(request: Request) -> dict[str, object]:
        """以單一 transaction 保存 sidebar group order 與 target placements。"""

        payload = await json_object_payload(request)
        group_ids = _string_list(payload.get("group_ids"))
        grouped_target_ids = _grouped_target_ids(payload.get("groups"))
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                updated_count = app_context.services.sidebar_layout.save_layout(
                    group_ids=group_ids,
                    grouped_target_ids=grouped_target_ids,
                )
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {"ok": True, "updated_count": updated_count}

    @app.post("/api/sidebar/placements")
    async def save_sidebar_placements(request: Request) -> dict[str, object]:
        """保存 sidebar group + target placements。"""

        payload = await json_object_payload(request)
        grouped_target_ids = _grouped_target_ids(payload.get("groups"))
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                updated_count = app_context.services.sidebar_layout.save_placements(
                    grouped_target_ids
                )
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {"ok": True, "updated_count": updated_count}

    @app.put("/api/sidebar/groups/{group_id}/template")
    async def save_sidebar_group_template(request: Request, group_id: str) -> dict[str, object]:
        """保存 sidebar group config template。"""

        payload = await json_object_payload(request)
        form = TargetConfigForm.from_sidebar_template_payload(payload)
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                current_template = app_context.services.sidebar_layout.get_template_or_default(
                    group_id,
                )
                template = app_context.services.sidebar_layout.save_template(
                    form.to_sidebar_group_template(
                        sidebar_group_id=group_id,
                        existing_discord_webhook=current_template.discord_webhook,
                    )
                )
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {"ok": True, "group_id": template.sidebar_group_id}

    @app.post("/api/sidebar/groups/{group_id}/template/apply")
    async def apply_sidebar_group_template(request: Request, group_id: str) -> dict[str, object]:
        """將 sidebar group template 明確套用到該 group 內 target configs。"""

        payload = await json_object_payload(request)
        sections = _string_list(payload.get("sections") or ["all"])
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                updated_count = app_context.services.sidebar_layout.apply_template(
                    group_id,
                    sections,  # type: ignore[arg-type]
                )
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {"ok": True, "updated_count": updated_count}


def _sidebar_bad_request(exc: ValueError) -> HTTPException:
    """將 sidebar application 錯誤轉成安全、可顯示的繁中 API 訊息。"""

    return HTTPException(status_code=400, detail=_sidebar_error_detail(exc))


def _sidebar_error_detail(exc: ValueError) -> str:
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


def _string_list(value: object) -> list[str]:
    """將 payload 欄位轉為字串清單。"""

    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="欄位必須是清單")
    return [str(item) for item in value]


def _grouped_target_ids(value: object) -> list[tuple[str | None, list[str]]]:
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
        groups.append((group_id, _string_list(item.get("target_ids"))))
    return groups
