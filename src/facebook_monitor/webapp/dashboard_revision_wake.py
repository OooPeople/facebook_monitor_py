"""Dashboard revision wake middleware。

職責：在 Web UI mutation response 成功後喚醒 revision watcher，加速 dashboard
partial update；wake 本身不直接產生 SSE event。
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.types import ASGIApp
from starlette.types import Message
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send


DASHBOARD_REVISION_WAKE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class DashboardRevisionWakeMiddleware:
    """在成功 mutation response 開始送出時喚醒 dashboard revision watcher。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """套用 pure ASGI middleware，避免長 SSE 被 BaseHTTPMiddleware 包住。"""

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "")).upper()
        should_observe = method in DASHBOARD_REVISION_WAKE_METHODS
        woken = False

        async def send_with_wake(message: Message) -> None:
            nonlocal woken
            if (
                should_observe
                and not woken
                and message["type"] == "http.response.start"
                and int(message.get("status", 500)) < 400
            ):
                woken = True
                scope["app"].state.dashboard_revision_notifier.wake()
            await send(message)

        await self.app(scope, receive, send_with_wake)


def register_dashboard_revision_wake_middleware(app: FastAPI) -> None:
    """註冊成功 mutation 後的 dashboard revision watcher wake hook。"""

    app.add_middleware(DashboardRevisionWakeMiddleware)


__all__ = [
    "DASHBOARD_REVISION_WAKE_METHODS",
    "DashboardRevisionWakeMiddleware",
    "register_dashboard_revision_wake_middleware",
]
