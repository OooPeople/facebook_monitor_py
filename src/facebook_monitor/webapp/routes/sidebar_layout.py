"""Sidebar layout write API routes."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request

from facebook_monitor.webapp.dependencies import run_web_app_context_operation
from facebook_monitor.webapp.request_payloads import json_object_payload
from facebook_monitor.webapp.routes.sidebar_common import sidebar_bad_request
from facebook_monitor.webapp.sidebar_use_cases import save_sidebar_layout_use_case


def register_sidebar_layout_routes(app: FastAPI) -> None:
    """註冊 sidebar order / placement routes。"""

    @app.post("/api/sidebar/groups/order", include_in_schema=False)
    async def save_sidebar_group_order(request: Request) -> dict[str, object]:
        """舊分段排序 API 已退役；正式保存只走 sidebar layout command。"""

        raise HTTPException(status_code=410, detail="sidebar layout API moved")

    @app.post("/api/sidebar/layout")
    async def save_sidebar_layout(request: Request) -> dict[str, object]:
        """以單一 transaction 保存 sidebar group order 與 target placements。"""

        payload = await json_object_payload(request)
        try:
            updated_count = await run_web_app_context_operation(
                request,
                lambda app_context: save_sidebar_layout_use_case(
                    app_context,
                    payload=payload,
                ),
                operation_name="sidebar.save_layout",
            )
        except ValueError as exc:
            raise sidebar_bad_request(exc) from exc
        return {"ok": True, "updated_count": updated_count}

    @app.post("/api/sidebar/placements", include_in_schema=False)
    async def save_sidebar_placements(request: Request) -> dict[str, object]:
        """舊分段 placement API 已退役；正式保存只走 sidebar layout command。"""

        raise HTTPException(status_code=410, detail="sidebar layout API moved")
