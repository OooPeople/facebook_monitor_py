"""Web UI static asset mounting。

職責：提供本機 Web UI 靜態檔的 cache policy 與 mount helper。
"""

from __future__ import annotations

from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope


class LocalStaticFiles(StaticFiles):
    """本機 Web UI 靜態檔，每次瀏覽器重整都應重新驗證。"""

    async def get_response(self, path: str, scope: Scope) -> Response:
        """回傳 static response，避免 ES module 長時間沿用舊版。"""

        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


__all__ = ["LocalStaticFiles"]
