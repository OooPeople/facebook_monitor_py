"""FastAPI Web UI tests。"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from httpx import Response as HttpResponse
from starlette.requests import Request
from starlette.responses import Response

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.keyword_text import parse_keywords_text
from facebook_monitor.webapp.app import create_app as create_production_app
from facebook_monitor.webapp.assets import ASSET_VERSION
from facebook_monitor.webapp.dependencies import TEMPLATES_DIR
from facebook_monitor.webapp.http_security import RequestBodyTooLarge
from facebook_monitor.webapp.http_security import _read_request_body_with_limit
from facebook_monitor.webapp.maintenance import BoundedRetentionMaintenanceRunner
from facebook_monitor.webapp.dependencies import redirect_with_error
from facebook_monitor.webapp.scheduler_session import SchedulerSessionOptions
from facebook_monitor.version import APP_VERSION
from tests.helpers.webapp import FakeSchedulerManager
from tests.webapp.app_test_helpers import create_app


class FakeBoundedRetentionRunner:
    """測試用 maintenance runner，確認 request 只觸發、不直接跑 cleanup。"""

    def __init__(self) -> None:
        self.triggered_paths: list[Path] = []

    def trigger(self, db_path: Path) -> bool:
        """記錄觸發路徑。"""

        self.triggered_paths.append(db_path)
        return True


class SchemaAwareBoundedRetentionRunner:
    """記錄 maintenance 觸發時 app DB 是否已完成 schema 初始化。"""

    def __init__(self) -> None:
        self.observed_app_settings_table: list[bool] = []

    def trigger(self, db_path: Path) -> bool:
        """以唯讀方式確認 request read path 已先建立 schema。"""

        with SqliteApplicationContext(db_path, initialize_schema_on_enter=False) as app_context:
            row = app_context.repositories.targets.connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table'
                    AND name = 'app_settings'
                """
            ).fetchone()
        self.observed_app_settings_table.append(row is not None)
        return True


class FakeDashboardRevisionNotifier:
    """測試用 dashboard revision notifier。"""

    def __init__(self) -> None:
        self.started_count = 0
        self.stopped_count = 0
        self.woken_count = 0

    async def start(self) -> None:
        """記錄 lifespan startup。"""

        self.started_count += 1

    async def stop(self) -> None:
        """記錄 lifespan shutdown。"""

        self.stopped_count += 1

    def wake(self) -> None:
        """記錄 mutation wake hook。"""

        self.woken_count += 1


def assert_basic_security_headers(response: HttpResponse) -> None:
    """確認本機 Web UI response 帶基本瀏覽器安全 header。"""

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
    assert_basic_security_headers(response)


def test_static_and_error_responses_include_basic_security_headers(
    tmp_path: Path,
) -> None:
    """Static 與 middleware error response 也需經過 security middleware。"""

    client = TestClient(
        create_production_app(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
            csrf_token="known-token",
            max_request_body_bytes=128,
        )
    )

    static_response = client.get("/static/dashboard/sidebar.js")
    csrf_response = client.post(
        "/settings/target-keywords",
        data={"exclude_keywords": "售完"},
        follow_redirects=False,
    )
    body_limit_response = client.post(
        "/settings/target-keywords",
        data={"csrf_token": "known-token", "exclude_keywords": "x" * 256},
        follow_redirects=False,
    )

    assert static_response.status_code == 200
    assert csrf_response.status_code == 403
    assert body_limit_response.status_code == 413
    assert_basic_security_headers(static_response)
    assert_basic_security_headers(csrf_response)
    assert_basic_security_headers(body_limit_response)


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


def test_create_app_uses_explicit_template_resource_dir(tmp_path: Path) -> None:
    """launcher 傳入的 templates dir 應成為 Web UI 實際 render 來源。"""

    db_path = tmp_path / "app.db"
    templates_dir = tmp_path / "templates"
    shutil.copytree(TEMPLATES_DIR, templates_dir)
    index_template = templates_dir / "index.html"
    index_template.write_text(
        index_template.read_text(encoding="utf-8") + "\n<!-- custom-template-dir -->\n",
        encoding="utf-8",
    )
    app = create_app(
        db_path=db_path,
        profile_dir=tmp_path / "profile",
        templates_dir=templates_dir,
    )
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "custom-template-dir" in response.text
    assert app.state.templates_dir == templates_dir


