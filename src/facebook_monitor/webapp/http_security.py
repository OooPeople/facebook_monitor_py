"""Web UI HTTP security middleware。

職責：集中 CSRF、request body size limit 與本機管理 UI 的安全 response headers。
"""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from secrets import compare_digest
from urllib.parse import parse_qs

from fastapi import FastAPI
from fastapi import Request
from starlette.responses import Response


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
LOCAL_UI_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: https://fbcdn.net https://*.fbcdn.net "
        "https://fbsbx.com https://*.fbsbx.com https://facebook.com https://*.facebook.com",
        "connect-src 'self'",
        "font-src 'self'",
        "form-action 'self'",
        "frame-src 'none'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    )
)


class RequestBodyTooLarge(Exception):
    """HTTP request body 超過本機管理 UI 可接受上限。"""


def register_http_security_middleware(app: FastAPI) -> None:
    """註冊 CSRF/body-limit/security-header middleware。"""

    @app.middleware("http")
    async def csrf_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """保護本機管理 UI 的 mutating routes，避免跨站表單直接操作 localhost。"""

        try:
            request_body = await _read_request_body_with_limit(
                request,
                max_bytes=int(getattr(request.app.state, "max_request_body_bytes")),
            )
            if request_body is not None:
                request = _replay_request_body(request, request_body)
            if _should_validate_csrf(request):
                submitted_token = request.headers.get(CSRF_HEADER, "").strip()
                if not submitted_token:
                    submitted_token = _submitted_csrf_token_from_body(
                        request,
                        request_body or b"",
                    )
                expected_token = str(getattr(request.app.state, "csrf_token", ""))
                if not submitted_token or not compare_digest(submitted_token, expected_token):
                    return _with_security_headers(
                        Response("CSRF validation failed", status_code=403)
                    )
            response = await call_next(request)
        except RequestBodyTooLarge:
            return _with_security_headers(
                Response("Request body too large", status_code=413)
            )
        return _with_security_headers(response)


def _should_validate_csrf(request: Request) -> bool:
    """判斷目前 request 是否需要 CSRF token。"""

    if request.method.upper() not in UNSAFE_METHODS:
        return False
    if not bool(getattr(request.app.state, "enforce_csrf", True)):
        return False
    return True


def _with_security_headers(response: Response) -> Response:
    """加上本機 Web UI 的基本安全 header。"""

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy",
        LOCAL_UI_CONTENT_SECURITY_POLICY,
    )
    return response


def _submitted_csrf_token_from_body(request: Request, body: bytes) -> str:
    """從已讀取的 urlencoded body 解析 CSRF token。"""

    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        decoded_body = body.decode("utf-8", errors="replace")
        values = parse_qs(decoded_body).get(CSRF_FORM_FIELD, [])
        return str(values[0]).strip() if values else ""
    return ""


async def _read_request_body_with_limit(
    request: Request,
    *,
    max_bytes: int,
) -> bytes | None:
    """在進入 route 前讀取並限制 request body；無 body 時回傳 None。"""

    limit = max(1, int(max_bytes))
    content_length = request.headers.get("content-length", "").strip()
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = 0
        if declared_size > limit:
            raise RequestBodyTooLarge
    if not _request_may_have_body(request, content_length=content_length):
        return None
    chunks: list[bytes] = []
    received_bytes = 0
    while True:
        message = await request.receive()
        if message.get("type") == "http.request":
            body = message.get("body", b"")
            if isinstance(body, bytes):
                chunks.append(body)
                received_bytes += len(body)
            if received_bytes > limit:
                raise RequestBodyTooLarge
            if not bool(message.get("more_body", False)):
                break
        elif message.get("type") == "http.disconnect":
            break
    body = b"".join(chunks)
    setattr(request, "_body", body)
    return body


def _request_may_have_body(request: Request, *, content_length: str) -> bool:
    """判斷是否需要預先讀 body 才能套用大小限制與 replay。"""

    if content_length and content_length != "0":
        return True
    return request.method.upper() not in {"GET", "HEAD", "OPTIONS", "TRACE"}


def _replay_request_body(request: Request, body: bytes) -> Request:
    """重建 request receive，避免 middleware 讀 body 後 route 讀不到 form。"""

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(request.scope, receive)


__all__ = [
    "CSRF_FORM_FIELD",
    "CSRF_HEADER",
    "LOCAL_UI_CONTENT_SECURITY_POLICY",
    "RequestBodyTooLarge",
    "register_http_security_middleware",
]
