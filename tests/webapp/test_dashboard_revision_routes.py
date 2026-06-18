"""FastAPI Web UI tests。"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
from pytest import MonkeyPatch
from fastapi.testclient import TestClient
import uvicorn

from facebook_monitor.application import context as application_context
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.webapp.dashboard_queries import list_sidebar_items
from facebook_monitor.webapp.dashboard_read_models import DashboardReadUnavailable
from facebook_monitor.webapp.dashboard_read_models import DashboardRevision
from facebook_monitor.webapp.dashboard_read_models import DashboardRevisionUnavailable
from facebook_monitor.webapp.dashboard_revision_notifier import DashboardRevisionNotifier
from facebook_monitor.webapp.dashboard_revision_query import get_dashboard_revision
from facebook_monitor.webapp.maintenance import BOUNDED_RETENTION_MAINTENANCE_READ_PATHS
from facebook_monitor.webapp.routes import dashboard as dashboard_routes
from facebook_monitor.webapp.routes.dashboard import _format_dashboard_revision_event
from facebook_monitor.webapp.routes.dashboard_revision_routes import (
    build_dashboard_revision_sse_response,
)
from facebook_monitor.webapp.routes.dashboard_revision_routes import dashboard_revision_event_stream


from tests.webapp.app_test_helpers import create_app


class FakeSseRequest:
    """測試用 request，只提供 SSE stream 需要的 disconnect probe。"""

    async def is_disconnected(self) -> bool:
        """測試期間保持連線。"""

        return False


class DisconnectAfterChecksRequest:
    """測試用 request，在指定 probe 次數後回報 client 已斷線。"""

    def __init__(self, *, connected_checks: int) -> None:
        self.connected_checks = connected_checks
        self.check_count = 0

    async def is_disconnected(self) -> bool:
        """前幾次維持連線，之後模擬 uvicorn shutdown/client disconnect。"""

        self.check_count += 1
        return self.check_count > self.connected_checks


class FiniteDashboardRevisionNotifier:
    """測試 route wiring 用的有限 notifier。"""

    def __init__(self) -> None:
        self.started_count = 0
        self.stopped_count = 0
        self.subscriber_count = 0

    async def start(self) -> None:
        """記錄 lifespan startup。"""

        self.started_count += 1

    async def stop(self) -> None:
        """記錄 lifespan shutdown。"""

        self.stopped_count += 1

    def wake(self) -> None:
        """route wiring 測試不需要 wake side effect。"""

    async def subscribe(self):
        """送出一筆 revision 後結束，避免 TestClient 等待無限 stream。"""

        self.subscriber_count += 1
        try:
            yield DashboardRevision(
                revision="rev-route",
                last_changed_at="2026-06-18T00:00:00",
            )
        finally:
            self.subscriber_count -= 1


def test_dashboard_revision_endpoint_changes_after_target_update(tmp_path: Path) -> None:
    """dashboard revision endpoint 只在資料有變更時供前端刷新。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    first_revision = client.get("/api/dashboard-revision").json()["revision"]
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "return_to": f"#target-{target.id}",
            "include_keywords": "票券",
            "exclude_keywords": "",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )
    second_revision = client.get("/api/dashboard-revision").json()["revision"]

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"#target-{target.id}")
    assert first_revision != second_revision


def test_dashboard_revision_read_path_does_not_initialize_schema(tmp_path: Path) -> None:
    """SSE revision read path 不應為空 DB 建立 schema 或檔案。"""

    db_path = tmp_path / "app.db"

    revision = get_dashboard_revision(db_path)

    assert revision.revision == "0"
    assert not db_path.exists()