def test_create_app_auto_starts_scheduler_with_configured_options(
    tmp_path: Path,
) -> None:
    """Web UI lifespan 需用 factory 參數啟動注入的 scheduler manager。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    scheduler_manager = FakeSchedulerManager()
    app = create_app(
        db_path=db_path,
        profile_dir=profile_dir,
        scheduler_manager=scheduler_manager,
        auto_start_scheduler=True,
        scheduler_interval_seconds=37,
        scheduler_tick_seconds=2.5,
        max_concurrent_scans=3,
    )

    with TestClient(app):
        assert scheduler_manager.started_count == 1
        assert scheduler_manager.options is not None
        assert scheduler_manager.options.db_path == db_path
        assert scheduler_manager.options.profile_dir == profile_dir
        assert scheduler_manager.options.interval_seconds == 37
        assert scheduler_manager.options.scheduler_tick_seconds == 2.5
        assert scheduler_manager.options.max_concurrent_scans == 3

    assert scheduler_manager.stopped_count == 1


def test_webui_lifespan_starts_and_stops_dashboard_revision_notifier(
    tmp_path: Path,
) -> None:
    """Web UI lifespan 會啟停 process-local dashboard revision notifier。"""

    notifier = FakeDashboardRevisionNotifier()
    app = create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile")
    app.state.dashboard_revision_notifier = notifier

    with TestClient(app):
        assert notifier.started_count == 1
        assert notifier.stopped_count == 0

    assert notifier.stopped_count == 1


def test_webui_shutdown_stops_dashboard_notifier_before_other_runtime(
    tmp_path: Path,
) -> None:
    """shutdown 第一個步驟先停止 SSE notifier，避免 active stream 卡住收尾。"""

    events: list[str] = []

    class OrderedNotifier(FakeDashboardRevisionNotifier):
        async def start(self) -> None:
            self.started_count += 1

        async def stop(self) -> None:
            events.append("notifier.stop")
            self.stopped_count += 1

    class OrderedRunner:
        async def wait_until_idle(self) -> None:
            events.append("retention.wait")

    class OrderedProfileManager:
        def is_active(self) -> bool:
            return False

        def open(self, _options: object) -> None:
            raise AssertionError("profile open should not be called")

        def close(self) -> None:
            events.append("profile.close")

    class OrderedSchedulerManager(FakeSchedulerManager):
        def stop(self) -> None:
            events.append("scheduler.stop")
            super().stop()

    app = create_app(
        db_path=tmp_path / "app.db",
        profile_dir=tmp_path / "profile",
        profile_manager=OrderedProfileManager(),
        scheduler_manager=OrderedSchedulerManager(),
    )
    app.state.dashboard_revision_notifier = OrderedNotifier()
    app.state.bounded_retention_maintenance_runner = OrderedRunner()

    with TestClient(app):
        pass

    assert events == [
        "notifier.stop",
        "retention.wait",
        "profile.close",
        "scheduler.stop",
    ]


def test_webui_startup_failure_after_notifier_start_stops_notifier(
    tmp_path: Path,
) -> None:
    """notifier 啟動後若 scheduler startup 失敗，lifespan 仍需明確收尾。"""

    class FailingSchedulerManager(FakeSchedulerManager):
        def start(self, options: SchedulerSessionOptions) -> None:
            super().start(options)
            raise RuntimeError("scheduler startup failed")

    notifier = FakeDashboardRevisionNotifier()
    scheduler_manager = FailingSchedulerManager()
    app = create_app(
        db_path=tmp_path / "app.db",
        profile_dir=tmp_path / "profile",
        scheduler_manager=scheduler_manager,
        auto_start_scheduler=True,
    )
    app.state.dashboard_revision_notifier = notifier

    with pytest.raises(RuntimeError, match="scheduler startup failed"):
        with TestClient(app):
            pass

    assert notifier.started_count == 1
    assert notifier.stopped_count == 1
    assert scheduler_manager.stopped_count == 1


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


def test_health_endpoint_does_not_run_bounded_retention_maintenance(
    tmp_path: Path,
) -> None:
    """Health check 只供 launcher 探測，不應有 housekeeping 副作用。"""

    db_path = tmp_path / "app.db"
    app = create_app(db_path=db_path, profile_dir=tmp_path / "profile")
    runner = FakeBoundedRetentionRunner()
    app.state.bounded_retention_maintenance_runner = runner
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert runner.triggered_paths == []


def test_successful_unsafe_requests_wake_dashboard_revision_notifier(
    tmp_path: Path,
) -> None:
    """成功的 unsafe HTTP method 會 wake dashboard revision watcher；GET/4xx 不會。"""

    notifier = FakeDashboardRevisionNotifier()
    app = create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile")
    app.state.dashboard_revision_notifier = notifier

    @app.get("/__wake_probe")
    async def wake_probe_get() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/__wake_probe")
    @app.put("/__wake_probe")
    @app.patch("/__wake_probe")
    @app.delete("/__wake_probe")
    async def wake_probe_mutation() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/__wake_rejected")
    async def wake_probe_rejected() -> Response:
        return Response(status_code=400)

    with TestClient(app) as client:
        assert client.get("/__wake_probe").status_code == 200
        assert notifier.woken_count == 0

        for method in (client.post, client.put, client.patch, client.delete):
            assert method("/__wake_probe").status_code == 200
        assert notifier.woken_count == 4

        assert client.post("/__wake_rejected").status_code == 400
        assert notifier.woken_count == 4


def test_web_ui_read_path_runs_bounded_retention_maintenance(
    tmp_path: Path,
) -> None:
    """主要 Web UI read path 只觸發 background bounded retention runner。"""

    db_path = tmp_path / "app.db"
    app = create_app(db_path=db_path, profile_dir=tmp_path / "profile")
    runner = FakeBoundedRetentionRunner()
    app.state.bounded_retention_maintenance_runner = runner
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert runner.triggered_paths == [db_path]


def test_web_ui_read_path_triggers_maintenance_after_schema_initialization(
    tmp_path: Path,
) -> None:
    """首次讀空 DB 時，housekeeping 不可搶在 dashboard schema 初始化前執行。"""

    db_path = tmp_path / "app.db"
    app = create_app(db_path=db_path, profile_dir=tmp_path / "profile")
    runner = SchemaAwareBoundedRetentionRunner()
    app.state.bounded_retention_maintenance_runner = runner
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert runner.observed_app_settings_table == [True]


def test_settings_read_path_runs_bounded_retention_maintenance(
    tmp_path: Path,
) -> None:
    """Settings read path 仍是明確 housekeeping background trigger。"""

    db_path = tmp_path / "app.db"
    app = create_app(db_path=db_path, profile_dir=tmp_path / "profile")
    runner = FakeBoundedRetentionRunner()
    app.state.bounded_retention_maintenance_runner = runner
    client = TestClient(app)

    response = client.get("/settings")

    assert response.status_code == 200
    assert runner.triggered_paths == [db_path]


def test_bounded_retention_runner_does_not_schedule_duplicate_task(
    tmp_path: Path,
) -> None:
    """同一個 Web app process 內已有 cleanup task 時不重複排程。"""

    calls: list[Path] = []

    def fake_maintenance(db_path: Path) -> int:
        calls.append(db_path)
        return 0

    async def run_test() -> None:
        runner = BoundedRetentionMaintenanceRunner(fake_maintenance)
        db_path = tmp_path / "app.db"

        first = runner.trigger(db_path)
        second = runner.trigger(db_path)
        while runner.running:
            await asyncio.sleep(0)

        assert first is True
        assert second is False
        assert calls == [db_path]

    asyncio.run(run_test())


def test_bounded_retention_runner_swallows_background_failure(
    tmp_path: Path,
) -> None:
    """background cleanup 失敗不可外溢到 request task。"""

    def failing_maintenance(_db_path: Path) -> int:
        raise RuntimeError("database locked")

    async def run_test() -> None:
        runner = BoundedRetentionMaintenanceRunner(failing_maintenance)

        assert runner.trigger(tmp_path / "app.db") is True
        while runner.running:
            await asyncio.sleep(0)
        assert not runner.running

    asyncio.run(run_test())


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
