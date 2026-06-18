"""Dashboard revision wake middleware。

職責：在 Web UI mutation response 成功後喚醒 revision watcher，加速 dashboard
partial update；wake 本身不直接產生 SSE event。
"""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable

from fastapi import FastAPI
from fastapi import Request
from starlette.responses import Response


DASHBOARD_REVISION_WAKE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def register_dashboard_revision_wake_middleware(app: FastAPI) -> None:
    """註冊成功 mutation 後的 dashboard revision watcher wake hook。"""

    @app.middleware("http")
    async def dashboard_revision_wake_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """成功 unsafe request 後喚醒 watcher；GET/read path 不參與。"""

        response = await call_next(request)
        if (
            request.method.upper() in DASHBOARD_REVISION_WAKE_METHODS
            and response.status_code < 400
        ):
            request.app.state.dashboard_revision_notifier.wake()
        return response


__all__ = [
    "DASHBOARD_REVISION_WAKE_METHODS",
    "register_dashboard_revision_wake_middleware",
]
