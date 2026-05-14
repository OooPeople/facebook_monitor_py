"""Sidebar layout write API routes。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.form_models import TargetConfigForm


def register_sidebar_routes(app: FastAPI) -> None:
    """註冊 sidebar layout、group 與 group template routes。"""

    @app.post("/api/sidebar/order")
    async def save_sidebar_order(request: Request) -> dict[str, object]:
        """保存平面 target order，供排序第一階段與 fallback 使用。"""

        payload = await _json_payload(request)
        target_ids = _string_list(payload.get("target_ids"))
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                updated_count = app_context.services.sidebar_layout.save_target_order(target_ids)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "updated_count": updated_count}

    @app.post("/api/sidebar/groups")
    async def create_sidebar_group(request: Request) -> dict[str, object]:
        """建立 sidebar UI group。"""

        payload = await _json_payload(request)
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                group = app_context.services.sidebar_layout.create_group(str(payload.get("name", "")))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "group_id": group.id, "name": group.name}

    @app.patch("/api/sidebar/groups/{group_id}")
    async def update_sidebar_group(request: Request, group_id: str) -> dict[str, object]:
        """更新 sidebar group name 或 collapsed 狀態。"""

        payload = await _json_payload(request)
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                group = app_context.repositories.sidebar_layout.get_group(group_id)
                if group is None:
                    raise HTTPException(status_code=404, detail="sidebar group not found")
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
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "group_id": group.id, "name": group.name, "collapsed": group.collapsed}

    @app.delete("/api/sidebar/groups/{group_id}")
    async def delete_sidebar_group(request: Request, group_id: str) -> dict[str, object]:
        """刪除空 sidebar group。"""

        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                app_context.services.sidebar_layout.delete_empty_group(group_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/sidebar/groups/order")
    async def save_sidebar_group_order(request: Request) -> dict[str, object]:
        """保存 sidebar group order。"""

        payload = await _json_payload(request)
        group_ids = _string_list(payload.get("group_ids"))
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                app_context.services.sidebar_layout.save_group_order(group_ids)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "updated_count": len(group_ids)}

    @app.post("/api/sidebar/layout")
    async def save_sidebar_layout(request: Request) -> dict[str, object]:
        """以單一 transaction 保存 sidebar group order 與 target placements。"""

        payload = await _json_payload(request)
        group_ids = _string_list(payload.get("group_ids"))
        grouped_target_ids = _grouped_target_ids(payload.get("groups"))
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                updated_count = app_context.services.sidebar_layout.save_layout(
                    group_ids=group_ids,
                    grouped_target_ids=grouped_target_ids,
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "updated_count": updated_count}

    @app.post("/api/sidebar/placements")
    async def save_sidebar_placements(request: Request) -> dict[str, object]:
        """保存 sidebar group + target placements。"""

        payload = await _json_payload(request)
        grouped_target_ids = _grouped_target_ids(payload.get("groups"))
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                updated_count = app_context.services.sidebar_layout.save_placements(
                    grouped_target_ids
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "updated_count": updated_count}

    @app.put("/api/sidebar/groups/{group_id}/template")
    async def save_sidebar_group_template(request: Request, group_id: str) -> dict[str, object]:
        """保存 sidebar group config template。"""

        payload = await _json_payload(request)
        form = _target_config_form_from_payload(payload)
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                template = app_context.services.sidebar_layout.save_template(
                    form.to_sidebar_group_template(sidebar_group_id=group_id)
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "group_id": template.sidebar_group_id}

    @app.post("/api/sidebar/groups/{group_id}/template/apply")
    async def apply_sidebar_group_template(request: Request, group_id: str) -> dict[str, object]:
        """將 sidebar group template 明確套用到該 group 內 target configs。"""

        payload = await _json_payload(request)
        sections = _string_list(payload.get("sections") or ["all"])
        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                updated_count = app_context.services.sidebar_layout.apply_template(
                    group_id,
                    sections,  # type: ignore[arg-type]
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "updated_count": updated_count}

async def _json_payload(request: Request) -> dict[str, object]:
    """讀取 JSON payload 並確認為 object。"""

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON payload must be an object")
    return payload


def _string_list(value: object) -> list[str]:
    """將 payload 欄位轉為字串清單。"""

    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="expected list payload")
    return [str(item) for item in value]


def _grouped_target_ids(value: object) -> list[tuple[str | None, list[str]]]:
    """解析 grouped placements payload。"""

    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="groups must be a list")
    groups: list[tuple[str | None, list[str]]] = []
    for item in value:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="group placement must be an object")
        raw_group_id = item.get("group_id")
        group_id = str(raw_group_id).strip() if raw_group_id is not None else None
        if group_id == "":
            group_id = None
        groups.append((group_id, _string_list(item.get("target_ids"))))
    return groups


def _target_config_form_from_payload(payload: dict[str, object]) -> TargetConfigForm:
    """將 JSON template payload 轉成共用 TargetConfigForm。"""

    return TargetConfigForm(
        include_keywords=str(payload.get("include_keywords", "")),
        exclude_keywords=str(payload.get("exclude_keywords", "")),
        exclude_ignore_phrases=str(payload.get("exclude_ignore_phrases", "")),
        refresh_mode=str(payload.get("refresh_mode", "fixed")),
        fixed_refresh_sec=_int_payload(
            payload.get("fixed_refresh_sec"),
            PYTHON_TARGET_CONFIG_DEFAULTS.fixed_refresh_sec or 60,
        ),
        min_refresh_sec=_int_payload(
            payload.get("min_refresh_sec"),
            PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec,
        ),
        max_refresh_sec=_int_payload(
            payload.get("max_refresh_sec"),
            PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec,
        ),
        max_items_per_scan=_int_payload(
            payload.get("max_items_per_scan"),
            PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan,
        ),
        auto_load_more=_checkbox_payload(payload.get("auto_load_more")),
        auto_adjust_sort=_checkbox_payload(payload.get("auto_adjust_sort")),
        enable_desktop_notification=_checkbox_payload(
            payload.get("enable_desktop_notification")
        ),
        enable_ntfy=_checkbox_payload(payload.get("enable_ntfy")),
        ntfy_topic=str(payload.get("ntfy_topic", "")),
        enable_discord_notification=_checkbox_payload(
            payload.get("enable_discord_notification")
        ),
        discord_webhook=str(payload.get("discord_webhook", "")),
    )


def _checkbox_payload(value: object) -> str | None:
    """將 JSON boolean 轉成 TargetConfigForm checkbox 表示。"""

    return "on" if bool(value) else None


def _int_payload(value: object, fallback: int) -> int:
    """解析整數 payload。"""

    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return fallback
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return fallback
