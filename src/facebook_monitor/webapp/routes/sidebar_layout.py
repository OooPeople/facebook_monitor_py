"""Sidebar layout write API routes."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi import Request

from facebook_monitor.webapp.dependencies import run_web_app_context_operation
from facebook_monitor.webapp.request_payloads import json_object_payload
from facebook_monitor.webapp.routes.sidebar_common import sidebar_bad_request
from facebook_monitor.webapp.sidebar_use_cases import save_sidebar_group_order_use_case
from facebook_monitor.webapp.sidebar_use_cases import save_sidebar_layout_use_case
from facebook_monitor.webapp.sidebar_use_cases import save_sidebar_order_use_case
from facebook_monitor.webapp.sidebar_use_cases import save_sidebar_placements_use_case


def register_sidebar_layout_routes(app: FastAPI) -> None:
    """註冊 sidebar order / placement routes。"""

    @app.post("/api/sidebar/order")
    async def save_sidebar_order(request: Request) -> dict[str, object]:
        """保存平面 target order，供排序第一階段與 fallback 使用。"""

        payload = await json_object_payload(request)
        try:
            updated_count = await run_web_app_context_operation(
                request,
                lambda app_context: save_sidebar_order_use_case(
                    app_context,
                    payload=payload,
                ),
                operation_name="sidebar.save_target_order",
            )
        except ValueError as exc:
            raise sidebar_bad_request(exc) from exc
        return {"ok": True, "updated_count": updated_count}

    @app.post("/api/sidebar/groups/order")
    async def save_sidebar_group_order(request: Request) -> dict[str, object]:
        """保存 sidebar group order。"""

        payload = await json_object_payload(request)
        try:
            updated_count = await run_web_app_context_operation(
                request,
                lambda app_context: save_sidebar_group_order_use_case(
                    app_context,
                    payload=payload,
                ),
                operation_name="sidebar.save_group_order",
            )
        except ValueError as exc:
            raise sidebar_bad_request(exc) from exc
        return {"ok": True, "updated_count": updated_count}

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

    @app.post("/api/sidebar/placements")
    async def save_sidebar_placements(request: Request) -> dict[str, object]:
        """保存 sidebar group + target placements。"""

        payload = await json_object_payload(request)
        try:
            updated_count = await run_web_app_context_operation(
                request,
                lambda app_context: save_sidebar_placements_use_case(
                    app_context,
                    payload=payload,
                ),
                operation_name="sidebar.save_placements",
            )
        except ValueError as exc:
            raise sidebar_bad_request(exc) from exc
        return {"ok": True, "updated_count": updated_count}
