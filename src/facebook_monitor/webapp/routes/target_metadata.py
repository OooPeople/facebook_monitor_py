"""Target metadata and cover image routes。"""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import CoverImageRefreshRequestStatus
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import redirect_with_error
from facebook_monitor.webapp.dependencies import redirect_with_message
from facebook_monitor.webapp.dependencies import run_web_app_context_operation
from facebook_monitor.webapp.dependencies import start_resident_scheduler_if_needed
from facebook_monitor.webapp.request_payloads import json_object_payload


def register_target_metadata_routes(app: FastAPI) -> None:
    """註冊 target metadata 與 cover image routes。"""

    @app.post("/targets/{target_id}/metadata/refresh")
    async def refresh_target_metadata_route(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """手動要求 resident worker 重新抓取 target 名稱與封面。"""

        try:

            def mark_pending(app_context: ApplicationContext) -> TargetDescriptor | None:
                """標記 metadata refresh pending 並回傳 target 是否存在。"""

                target = app_context.repositories.targets.get(target_id)
                if target is None:
                    return None
                app_context.services.targets.mark_target_metadata_refresh_pending(target_id)
                return target

            target = await run_web_app_context_operation(
                request,
                mark_pending,
                operation_name="request_target_metadata_refresh",
            )
            if target is None:
                return redirect_with_error("重新抓取失敗: target 不存在", return_to=return_to)
            get_scheduler_manager(request).request_metadata_refresh(target_id)
            start_resident_scheduler_if_needed(request)
        except Exception as exc:
            return redirect_with_error(
                "重新抓取失敗：" + format_failure_message_text(str(exc)),
                return_to=return_to,
            )
        return redirect_with_message(
            "已加入排程，會在下一次啟動時抓取名稱與封面",
            return_to=return_to,
        )

    @app.post("/api/targets/{target_id}/cover-image/load-failure")
    async def report_target_cover_image_load_failure(
        request: Request,
        target_id: str,
    ) -> JSONResponse:
        """接收 UI 壞圖 hint，排程 image-only cover URL 背景刷新。"""

        payload = await json_object_payload(request)
        reported_url = str(payload.get("url", "")).strip()
        min_interval_seconds = (
            PYTHON_SCHEDULER_RUNTIME_DEFAULTS.cover_image_load_failure_min_interval_seconds
        )
        result = await run_web_app_context_operation(
            request,
            lambda app_context: app_context.services.targets.request_target_cover_image_refresh(
                target_id,
                reported_url=reported_url,
                min_interval_seconds=min_interval_seconds,
            ),
            operation_name="request_target_cover_image_refresh",
        )
        if result.status in {
            CoverImageRefreshRequestStatus.QUEUED,
            CoverImageRefreshRequestStatus.PENDING,
        }:
            start_resident_scheduler_if_needed(request)
        return JSONResponse({
            "ok": result.status
            not in {
                CoverImageRefreshRequestStatus.NOT_FOUND,
                CoverImageRefreshRequestStatus.INVALID_URL,
            },
            "status": result.status,
            "queued": result.queued,
            "reason": result.reason,
        })
