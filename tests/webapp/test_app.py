"""FastAPI Web UI tests。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from starlette.requests import Request

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import UpsertCommentsTargetRequest
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.webapp.app import create_app as create_production_app
from facebook_monitor.webapp.app import RequestBodyTooLarge
from facebook_monitor.webapp.app import _read_request_body_with_limit
from facebook_monitor.webapp.app import parse_keywords_text
from facebook_monitor.webapp.assets import ASSET_VERSION
from facebook_monitor.webapp.dependencies import redirect_with_error
from facebook_monitor.version import APP_VERSION
from tests.webapp.app_test_helpers import create_app


def test_parse_keywords_text_dedupes_and_trims() -> None:
    """Web UI keyword parser 會去除空白與重複值。"""

    assert parse_keywords_text("票, 交換,票,,讓票") == ("票", "交換", "讓票")
    assert parse_keywords_text("徵;收;已售") == ("徵", "收", "已售")


def test_static_assets_revalidate_for_local_ui(tmp_path: Path) -> None:
    """Static JS/CSS 不保留本機快取，避免 sidebar module 沿用舊版。"""

    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.get("/static/dashboard/sidebar.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0, must-revalidate"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"


def test_web_ui_responses_include_basic_security_headers(tmp_path: Path) -> None:
    """本機 Web UI response 仍應帶基本瀏覽器安全 header。"""

    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "img-src 'self' data: https://fbcdn.net https://*.fbcdn.net" in csp
    assert "connect-src 'self'" in csp
    assert "form-action 'self'" in csp
    assert "frame-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "object-src 'none'" in csp
    assert "unsafe-eval" not in csp


def test_redirect_error_redacts_sensitive_values() -> None:
    """錯誤 redirect query 不可包含 webhook token 或本機使用者目錄。"""

    response = redirect_with_error(
        r"失敗：https://discord.com/api/webhooks/123456/very-secret-token "
        r"C:\Users\alice\facebook_monitor_data\logs\error.log"
    )
    error = parse_qs(urlsplit(response.headers["location"]).query)["error"][0]

    assert "very-secret-token" not in error
    assert "alice" not in error
    assert "[已隱藏]" in error
    assert "%USERPROFILE%" in error


def test_create_app_uses_explicit_static_resource_dir(tmp_path: Path) -> None:
    """launcher 傳入的 static dir 應成為 Web UI 實際掛載資源路徑。"""

    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "resource-check.txt").write_text("custom static", encoding="utf-8")
    app = create_app(
        db_path=tmp_path / "app.db",
        profile_dir=tmp_path / "profile",
        static_dir=static_dir,
    )
    client = TestClient(app)

    response = client.get("/static/resource-check.txt")

    assert response.status_code == 200
    assert response.text == "custom static"
    assert app.state.static_dir == static_dir


def test_health_endpoint_returns_app_identity(tmp_path: Path) -> None:
    """Health endpoint 供 launcher 判斷既有 Web UI 是否存活。"""

    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "status": "ok",
        "app": "Facebook Monitor",
        "version": APP_VERSION,
        "asset_version": ASSET_VERSION,
        "python_version": payload["python_version"],
        "packaging_mode": "source",
    }
    assert payload["python_version"]


def test_mutating_routes_require_csrf_token_for_loopback_host(tmp_path: Path) -> None:
    """CSRF middleware 驗 token 後，下游 Form route 仍要讀得到 body。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_production_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            csrf_token="known-token",
        )
    )
    missing_token_response = client.post(
        "/settings/target-keywords",
        data={"exclude_keywords": "售完"},
        headers={"host": "127.0.0.1:4818"},
        follow_redirects=False,
    )
    valid_token_response = client.post(
        "/settings/target-keywords",
        data={"csrf_token": "known-token", "exclude_keywords": "售完"},
        headers={"host": "127.0.0.1:4818"},
        follow_redirects=False,
    )

    assert missing_token_response.status_code == 403
    assert valid_token_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        defaults = app_context.repositories.app_settings.get_target_keyword_defaults()
    assert defaults.exclude_keywords_text == "售完"


def test_mutating_routes_require_csrf_token_for_testserver_host(tmp_path: Path) -> None:
    """TestClient 預設 testserver host 也不能繞過 runtime CSRF 驗證。"""

    client = TestClient(
        create_production_app(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
            csrf_token="known-token",
        )
    )

    response = client.post(
        "/settings/target-keywords",
        data={"exclude_keywords": "售完"},
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_request_body_limit_rejects_large_form_before_route(tmp_path: Path) -> None:
    """本機 Web UI 會在 route 解析 form 前拒絕過大的 body。"""

    db_path = tmp_path / "app.db"
    client = TestClient(
        create_production_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            csrf_token="known-token",
            max_request_body_bytes=32,
        )
    )
    with SqliteApplicationContext(db_path) as app_context:
        original_defaults = app_context.repositories.app_settings.get_target_keyword_defaults()

    response = client.post(
        "/settings/target-keywords",
        data={"csrf_token": "known-token", "exclude_keywords": "x" * 64},
        follow_redirects=False,
    )

    assert response.status_code == 413
    with SqliteApplicationContext(db_path) as app_context:
        defaults = app_context.repositories.app_settings.get_target_keyword_defaults()
    assert defaults.exclude_keywords_text == original_defaults.exclude_keywords_text


def test_request_body_limit_counts_streamed_bytes_without_content_length() -> None:
    """即使沒有 Content-Length，實際讀取 body 也會被限制。"""

    messages = [
        {"type": "http.request", "body": b"1234", "more_body": True},
        {"type": "http.request", "body": b"5678", "more_body": False},
    ]

    async def receive() -> dict[str, object]:
        return messages.pop(0)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
        },
        receive,
    )

    async def read_body() -> None:
        await _read_request_body_with_limit(request, max_bytes=6)

    try:
        asyncio.run(read_body())
    except RequestBodyTooLarge:
        pass
    else:
        raise AssertionError("request body limit should reject streamed bytes")


def test_pages_render_csrf_token_for_forms_and_fetch_headers(tmp_path: Path) -> None:
    """HTML 會把同一個 CSRF token 提供給 form 與前端 fetch 使用。"""

    client = TestClient(
        create_app(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
            csrf_token="known-token",
        )
    )

    response = client.get("/settings")

    assert response.status_code == 200
    assert '<meta name="csrf-token" content="known-token">' in response.text
    assert response.text.count('name="csrf_token" value="known-token"') >= 3
    assert re.search(r'name="csrf_token" value="known-token"', response.text)


def test_sidebar_status_shows_target_mode_chip(tmp_path: Path) -> None:
    """sidebar 副行在狀態與掃描摘要中間顯示貼文/留言 mode chip。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        comments = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="1370511589953459",
                parent_post_id="2772468963091041",
                canonical_url=(
                    "https://www.facebook.com/groups/1370511589953459/posts/2772468963091041"
                ),
                group_name="測試社團",
            )
        )
        posts = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="貼文社團",
            )
        )
        app_context.services.targets.pause_target_monitoring(comments.id)
        app_context.services.targets.pause_target_monitoring(posts.id)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert 'data-sidebar-mode-label="留言"' in response.text
    assert 'data-sidebar-mode-class="comments"' in response.text
    assert (
        'class="sidebar-status-token target-mode-chip sidebar-mode-chip comments">留言</span>'
        in response.text
    )
    assert 'data-sidebar-mode-label="貼文"' in response.text
    assert 'data-sidebar-mode-class="posts"' in response.text
    assert (
        'class="sidebar-status-token target-mode-chip sidebar-mode-chip posts">貼文</span>'
        in response.text
    )
