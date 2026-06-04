"""Hit record routes。

職責：提供 UI 重構 Phase 1 所需的 target-scoped 命中紀錄查詢與清空 API。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request

from facebook_monitor.application.target_actions import clear_target_hit_records_action
from facebook_monitor.core.defaults import PYTHON_WEBUI_RUNTIME_DEFAULTS
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_session_started_at
from facebook_monitor.webapp.dependencies import run_web_db_operation
from facebook_monitor.webapp.dependencies import run_web_read_operation
from facebook_monitor.webapp.query_service import count_hit_records
from facebook_monitor.webapp.query_service import list_full_hit_record_rows
from facebook_monitor.webapp.query_service import list_hit_record_preview_rows
from facebook_monitor.webapp.query_service import target_exists
from facebook_monitor.webapp.query_service import DashboardReadUnavailable


def register_hit_record_routes(app: FastAPI) -> None:
    """註冊 target-scoped hit record API routes。"""

    async def ensure_target_exists(request: Request, target_id: str) -> None:
        """確認 target 存在，讓 API 對不存在 target 回傳 404。"""

        db_path = get_db_path(request)
        try:
            exists = await run_web_read_operation(
                lambda: target_exists(db_path, target_id),
                operation_name="hit_records.target_exists",
            )
        except DashboardReadUnavailable as exc:
            raise HTTPException(status_code=503, detail="dashboard data unavailable") from exc
        if not exists:
            raise HTTPException(status_code=404, detail="target not found")

    @app.get("/api/targets/{target_id}/hit-records/preview")
    async def hit_record_preview(
        request: Request,
        target_id: str,
        limit: int = Query(
            default=PYTHON_WEBUI_RUNTIME_DEFAULTS.hit_record_preview_limit,
            ge=1,
            le=PYTHON_WEBUI_RUNTIME_DEFAULTS.hit_record_preview_max_limit,
        ),
    ) -> dict[str, object]:
        """回傳右側 preview tab 使用的本次 session 命中紀錄。"""

        await ensure_target_exists(request, target_id)
        db_path = get_db_path(request)
        session_started_at = get_session_started_at(request)
        try:
            rows = await run_web_read_operation(
                lambda: list_hit_record_preview_rows(
                    db_path,
                    target_id,
                    limit=limit,
                    session_started_at=session_started_at,
                ),
                operation_name="hit_records.preview",
            )
            total_count = await run_web_read_operation(
                lambda: count_hit_records(
                    db_path,
                    target_id,
                    session_started_at=session_started_at,
                ),
                operation_name="hit_records.preview_count",
            )
        except DashboardReadUnavailable as exc:
            raise HTTPException(status_code=503, detail="dashboard data unavailable") from exc
        return {
            "target_id": target_id,
            "total_count": total_count,
            "items": [row.to_dict() for row in rows],
        }

    @app.get("/api/targets/{target_id}/hit-records/count")
    async def hit_record_count(request: Request, target_id: str) -> dict[str, object]:
        """回傳單一 target 本次 session 的命中紀錄總數。"""

        await ensure_target_exists(request, target_id)
        db_path = get_db_path(request)
        session_started_at = get_session_started_at(request)
        try:
            total_count = await run_web_read_operation(
                lambda: count_hit_records(
                    db_path,
                    target_id,
                    session_started_at=session_started_at,
                ),
                operation_name="hit_records.count",
            )
        except DashboardReadUnavailable as exc:
            raise HTTPException(status_code=503, detail="dashboard data unavailable") from exc
        return {"target_id": target_id, "total_count": total_count}

    @app.get("/api/targets/{target_id}/hit-records")
    async def hit_record_list(
        request: Request,
        target_id: str,
        limit: int = Query(
            default=PYTHON_WEBUI_RUNTIME_DEFAULTS.hit_record_full_limit,
            ge=1,
            le=PYTHON_WEBUI_RUNTIME_DEFAULTS.hit_record_full_max_limit,
        ),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        """回傳完整查看紀錄 modal 使用的持久詳細列表。"""

        await ensure_target_exists(request, target_id)
        db_path = get_db_path(request)
        try:
            rows = await run_web_read_operation(
                lambda: list_full_hit_record_rows(
                    db_path,
                    target_id,
                    limit=limit,
                    offset=offset,
                ),
                operation_name="hit_records.list",
            )
            total_count = await run_web_read_operation(
                lambda: count_hit_records(db_path, target_id),
                operation_name="hit_records.list_count",
            )
        except DashboardReadUnavailable as exc:
            raise HTTPException(status_code=503, detail="dashboard data unavailable") from exc
        return {
            "target_id": target_id,
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
            "items": [row.to_dict() for row in rows],
        }

    @app.delete("/api/targets/{target_id}/hit-records")
    async def clear_hit_record_list(request: Request, target_id: str) -> dict[str, object]:
        """清空單一 target 的命中紀錄，不影響其他 runtime/debug 資料。"""

        await ensure_target_exists(request, target_id)
        db_path = get_db_path(request)
        outcome = await run_web_db_operation(
            lambda: clear_target_hit_records_action(db_path, target_id),
            operation_name="hit_records.clear_target",
        )
        if not outcome.ok:
            raise HTTPException(status_code=404, detail=outcome.message)
        return {
            "target_id": target_id,
            "deleted_count": outcome.updated_count,
            "total_count": 0,
        }
