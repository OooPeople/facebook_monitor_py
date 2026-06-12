"""Sidebar route shared helpers."""

from __future__ import annotations

from fastapi import HTTPException

from facebook_monitor.webapp.sidebar_api import sidebar_error_detail


def sidebar_bad_request(exc: ValueError) -> HTTPException:
    """將 sidebar application 錯誤轉成安全、可顯示的繁中 API 訊息。"""

    return HTTPException(status_code=400, detail=sidebar_error_detail(exc))
