"""Sidebar layout write API routes。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request

from facebook_monitor.application.target_actions import pause_sidebar_group_monitoring_action
from facebook_monitor.application.target_actions import restart_sidebar_group_monitoring_action
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import run_web_app_context_operation
from facebook_monitor.webapp.dependencies import run_web_db_operation
from facebook_monitor.webapp.dependencies import start_resident_scheduler_if_needed
from facebook_monitor.webapp.form_models import TargetConfigForm
from facebook_monitor.webapp.request_payloads import json_object_payload
from facebook_monitor.webapp.sidebar_api import grouped_target_ids
from facebook_monitor.webapp.sidebar_api import sidebar_error_detail
from facebook_monitor.webapp.sidebar_api import string_list
from facebook_monitor.webapp.sidebar_use_cases import parse_sidebar_template_sections
from facebook_monitor.webapp.sidebar_use_cases import save_sidebar_group_template_use_case
from facebook_monitor.webapp.sidebar_use_cases import update_sidebar_group_use_case


def register_sidebar_routes(app: FastAPI) -> None:
    """註冊 sidebar layout、group 與 group template routes。"""

    @app.post("/api/sidebar/order")
    async def save_sidebar_order(request: Request) -> dict[str, object]:
        """保存平面 target order，供排序第一階段與 fallback 使用。"""

        payload = await json_object_payload(request)
        target_ids = string_list(payload.get("target_ids"))
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
            group = await run_web_app_context_operation(
                request,
                lambda app_context: update_sidebar_group_use_case(
                    app_context,
                    group_id=group_id,
                    payload=payload,
                ),
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
            if outcome.start_scheduler:
                start_resident_scheduler_if_needed(request)
            elif outcome.wake_scheduler:
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
            if outcome.start_scheduler:
                start_resident_scheduler_if_needed(request)
            elif outcome.wake_scheduler:
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
        group_ids = string_list(payload.get("group_ids"))
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
        group_ids = string_list(payload.get("group_ids"))
        parsed_groups = grouped_target_ids(payload.get("groups"))
        try:
            updated_count = await run_web_app_context_operation(
                request,
                lambda app_context: app_context.services.sidebar_layout.save_layout(
                    group_ids=group_ids,
                    grouped_target_ids=parsed_groups,
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
        parsed_groups = grouped_target_ids(payload.get("groups"))
        try:
            updated_count = await run_web_app_context_operation(
                request,
                lambda app_context: app_context.services.sidebar_layout.save_placements(
                    parsed_groups
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
            template = await run_web_app_context_operation(
                request,
                lambda app_context: save_sidebar_group_template_use_case(
                    app_context,
                    group_id=group_id,
                    form=form,
                ),
                operation_name="sidebar.save_group_template",
            )
        except ValueError as exc:
            raise _sidebar_bad_request(exc) from exc
        return {"ok": True, "group_id": template.sidebar_group_id}

    @app.post("/api/sidebar/groups/{group_id}/template/apply")
    async def apply_sidebar_group_template(request: Request, group_id: str) -> dict[str, object]:
        """將 sidebar group template 明確套用到該 group 內 target configs。"""

        payload = await json_object_payload(request)
        sections = parse_sidebar_template_sections(payload)
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

    return HTTPException(status_code=400, detail=sidebar_error_detail(exc))
