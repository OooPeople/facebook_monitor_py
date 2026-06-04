"""Target notification test routes。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.user_messages import format_notification_event_message
from facebook_monitor.notifications.manual_test import send_manual_test_notification
from facebook_monitor.notifications.safe_messages import safe_exception_message
from facebook_monitor.webapp.dependencies import get_desktop_sender
from facebook_monitor.webapp.dependencies import get_discord_sender
from facebook_monitor.webapp.dependencies import get_ntfy_sender
from facebook_monitor.webapp.dependencies import redirect_with_error
from facebook_monitor.webapp.dependencies import redirect_with_message
from facebook_monitor.webapp.dependencies import run_web_app_context_operation
from facebook_monitor.webapp.form_models import format_notification_form_error
from facebook_monitor.webapp.form_models import NotificationConfigForm


class _TargetNotificationTestNotFound(Exception):
    """表示測試通知 route 找不到指定 target。"""


def _wants_json_response(request: Request) -> bool:
    """判斷前端是否要求保留目前頁面並以 JSON 接收操作結果。"""

    return "application/json" in request.headers.get("accept", "").lower()


async def _send_target_test_notifications(
    request: Request,
    *,
    target_id: str,
    notification_form: NotificationConfigForm,
) -> list[str]:
    """依表單欄位送出 target 測試通知，並回傳已在 UI 顯示前本地化的結果。"""

    def load_config(
        app_context: ApplicationContext,
    ) -> tuple[TargetDescriptor, TargetConfig]:
        """讀取測試通知需要的 target 與既有通知 secret。"""

        target = app_context.repositories.targets.get(target_id)
        if target is None:
            raise _TargetNotificationTestNotFound
        existing_config = app_context.services.targets.get_config_for_target(target)
        return target, existing_config

    target, existing_config = await run_web_app_context_operation(
        request,
        load_config,
        operation_name="load_target_notification_test_config",
    )
    config = notification_form.to_target_config(
        target_id=target.id,
        existing_ntfy_topic=existing_config.ntfy_topic,
        existing_discord_webhook=existing_config.discord_webhook,
    )
    results = await run_in_threadpool(
        send_manual_test_notification,
        config=config,
        ntfy_sender=get_ntfy_sender(request),
        desktop_sender=get_desktop_sender(request),
        discord_sender=get_discord_sender(request),
    )
    return [format_notification_event_message(result) for result in results]


def _target_notification_test_error_response(
    request: Request,
    *,
    error_message: str,
    return_to: str,
    status_code: int,
) -> JSONResponse | RedirectResponse:
    """依 Accept header 回傳 target 測試通知的 JSON 或 redirect 錯誤。"""

    if _wants_json_response(request):
        return JSONResponse(
            {"ok": False, "error": error_message},
            status_code=status_code,
        )
    return redirect_with_error(error_message, return_to=return_to)


def _target_notification_test_success_response(
    request: Request,
    *,
    localized_results: list[str],
    return_to: str,
) -> JSONResponse | RedirectResponse:
    """依 Accept header 回傳 target 測試通知成功結果。"""

    message = "測試通知結果：" + " / ".join(localized_results)
    if _wants_json_response(request):
        return JSONResponse({"ok": True, "message": message, "results": localized_results})
    return redirect_with_message(message, return_to=return_to)


def register_target_notification_routes(app: FastAPI) -> None:
    """註冊 target 測試通知 route。"""

    @app.post("/targets/{target_id}/notifications/test")
    async def test_target_notifications(
        request: Request,
        target_id: str,
        notification_form: Annotated[
            NotificationConfigForm,
            Depends(NotificationConfigForm.as_form),
        ],
        return_to: Annotated[str, Form()] = "",
    ) -> object:
        """依 target 設定 modal 目前欄位送出一則測試通知，不保存設定。"""

        try:
            localized_results = await _send_target_test_notifications(
                request,
                target_id=target_id,
                notification_form=notification_form,
            )
        except _TargetNotificationTestNotFound:
            return _target_notification_test_error_response(
                request,
                error_message="測試通知失敗: target 不存在",
                return_to=return_to,
                status_code=404,
            )
        except ValueError as exc:
            error_message = "測試通知失敗: " + format_notification_form_error(exc)
            return _target_notification_test_error_response(
                request,
                error_message=error_message,
                return_to=return_to,
                status_code=400,
            )
        except Exception as exc:
            error_message = (
                "測試通知失敗: "
                + format_notification_event_message(
                    safe_exception_message("notification_test_failed", exc)
                )
            )
            return _target_notification_test_error_response(
                request,
                error_message=error_message,
                return_to=return_to,
                status_code=400,
            )
        return _target_notification_test_success_response(
            request,
            localized_results=localized_results,
            return_to=return_to,
        )
