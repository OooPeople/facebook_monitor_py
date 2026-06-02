"""Sidebar layout write API routes。"""

from __future__ import annotations

from typing import cast

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.sidebar_layout_service import SidebarTemplateSection
from facebook_monitor.application.target_actions import pause_sidebar_group_monitoring_action
from facebook_monitor.application.target_actions import restart_sidebar_group_monitoring_action
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import run_web_app_context_operation
from facebook_monitor.webapp.dependencies import run_web_db_operation
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
            updated_count = await run_web_app_context_operation(
                request,
                lambda app_context: app_context.services.sidebar_layout.save_target_order(
                    target_ids
                ),
                operation_name="sidebar.save_target_order",
            )
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {"ok": True, "updated_count": updated_count}

    @app.post("/api/sidebar/groups")
    async def create_sidebar_group(request: Request) -> dict[str, object]:
        """建立 sidebar UI group。"""

        payload = await json_object_payload(request)
        try:
            group = await run_web_app_context_operation(
                request,
                lambda app_context: app_context.services.sidebar_layout.create_group(
                    str(payload.get("name", ""))
                ),
                operation_name="sidebar.create_group",
            )
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {"ok": True, "group_id": group.id, "name": group.name}

    @app.patch("/api/sidebar/groups/{group_id}")
    async def update_sidebar_group(request: Request, group_id: str) -> dict[str, object]:
        """更新 sidebar group name 或 collapsed 狀態。"""

        payload = await json_object_payload(request)
        try:
            def update(app_context: ApplicationContext):
                """更新 sidebar group name/collapsed，維持單一 DB operation。"""

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
                return group

            group = await run_web_app_context_operation(
                request,
                update,
                operation_name="sidebar.update_group",
            )
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {"ok": True, "group_id": group.id, "name": group.name, "collapsed": group.collapsed}

    @app.delete("/api/sidebar/groups/{group_id}")
    async def delete_sidebar_group(request: Request, group_id: str) -> dict[str, object]:
        """刪除空 sidebar group。"""

        try:
            await run_web_app_context_operation(
                request,
                lambda app_context: app_context.services.sidebar_layout.delete_empty_group(
                    group_id
                ),
                operation_name="sidebar.delete_group",
            )
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {"ok": True}

    @app.post("/api/sidebar/groups/{group_id}/start")
    async def start_sidebar_group_monitoring(
        request: Request,
        group_id: str,
    ) -> dict[str, object]:
        """開始 sidebar group 內 targets，route 只負責喚醒 scheduler 一次。"""

        try:
            db_path = get_db_path(request)
            outcome = await run_web_db_operation(
                lambda: restart_sidebar_group_monitoring_action(db_path, group_id),
                operation_name="sidebar.start_group_monitoring",
            )
            if outcome.wake_scheduler:
                get_scheduler_manager(request).wake()
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {
            "ok": outcome.ok,
            "updated_count": outcome.updated_count,
            "message": outcome.message,
        }

    @app.post("/api/sidebar/groups/{group_id}/stop")
    async def stop_sidebar_group_monitoring(
        request: Request,
        group_id: str,
    ) -> dict[str, object]:
        """停止 sidebar group 內 targets，保留 target-scoped seen/history。"""

        try:
            db_path = get_db_path(request)
            outcome = await run_web_db_operation(
                lambda: pause_sidebar_group_monitoring_action(db_path, group_id),
                operation_name="sidebar.stop_group_monitoring",
            )
            if outcome.wake_scheduler:
                get_scheduler_manager(request).wake()
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {
            "ok": outcome.ok,
            "updated_count": outcome.updated_count,
            "message": outcome.message,
        }

    @app.post("/api/sidebar/groups/order")
    async def save_sidebar_group_order(request: Request) -> dict[str, object]:
        """保存 sidebar group order。"""

        payload = await json_object_payload(request)
        group_ids = _string_list(payload.get("group_ids"))
        try:
            await run_web_app_context_operation(
                request,
                lambda app_context: app_context.services.sidebar_layout.save_group_order(
                    group_ids
                ),
                operation_name="sidebar.save_group_order",
            )
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
            updated_count = await run_web_app_context_operation(
                request,
                lambda app_context: app_context.services.sidebar_layout.save_layout(
                    group_ids=group_ids,
                    grouped_target_ids=grouped_target_ids,
                ),
                operation_name="sidebar.save_layout",
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
            updated_count = await run_web_app_context_operation(
                request,
                lambda app_context: app_context.services.sidebar_layout.save_placements(
                    grouped_target_ids
                ),
                operation_name="sidebar.save_placements",
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
            def save_template(app_context: ApplicationContext):
                """保存 sidebar group template，保留既有 secret 清除/沿用語義。"""

                current_template = app_context.services.sidebar_layout.get_template_or_default(
                    group_id,
                )
                template = app_context.services.sidebar_layout.save_template(
                    form.to_sidebar_group_template(
                        sidebar_group_id=group_id,
                        existing_ntfy_topic=current_template.ntfy_topic,
                        existing_discord_webhook=current_template.discord_webhook,
                    )
                )
                return template

            template = await run_web_app_context_operation(
                request,
                save_template,
                operation_name="sidebar.save_group_template",
            )
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {"ok": True, "group_id": template.sidebar_group_id}

    @app.post("/api/sidebar/groups/{group_id}/template/apply")
    async def apply_sidebar_group_template(request: Request, group_id: str) -> dict[str, object]:
        """將 sidebar group template 明確套用到該 group 內 target configs。"""

        payload = await json_object_payload(request)
        sections = cast(
            list[SidebarTemplateSection],
            _string_list(payload.get("sections") or ["all"]),
        )
        try:
            updated_count = await run_web_app_context_operation(
                request,
                lambda app_context: app_context.services.sidebar_layout.apply_template(
                    group_id,
                    sections,
                ),
                operation_name="sidebar.apply_group_template",
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
