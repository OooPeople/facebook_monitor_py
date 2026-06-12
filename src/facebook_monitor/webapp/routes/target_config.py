"""Target config routes。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from fastapi.responses import RedirectResponse

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.input_limits import normalize_display_name
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.webapp.dependencies import redirect_with_error
from facebook_monitor.webapp.dependencies import redirect_with_message
from facebook_monitor.webapp.dependencies import run_web_app_context_operation
from facebook_monitor.webapp.notification_form_models import format_notification_form_error
from facebook_monitor.webapp.target_config_form import TargetConfigForm


def register_target_config_routes(app: FastAPI) -> None:
    """註冊 target 設定與顯示名稱 routes。"""

    @app.post("/targets/{target_id}/config")
    async def update_config(
        request: Request,
        target_id: str,
        config_form: Annotated[
            TargetConfigForm,
            Depends(TargetConfigForm.as_form),
        ],
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """更新單一 target 設定。"""

        try:

            def update(app_context: ApplicationContext) -> None:
                """在 Web DB retry/thread 邊界內更新 target config。"""

                app_context.services.targets.update_target_config(
                    config_form.to_update_request(target_id=target_id)
                )

            await run_web_app_context_operation(
                request,
                update,
                operation_name="update_target_config",
            )
        except ValueError as exc:
            return redirect_with_error(
                "設定更新失敗：" + format_notification_form_error(exc),
                return_to=return_to,
            )
        except Exception as exc:
            return redirect_with_error(
                "設定更新失敗：" + format_failure_message_text(str(exc)),
                return_to=return_to,
            )
        return redirect_with_message(
            "設定已更新",
            return_to=return_to,
            feedback="target_config_saved",
        )

    @app.post("/targets/{target_id}/name")
    async def update_target_name(
        request: Request,
        target_id: str,
        display_name: Annotated[str, Form()] = "",
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """更新 target card 顯示名稱。"""

        try:

            def update(app_context: ApplicationContext) -> None:
                """在 Web DB retry/thread 邊界內更新 target 顯示名稱。"""

                app_context.services.targets.update_target_name(
                    target_id,
                    normalize_display_name(display_name),
                )

            await run_web_app_context_operation(
                request,
                update,
                operation_name="update_target_name",
            )
        except Exception as exc:
            return redirect_with_error(
                "名稱更新失敗：" + format_failure_message_text(str(exc)),
                return_to=return_to,
            )
        return redirect_with_message(
            "名稱已更新",
            return_to=return_to,
            feedback="target_name_saved",
        )
