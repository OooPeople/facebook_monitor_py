"""Sidebar group template API routes."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi import Request

from facebook_monitor.webapp.dependencies import run_web_app_context_operation
from facebook_monitor.webapp.form_models import TargetConfigForm
from facebook_monitor.webapp.request_payloads import json_object_payload
from facebook_monitor.webapp.routes.sidebar_common import sidebar_bad_request
from facebook_monitor.webapp.sidebar_use_cases import parse_sidebar_template_sections
from facebook_monitor.webapp.sidebar_use_cases import save_sidebar_group_template_use_case


def register_sidebar_template_routes(app: FastAPI) -> None:
    """註冊 sidebar group template routes。"""

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
            raise sidebar_bad_request(exc) from exc
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
            raise sidebar_bad_request(exc) from exc
        return {"ok": True, "updated_count": updated_count}
