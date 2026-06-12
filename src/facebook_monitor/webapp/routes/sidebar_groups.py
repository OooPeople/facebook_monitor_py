"""Sidebar group API routes."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request

from facebook_monitor.webapp.dependencies import run_web_app_context_operation
from facebook_monitor.webapp.request_payloads import json_object_payload
from facebook_monitor.webapp.routes.sidebar_common import sidebar_bad_request
from facebook_monitor.webapp.sidebar_use_cases import create_sidebar_group_use_case
from facebook_monitor.webapp.sidebar_use_cases import delete_sidebar_group_use_case
from facebook_monitor.webapp.sidebar_use_cases import update_sidebar_group_use_case


def register_sidebar_group_routes(app: FastAPI) -> None:
    """註冊 sidebar group CRUD routes。"""

    @app.post("/api/sidebar/groups")
    async def create_sidebar_group(request: Request) -> dict[str, object]:
        """建立 sidebar UI group。"""

        payload = await json_object_payload(request)
        try:
            group = await run_web_app_context_operation(
                request,
                lambda app_context: create_sidebar_group_use_case(
                    app_context,
                    payload=payload,
                ),
                operation_name="sidebar.create_group",
            )
        except ValueError as exc:
            raise sidebar_bad_request(exc) from exc
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
        except HTTPException:
            raise
        except ValueError as exc:
            raise sidebar_bad_request(exc) from exc
        return {
            "ok": True,
            "group_id": group.id,
            "name": group.name,
            "collapsed": group.collapsed,
        }

    @app.delete("/api/sidebar/groups/{group_id}")
    async def delete_sidebar_group(request: Request, group_id: str) -> dict[str, object]:
        """刪除空 sidebar group。"""

        try:
            await run_web_app_context_operation(
                request,
                lambda app_context: delete_sidebar_group_use_case(
                    app_context,
                    group_id=group_id,
                ),
                operation_name="sidebar.delete_group",
            )
        except ValueError as exc:
            raise sidebar_bad_request(exc) from exc
        return {"ok": True}