def test_dashboard_sidebar_read_path_does_not_initialize_schema(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Sidebar partial update read path 不應在掃描寫入期間重跑 schema init。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("dashboard read path should not initialize schema")

    monkeypatch.setattr(application_context, "initialize_schema", fail_if_called)

    items = list_sidebar_items(db_path)

    assert len(items) == 1


def test_dashboard_revision_endpoint_ignores_temporary_sqlite_lock(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """dashboard polling endpoint 遇到短暫 DB lock 時回 503，前端可忽略該輪。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path):
        pass

    def raise_locked(*args: object, **kwargs: object) -> object:
        raise DashboardRevisionUnavailable("database is locked")

    monkeypatch.setattr(dashboard_routes, "get_dashboard_revision", raise_locked)
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get("/api/dashboard-revision")

    assert response.status_code == 503


def test_dashboard_page_uses_dashboard_route_view_loader_seam(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Dashboard page read model seam 留在 routes.dashboard，避免 split 後失控。"""

    def raise_locked(*args: object, **kwargs: object) -> object:
        raise DashboardReadUnavailable("database is locked")

    monkeypatch.setattr(dashboard_routes, "get_dashboard_view", raise_locked)
    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.get("/")

    assert response.status_code == 503


def test_dashboard_sidebar_endpoint_ignores_temporary_sqlite_lock(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Sidebar partial update 遇到短暫 DB lock 時回 503，避免 ASGI traceback。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path):
        pass

    def raise_locked(*args: object, **kwargs: object) -> object:
        raise DashboardReadUnavailable("database is locked")

    monkeypatch.setattr(dashboard_routes, "list_sidebar_items", raise_locked)
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get("/api/sidebar")

    assert response.status_code == 503


def test_dashboard_card_uses_dashboard_route_target_card_loader_seam(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Target card partial read model seam 留在 routes.dashboard。"""

    def raise_locked(*args: object, **kwargs: object) -> object:
        raise DashboardReadUnavailable("database is locked")

    monkeypatch.setattr(dashboard_routes, "get_target_card", raise_locked)
    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.get("/api/targets/missing/card")

    assert response.status_code == 503


def test_dashboard_events_streams_revision_event(tmp_path: Path) -> None:
    """dashboard event stream endpoint 與 event 格式會提供 revision event。"""

    db_path = tmp_path / "app.db"
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    openapi = client.get("/openapi.json").json()
    event_text = _format_dashboard_revision_event(
        {"revision": "rev-1", "last_changed_at": "2026-05-08T00:00:00"}
    )

    assert "/api/dashboard-events" in openapi["paths"]
    assert event_text.startswith("id: rev-1\nevent: dashboard_revision\n")
    assert event_text.endswith("\n\n")
    data_line = next(line for line in event_text.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload == {"revision": "rev-1", "last_changed_at": "2026-05-08T00:00:00"}


def test_dashboard_events_route_uses_app_state_notifier_with_lifespan(
    tmp_path: Path,
) -> None:
    """實際 ASGI route 需從 app.state 取 notifier，並在 TestClient teardown 清理。"""

    app = create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile")
    notifier = FiniteDashboardRevisionNotifier()
    app.state.dashboard_revision_notifier = notifier

    with TestClient(app) as client:
        response = client.get("/api/dashboard-events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.startswith("retry: 2500\n\n")
    assert "id: rev-route\nevent: dashboard_revision\n" in response.text
    assert '"revision":"rev-route"' in response.text
    assert notifier.started_count == 1
    assert notifier.stopped_count == 1
    assert notifier.subscriber_count == 0


def test_dashboard_events_stream_sends_retry_and_initial_revision(tmp_path: Path) -> None:
    """長 SSE stream 開頭送 retry，已有真 revision 時送 initial event。"""

    async def run_test() -> None:
        notifier = DashboardRevisionNotifier(
            db_path=tmp_path / "app.db",
            get_dashboard_revision=lambda _path: DashboardRevision(
                revision="rev-1",
                last_changed_at="2026-06-18T00:00:00",
            ),
            poll_interval_seconds=1.0,
        )
        stream = dashboard_revision_event_stream(
            FakeSseRequest(),
            notifier=notifier,
            format_revision_event=_format_dashboard_revision_event,
            keepalive_seconds=0.05,
            retry_milliseconds=2500,
        )
        try:
            retry = await asyncio.wait_for(anext(stream), timeout=0.5)
            event = await asyncio.wait_for(anext(stream), timeout=0.5)
        finally:
            await stream.aclose()
            await notifier.stop()

        assert retry == "retry: 2500\n\n"
        assert event.startswith("id: rev-1\nevent: dashboard_revision\n")
        assert event.endswith("\n\n")

    asyncio.run(run_test())


def test_dashboard_events_stream_initial_failure_sends_keepalive_not_fake_revision(
    tmp_path: Path,
) -> None:
    """initial revision 讀取失敗時不送 revision=0 event，只送 keepalive。"""

    def raise_locked(_path: Path) -> DashboardRevision:
        raise DashboardRevisionUnavailable("database is locked")

    async def run_test() -> None:
        notifier = DashboardRevisionNotifier(
            db_path=tmp_path / "app.db",
            get_dashboard_revision=raise_locked,
            poll_interval_seconds=1.0,
        )
        stream = dashboard_revision_event_stream(
            FakeSseRequest(),
            notifier=notifier,
            format_revision_event=_format_dashboard_revision_event,
            keepalive_seconds=0.01,
            retry_milliseconds=2500,
        )
        try:
            retry = await asyncio.wait_for(anext(stream), timeout=0.5)
            keepalive = await asyncio.wait_for(anext(stream), timeout=0.5)
        finally:
            await stream.aclose()
            await notifier.stop()

        assert retry == "retry: 2500\n\n"
        assert keepalive == ": keepalive\n\n"
        assert "dashboard_revision" not in keepalive
        assert "revision" not in keepalive

    asyncio.run(run_test())


def test_dashboard_events_stream_disconnect_does_not_wait_for_keepalive(
    tmp_path: Path,
) -> None:
    """client/shutdown disconnect 應短時間結束，不等待長 keepalive interval。"""

    def raise_locked(_path: Path) -> DashboardRevision:
        raise DashboardRevisionUnavailable("database is locked")

    async def run_test() -> None:
        request = DisconnectAfterChecksRequest(connected_checks=1)
        notifier = DashboardRevisionNotifier(
            db_path=tmp_path / "app.db",
            get_dashboard_revision=raise_locked,
            poll_interval_seconds=60.0,
        )
        stream = dashboard_revision_event_stream(
            request,
            notifier=notifier,
            format_revision_event=_format_dashboard_revision_event,
            keepalive_seconds=60.0,
            retry_milliseconds=2500,
            disconnect_poll_seconds=0.01,
        )
        try:
            assert await asyncio.wait_for(anext(stream), timeout=0.5) == "retry: 2500\n\n"
            try:
                await asyncio.wait_for(anext(stream), timeout=0.2)
            except StopAsyncIteration:
                pass
            else:
                raise AssertionError("stream should finish after disconnect")
        finally:
            await stream.aclose()
            await notifier.stop()

        assert request.check_count >= 2

    asyncio.run(run_test())


def test_dashboard_events_active_stream_shutdown_signal_finishes_before_uvicorn_timeout(
    tmp_path: Path,
) -> None:
    """pre-shutdown signal 會讓 active SSE 在 uvicorn graceful timeout 前結束。"""

    async def run_test() -> None:
        app = create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile")
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="error",
            timeout_graceful_shutdown=1,
            lifespan="on",
        )
        server = uvicorn.Server(config)
        server_task = asyncio.create_task(server.serve())
        try:
            while not server.started:
                await asyncio.sleep(0.01)
            if not server.servers:
                raise AssertionError("uvicorn server did not expose sockets")
            port = int(server.servers[0].sockets[0].getsockname()[1])
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "GET",
                    f"http://127.0.0.1:{port}/api/dashboard-events",
                ) as response:
                    assert response.status_code == 200
                    lines = response.aiter_lines()
                    assert await asyncio.wait_for(anext(lines), timeout=2.0) == (
                        "retry: 2500"
                    )
                    started = time.perf_counter()
                    app.state.dashboard_revision_notifier.request_stop()
                    server.should_exit = True
                    await asyncio.wait_for(server_task, timeout=2.0)
                    elapsed = time.perf_counter() - started
        finally:
            server.should_exit = True
            if not server_task.done():
                await server_task

        assert elapsed < 1.0

    asyncio.run(run_test())


def test_dashboard_events_stream_sends_revision_change(tmp_path: Path) -> None:
    """notifier 讀到 revision 變更時，長 SSE stream 送 dashboard_revision event。"""

    state = {"revision": "rev-1"}

    def load_revision(_path: Path) -> DashboardRevision:
        return DashboardRevision(
            revision=state["revision"],
            last_changed_at="2026-06-18T00:00:00",
        )

    async def run_test() -> None:
        notifier = DashboardRevisionNotifier(
            db_path=tmp_path / "app.db",
            get_dashboard_revision=load_revision,
            poll_interval_seconds=1.0,
        )
        await notifier.start()
        stream = dashboard_revision_event_stream(
            FakeSseRequest(),
            notifier=notifier,
            format_revision_event=_format_dashboard_revision_event,
            keepalive_seconds=0.05,
            retry_milliseconds=2500,
        )
        try:
            await asyncio.wait_for(anext(stream), timeout=0.5)
            assert "rev-1" in await asyncio.wait_for(anext(stream), timeout=0.5)
            state["revision"] = "rev-2"
            notifier.wake()
            event = await asyncio.wait_for(anext(stream), timeout=0.5)
        finally:
            await stream.aclose()
            await notifier.stop()

        assert event.startswith("id: rev-2\nevent: dashboard_revision\n")

    asyncio.run(run_test())


def test_dashboard_events_stream_ends_when_notifier_stops(tmp_path: Path) -> None:
    """notifier stop 會喚醒 stream generator 結束，避免 shutdown / teardown 卡住。"""

    async def run_test() -> None:
        notifier = DashboardRevisionNotifier(
            db_path=tmp_path / "app.db",
            get_dashboard_revision=lambda _path: DashboardRevision(revision="rev-1"),
            poll_interval_seconds=1.0,
        )
        await notifier.start()
        stream = dashboard_revision_event_stream(
            FakeSseRequest(),
            notifier=notifier,
            format_revision_event=_format_dashboard_revision_event,
            keepalive_seconds=0.05,
            retry_milliseconds=2500,
        )
        try:
            assert await asyncio.wait_for(anext(stream), timeout=0.5) == "retry: 2500\n\n"
            assert "rev-1" in await asyncio.wait_for(anext(stream), timeout=0.5)
            await notifier.stop()
            try:
                await asyncio.wait_for(anext(stream), timeout=0.5)
            except StopAsyncIteration:
                pass
            else:
                raise AssertionError("stream should finish after notifier stop")
        finally:
            await stream.aclose()
            await notifier.stop()

    asyncio.run(run_test())


def test_dashboard_events_response_headers_and_no_maintenance(tmp_path: Path) -> None:
    """SSE response header 符合長 SSE，且 route 不觸發 bounded retention。"""

    notifier = DashboardRevisionNotifier(
        db_path=tmp_path / "app.db",
        get_dashboard_revision=lambda _path: DashboardRevision(revision="rev-1"),
        poll_interval_seconds=1.0,
    )

    response = build_dashboard_revision_sse_response(
        FakeSseRequest(),
        notifier=notifier,
        format_revision_event=_format_dashboard_revision_event,
    )

    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["connection"] == "keep-alive"
    assert "/api/dashboard-events" not in BOUNDED_RETENTION_MAINTENANCE_READ_PATHS
