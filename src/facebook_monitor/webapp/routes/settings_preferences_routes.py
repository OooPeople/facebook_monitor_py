"""Settings preference route registration。"""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import RedirectResponse

from facebook_monitor.webapp.request_payloads import json_object_payload
from facebook_monitor.webapp.dependencies import redirect_settings_with_error
from facebook_monitor.webapp.dependencies import redirect_settings_with_message
from facebook_monitor.webapp.settings_use_cases import (
    parse_target_keyword_defaults_for_settings,
)
from facebook_monitor.webapp.settings_use_cases import save_target_keyword_defaults_for_settings
from facebook_monitor.webapp.settings_use_cases import save_theme_preference_for_settings


def register_settings_preference_routes(app: FastAPI) -> None:
    """註冊 theme 與 target keyword defaults routes。"""

    @app.post("/settings/theme")
    async def update_theme(request: Request) -> dict[str, str]:
        """保存 Web UI theme preference，避免 auto-port 時遺失主題。"""

        payload = await json_object_payload(request)
        theme = str(payload.get("theme", "")).strip()
        if theme not in {"light", "dark"}:
            raise HTTPException(status_code=400, detail="invalid theme")
        return {"theme": await save_theme_preference_for_settings(request, theme)}

    @app.post("/settings/target-keywords")
    async def update_target_keyword_defaults(
        request: Request,
        exclude_keywords: Annotated[str, Form()] = "",
        exclude_ignore_phrases: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """更新新增 target 時套用的關鍵字預設值。"""

        try:
            settings = parse_target_keyword_defaults_for_settings(
                exclude_keywords=exclude_keywords,
                exclude_ignore_phrases=exclude_ignore_phrases,
            )
        except ValueError as exc:
            return redirect_settings_with_error(str(exc))
        await save_target_keyword_defaults_for_settings(request, settings)
        return redirect_settings_with_message(
            "預設值已儲存",
            feedback="target_keyword_defaults_saved",
        )


__all__ = ["register_settings_preference_routes"]
