"""Sidebar Web UI use cases。

職責：承接 sidebar route 的 payload 語義與 application service 呼叫，避免 route
直接做易錯的 JSON 型別轉換。
"""

from __future__ import annotations

from typing import cast

from fastapi import HTTPException

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.sidebar_layout_service import SidebarTemplateSection
from facebook_monitor.core.sidebar_models import SidebarGroup
from facebook_monitor.core.sidebar_models import SidebarGroupConfigTemplate
from facebook_monitor.webapp.form_models import TargetConfigForm
from facebook_monitor.webapp.sidebar_api import string_list


def update_sidebar_group_use_case(
    app_context: ApplicationContext,
    *,
    group_id: str,
    payload: dict[str, object],
) -> SidebarGroup:
    """更新 sidebar group 名稱與收合狀態。"""

    group = app_context.repositories.sidebar_layout.get_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="找不到指定的 sidebar 群組")
    name = parse_sidebar_group_name(payload)
    collapsed = parse_sidebar_group_collapsed(payload)
    if name is not None:
        group = app_context.services.sidebar_layout.rename_group(
            group_id,
            name,
        )
    if collapsed is not None:
        group = app_context.services.sidebar_layout.set_group_collapsed(
            group_id,
            collapsed,
        )
    return group


def parse_sidebar_group_name(payload: dict[str, object]) -> str | None:
    """解析 sidebar group name；先驗證避免 mixed payload partial write。"""

    if "name" not in payload:
        return None
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("群組名稱不可空白")
    return name


def parse_sidebar_group_collapsed(payload: dict[str, object]) -> bool | None:
    """解析 sidebar group collapsed；只接受 JSON boolean。"""

    if "collapsed" not in payload:
        return None
    value = payload["collapsed"]
    if not isinstance(value, bool):
        raise ValueError("collapsed 必須是布林值")
    return value


def save_sidebar_group_template_use_case(
    app_context: ApplicationContext,
    *,
    group_id: str,
    form: TargetConfigForm,
) -> SidebarGroupConfigTemplate:
    """保存 sidebar group template，保留 secret 清除/沿用語義。"""

    current_template = app_context.services.sidebar_layout.get_template_or_default(
        group_id,
    )
    return app_context.services.sidebar_layout.save_template(
        form.to_sidebar_group_template(
            sidebar_group_id=group_id,
            existing_ntfy_topic=current_template.ntfy_topic,
            existing_discord_webhook=current_template.discord_webhook,
        )
    )


def parse_sidebar_template_sections(
    payload: dict[str, object],
) -> list[SidebarTemplateSection]:
    """解析 template apply sections；缺省才代表 all，空清單交給 service 拒絕。"""

    if "sections" not in payload:
        return ["all"]
    return cast(list[SidebarTemplateSection], string_list(payload.get("sections")))
