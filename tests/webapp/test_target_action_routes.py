"""FastAPI Web UI tests。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
import sqlite3
from pathlib import Path
from threading import Event
from urllib.parse import unquote

from pytest import MonkeyPatch
from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.application.scan_recording_service import RecordScanRequest
from facebook_monitor.application.target_actions import (
    restart_sidebar_group_monitoring_action,
)
from facebook_monitor.application.services import TargetApplicationService
from facebook_monitor.core.facebook_temporary_block import FacebookActionKind
from facebook_monitor.core.facebook_temporary_block import FacebookProductOperationKind
from facebook_monitor.core.facebook_temporary_block import FacebookWorkSourceKind
from facebook_monitor.core.facebook_temporary_block import TemporaryBlockFinding
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.webapp.routes import target_actions as target_action_routes
from facebook_monitor.worker import facebook_access_incident
from facebook_monitor.worker.facebook_access_incident import (
    FacebookAccessIncidentOutcomeKind,
)
from tests.helpers.webapp import FakeSchedulerManager


from tests.webapp.app_test_helpers import create_app
from tests.webapp.app_test_helpers import page_feedback


@dataclass(frozen=True)
class _TargetStartIncidentRace:
    """保存Start／incident writer race的durable fixture。"""

    db_path: Path
    profile_dir: Path
    target_id: str
    warning_generation: int
    finding: TemporaryBlockFinding
    incident_at: datetime


def test_scheduler_routes_are_not_public_daily_controls(tmp_path: Path) -> None:
    """Web UI 不再提供全域 scheduler 日常主開關 route。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )

    start_response = client.post("/scheduler/start", follow_redirects=False)
    index_response = client.get("/")
    stop_response = client.post("/scheduler/stop", follow_redirects=False)

    assert start_response.status_code == 404
    assert scheduler_manager.started_count == 0
    assert scheduler_manager.options is None
    assert "背景掃描服務" not in index_response.text
    assert "啟動自動掃描" not in index_response.text
    assert "停止自動掃描" not in index_response.text
    assert stop_response.status_code == 404
    assert scheduler_manager.stopped_count == 0
    assert not scheduler_manager.running


def test_webui_startup_resets_targets_to_stopped(tmp_path: Path) -> None:
    """正式 Web UI 啟動時會停止 target，但不覆蓋浮動刷新設定。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    fixed_refresh_sec=None,
                    min_refresh_sec=25,
                    max_refresh_sec=35,
                    jitter_enabled=True,
                ),
            )
        )

    with TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            reset_targets_on_startup=True,
        )
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "已停止" in response.text
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.runtime_states.get(target.id)
        config = app_context.repositories.configs.get_for_target(target)
    assert loaded is not None
    assert loaded.paused
    assert state is not None
    assert state.desired_state.value == "stopped"
    assert config is not None
    assert config.fixed_refresh_sec is None
    assert config.jitter_enabled
    assert config.min_refresh_sec == 25
    assert config.max_refresh_sec == 35


def test_webui_startup_can_clear_runtime_debug_data(tmp_path: Path) -> None:
    """Web UI 啟動時可清除上一輪 runtime/debug data，保留 target 設定。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="seen-before-startup",
                item_kind=ItemKind.POST,
            )
        )
        app_context.repositories.scan_scope_state.mark_initialized(target.scope_id)
        logical = app_context.repositories.logical_items.mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="seen-before-startup",
                item_kind=ItemKind.POST,
            ),
            item_keys=("seen-before-startup",),
        )
        app_context.repositories.notification_dedupe.reserve_match(
            target_id=target.id,
            logical_item_id=logical.logical_item_id,
            item_key="seen-before-startup",
            item_kind=ItemKind.POST,
            channel=NotificationChannel.NTFY,
        )
        app_context.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:seen-before-startup:ntfy",
                target_id=target.id,
                item_key="seen-before-startup",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )
        scan_run_id = app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                item_count=1,
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=scan_run_id,
                    item_kind=ItemKind.POST,
                    item_key="seen-before-startup",
                    item_index=0,
                )
            ],
        )
        app_context.repositories.notification_events.add(
            NotificationEvent(
                target_id=target.id,
                item_key="seen-before-startup",
                channel=NotificationChannel.NTFY,
                status=NotificationStatus.SENT,
                message="sent",
            )
        )

    with TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            reset_runtime_data_on_startup=True,
        )
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        config = app_context.repositories.configs.get_for_target(target)
        latest_scan = app_context.repositories.scan_runs.latest_by_target(target.id)
        latest_items = app_context.repositories.latest_scan_items.list_by_target(target.id)
        notifications = app_context.repositories.notification_events.list_by_target(target.id)
        has_seen = app_context.repositories.seen_items.has_seen(
            target.scope_id,
            "seen-before-startup",
        )
        scope_initialized = app_context.repositories.scan_scope_state.is_initialized(
            target.scope_id
        )
        logical_alias_count = app_context.repositories.seen_items.connection.execute(
            "SELECT COUNT(*) FROM logical_item_aliases WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]
        dedupe_count = app_context.repositories.seen_items.connection.execute(
            "SELECT COUNT(*) FROM notification_dedupe WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]
        outbox = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{target.id}:seen-before-startup:ntfy"
        )

    assert loaded is not None
    assert config is not None
    assert latest_scan is None
    assert latest_items == []
    assert notifications == []
    assert has_seen
    assert scope_initialized
    assert logical_alias_count == 1
    assert dedupe_count == 1
    assert outbox is not None


def test_start_and_stop_routes_update_target_status(tmp_path: Path) -> None:
    """Web UI 開始/停止 route 對齊 restart/pause 語義。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="seen-before-start",
                item_kind=ItemKind.POST,
            )
        )
        app_context.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:seen-before-start:ntfy",
                target_id=target.id,
                item_key="seen-before-start",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    stop_response = client.post(f"/targets/{target.id}/stop", follow_redirects=False)
    start_response = client.post(
        f"/targets/{target.id}/start",
        data={"return_to": f"#target-{target.id}"},
        follow_redirects=False,
    )

    assert stop_response.status_code == 303
    assert start_response.status_code == 303
    assert start_response.headers["location"].endswith(f"#target-{target.id}")
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.runtime_states.get(target.id)
        has_seen = app_context.repositories.seen_items.has_seen(
            target.scope_id,
            "seen-before-start",
        )
        outbox_entry = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{target.id}:seen-before-start:ntfy",
        )
    assert loaded is not None
    assert loaded.enabled
    assert not loaded.paused
    assert state is not None
    assert state.scan_requested_at is not None
    assert has_seen
    assert outbox_entry is not None
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.woken_count == 2


def test_temporary_block_warning_requires_current_target_confirmation(
    tmp_path: Path,
) -> None:
    """警告期內只允許帶目前 generation 的單 target 明確確認。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="warning-group",
                canonical_url="https://www.facebook.com/groups/warning-group",
            )
        )
        group = app_context.services.sidebar_layout.create_group("警告期批次操作")
        app_context.services.sidebar_layout.save_placements(
            [(group.id, [target.id])]
        )
        observed_at = datetime.now(UTC)
        warning = app_context.services.facebook_temporary_block_warning.record(
            TemporaryBlockFinding(
                source_kind=FacebookWorkSourceKind.SCAN,
                operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
                action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
                target_id=target.id,
            ),
            detected_at=observed_at,
        )
        app_context.services.targets.pause_all_target_monitoring()

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=profile_dir,
            scheduler_manager=scheduler_manager,
        )
    )

    page = client.get("/")
    unconfirmed = client.post(
        f"/targets/{target.id}/start",
        follow_redirects=False,
    )
    stale = client.post(
        f"/targets/{target.id}/start",
        data={
            "temporary_block_warning_confirmed": "1",
            "warning_generation": str(warning.generation - 1),
        },
        follow_redirects=False,
    )
    blocked_group = restart_sidebar_group_monitoring_action(
        db_path,
        group.id,
    )
    group_start = client.post(f"/api/sidebar/groups/{group.id}/start")

    assert "Facebook 暫時限制存取警告" in page.text
    assert "data-temporary-block-confirm-submit" in page.text
    assert unconfirmed.status_code == 303
    assert "繼續執行可能無法取得內容" in unquote(
        unconfirmed.headers["location"]
    )
    assert stale.status_code == 303
    assert not blocked_group.ok
    assert blocked_group.confirmation_required
    assert blocked_group.warning_generation == warning.generation
    assert group_start.status_code == 200
    assert group_start.json()["confirmation_required"] is True
    assert group_start.json()["warning_generation"] == warning.generation
    assert scheduler_manager.started_count == 0
    with SqliteApplicationContext(db_path) as app_context:
        before_confirm = app_context.repositories.targets.get(target.id)
    assert before_confirm is not None and before_confirm.paused

    confirmed = client.post(
        f"/targets/{target.id}/start",
        data={
            "temporary_block_warning_confirmed": "1",
            "warning_generation": str(warning.generation),
        },
        follow_redirects=False,
    )

    assert confirmed.status_code == 303
    assert "target_started" in confirmed.headers["location"]
    assert scheduler_manager.started_count == 1
    with SqliteApplicationContext(db_path) as app_context:
        started = app_context.repositories.targets.get(target.id)
    assert started is not None and not started.paused

    client.post(f"/targets/{target.id}/stop", follow_redirects=False)
    second_unconfirmed = client.post(
        f"/targets/{target.id}/start",
        follow_redirects=False,
    )
    assert "繼續執行可能無法取得內容" in unquote(
        second_unconfirmed.headers["location"]
    )
    confirmed_group = client.post(
        f"/api/sidebar/groups/{group.id}/start",
        json={
            "temporary_block_warning_confirmed": True,
            "warning_generation": warning.generation,
        },
    )
    assert confirmed_group.status_code == 200
    assert confirmed_group.json()["confirmation_required"] is False
    assert confirmed_group.json()["updated_count"] == 1


def test_expired_temporary_block_warning_uses_normal_start_flow(tmp_path: Path) -> None:
    """已過期 warning 不可繼續攔截 Start，也不需要 confirmation payload。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="expired-start-warning",
                canonical_url="https://www.facebook.com/groups/expired-start-warning",
            )
        )
        app_context.services.facebook_temporary_block_warning.record(
            TemporaryBlockFinding(
                source_kind=FacebookWorkSourceKind.SCAN,
                operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
                action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
                target_id=target.id,
            ),
            detected_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        app_context.services.targets.pause_target_monitoring(target.id)
    scheduler = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler,
        )
    )

    response = client.post(f"/targets/{target.id}/start", follow_redirects=False)

    assert response.status_code == 303
    assert "target_started" in response.headers["location"]
    assert scheduler.started_count == 1
    with SqliteApplicationContext(db_path) as app_context:
        started = app_context.repositories.targets.get(target.id)
    assert started is not None and not started.paused


def test_batch_start_rejects_confirmation_for_superseded_warning_generation(
    tmp_path: Path,
) -> None:
    """批次 Start 在送出前 warning 更新時回傳新 generation，且保持零 target mutation。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="batch-warning-race",
                canonical_url="https://www.facebook.com/groups/batch-warning-race",
            )
        )
        group = app_context.services.sidebar_layout.create_group("批次 warning race")
        app_context.services.sidebar_layout.save_placements([(group.id, [target.id])])
        first = app_context.services.facebook_temporary_block_warning.record(
            TemporaryBlockFinding(
                source_kind=FacebookWorkSourceKind.SCAN,
                operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
                action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
                target_id=target.id,
            ),
            detected_at=datetime.now(UTC),
        )
        second = app_context.services.facebook_temporary_block_warning.record(
            TemporaryBlockFinding(
                source_kind=FacebookWorkSourceKind.METADATA,
                operation_kind=FacebookProductOperationKind.GROUP_METADATA_ACCESS,
                action_kind=FacebookActionKind.GROUP_DOCUMENT,
                target_id=target.id,
            ),
            detected_at=datetime.now(UTC) + timedelta(microseconds=1),
        )
        app_context.services.targets.pause_target_monitoring(target.id)
    scheduler = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler,
        )
    )

    stale = client.post(
        f"/api/sidebar/groups/{group.id}/start",
        json={
            "temporary_block_warning_confirmed": True,
            "warning_generation": first.generation,
        },
    )

    assert stale.status_code == 200
    assert stale.json()["confirmation_required"] is True
    assert stale.json()["warning_generation"] == second.generation
    assert stale.json()["updated_count"] == 0
    assert scheduler.started_count == 0
    assert scheduler.woken_count == 0
    with SqliteApplicationContext(db_path) as app_context:
        unchanged = app_context.repositories.targets.get(target.id)
        runtime = app_context.repositories.runtime_states.get(target.id)
    assert unchanged is not None and unchanged.paused
    assert runtime is not None and runtime.desired_state.value == "stopped"
    assert runtime.scan_requested_at is None

    confirmed = client.post(
        f"/api/sidebar/groups/{group.id}/start",
        json={
            "temporary_block_warning_confirmed": True,
            "warning_generation": second.generation,
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["confirmation_required"] is False
    assert confirmed.json()["updated_count"] == 1
    assert scheduler.started_count == 1


def test_incident_committing_before_confirmed_start_rejects_stale_confirmation(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Incident先線性化時，舊generation確認不得啟動target或scheduler。"""

    fixture = _seed_target_start_incident_race(tmp_path)
    scheduler = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=fixture.db_path,
            profile_dir=fixture.profile_dir,
            scheduler_manager=scheduler,
        )
    )
    incident_locked = Event()
    release_incident = Event()
    original = facebook_access_incident._record_facebook_access_incident

    def hold_incident_writer(*args, **kwargs):
        incident_locked.set()
        assert release_incident.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        facebook_access_incident,
        "_record_facebook_access_incident",
        hold_incident_writer,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        incident_future = pool.submit(
            facebook_access_incident.record_facebook_access_incident_for_db,
            db_path=fixture.db_path,
            finding=fixture.finding,
            occurred_at=fixture.incident_at,
        )
        assert incident_locked.wait(timeout=5)
        start_future = pool.submit(
            client.post,
            f"/targets/{fixture.target_id}/start",
            data={
                "temporary_block_warning_confirmed": "1",
                "warning_generation": str(fixture.warning_generation),
            },
            follow_redirects=False,
        )
        release_incident.set()
        incident = incident_future.result(timeout=10)
        response = start_future.result(timeout=10)

    assert incident.kind == FacebookAccessIncidentOutcomeKind.RECORDED
    assert response.status_code == 303
    assert "繼續執行可能無法取得內容" in unquote(response.headers["location"])
    assert (
        f"temporary_block_reprompt_target={fixture.target_id}"
        in response.headers["location"]
    )
    assert scheduler.started_count == 0
    assert scheduler.woken_count == 0
    with SqliteApplicationContext(fixture.db_path) as app_context:
        target = app_context.repositories.targets.get(fixture.target_id)
        runtime = app_context.repositories.runtime_states.get(fixture.target_id)
        current_warning = app_context.services.facebook_temporary_block_warning.get()
    assert target is not None and target.paused
    assert runtime is not None and runtime.desired_state.value == "stopped"
    assert runtime.scan_requested_at is None
    assert current_warning is not None
    refreshed_page = client.get(response.headers["location"])
    assert "data-temporary-block-confirm-submit" in refreshed_page.text
    assert f'data-target-id="{fixture.target_id}"' in refreshed_page.text
    assert f'value="{current_warning.generation}"' in refreshed_page.text


def test_confirmed_start_committing_before_incident_is_stopped_by_incident(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Start先線性化時可啟動scheduler，但隨後incident仍停止來源target。"""

    fixture = _seed_target_start_incident_race(tmp_path)
    scheduler = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=fixture.db_path,
            profile_dir=fixture.profile_dir,
            scheduler_manager=scheduler,
        )
    )
    start_locked = Event()
    release_start = Event()
    original = TargetApplicationService.restart_target_monitoring

    def hold_start_writer(self, target_id: str):
        start_locked.set()
        assert release_start.wait(timeout=5)
        return original(self, target_id)

    monkeypatch.setattr(
        TargetApplicationService,
        "restart_target_monitoring",
        hold_start_writer,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        start_future = pool.submit(
            client.post,
            f"/targets/{fixture.target_id}/start",
            data={
                "temporary_block_warning_confirmed": "1",
                "warning_generation": str(fixture.warning_generation),
            },
            follow_redirects=False,
        )
        assert start_locked.wait(timeout=5)
        incident_future = pool.submit(
            facebook_access_incident.record_facebook_access_incident_for_db,
            db_path=fixture.db_path,
            finding=fixture.finding,
            occurred_at=fixture.incident_at,
        )
        release_start.set()
        response = start_future.result(timeout=10)
        incident = incident_future.result(timeout=10)

    assert response.status_code == 303
    assert "target_started" in response.headers["location"]
    assert incident.kind == FacebookAccessIncidentOutcomeKind.RECORDED
    assert scheduler.started_count == 1
    with SqliteApplicationContext(fixture.db_path) as app_context:
        target = app_context.repositories.targets.get(fixture.target_id)
        runtime = app_context.repositories.runtime_states.get(fixture.target_id)
    assert target is not None and target.paused
    assert runtime is not None and runtime.desired_state.value == "stopped"
    assert runtime.scan_requested_at is None


def test_reset_target_notification_state_route_clears_outbox_and_seen(
    tmp_path: Path,
) -> None:
    """target 更多操作會重置通知與 seen 去重狀態，但不喚醒 scheduler。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=first.scope_id,
                item_key="first-seen",
                item_kind=ItemKind.POST,
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=second.scope_id,
                item_key="second-seen",
                item_kind=ItemKind.POST,
            )
        )
        for target, item_key in ((first, "first-seen"), (second, "second-seen")):
            app_context.repositories.notification_outbox.enqueue(
                NotificationOutboxEntry(
                    idempotency_key=f"{target.id}:{item_key}:ntfy",
                    target_id=target.id,
                    item_key=item_key,
                    item_kind=ItemKind.POST,
                    channel=NotificationChannel.NTFY,
                    title="title",
                    message="message",
                )
            )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(
        f"/targets/{first.id}/notifications/clear",
        data={"return_to": f"#target-{first.id}"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (
        page_feedback(response.text)["message"]
        == "已重置通知狀態：清除通知紀錄 1 筆、已看紀錄 1 筆"
    )
    assert page_feedback(response.text)["feedback"] == "notification_state_reset"
    with SqliteApplicationContext(db_path) as app_context:
        first_seen = app_context.repositories.seen_items.has_seen(
            first.scope_id,
            "first-seen",
        )
        first_outbox = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{first.id}:first-seen:ntfy",
        )
        second_outbox = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{second.id}:second-seen:ntfy",
        )
        second_seen = app_context.repositories.seen_items.has_seen(
            second.scope_id,
            "second-seen",
        )
    assert not first_seen
    assert second_seen
    assert first_outbox is None
    assert second_outbox is not None
    assert scheduler_manager.woken_count == 0


def test_sidebar_group_start_and_stop_routes_update_only_group_targets(
    tmp_path: Path,
) -> None:
    """sidebar group 開始/停止批次套用 target 語義，且只喚醒 scheduler 一次。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        outside = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="333",
                canonical_url="https://www.facebook.com/groups/333",
            )
        )
        group = app_context.services.sidebar_layout.create_group("批次操作")
        app_context.services.sidebar_layout.save_placements(
            [
                (group.id, [first.id, second.id]),
                (None, [outside.id]),
            ]
        )
        app_context.services.targets.restart_target_monitoring(second.id)
        app_context.services.targets.restart_target_monitoring(outside.id)
        for target, item_key in (
            (first, "first-seen"),
            (second, "second-seen"),
            (outside, "outside-seen"),
        ):
            app_context.repositories.seen_items.mark_seen(
                SeenItem(
                    scope_id=target.scope_id,
                    item_key=item_key,
                    item_kind=ItemKind.POST,
                )
            )
            app_context.repositories.notification_outbox.enqueue(
                NotificationOutboxEntry(
                    idempotency_key=f"{target.id}:{item_key}:desktop",
                    target_id=target.id,
                    item_key=item_key,
                    item_kind=ItemKind.POST,
                    channel=NotificationChannel.DESKTOP,
                    title="title",
                    message="message",
                )
            )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )

    stop_response = client.post(f"/api/sidebar/groups/{group.id}/stop")

    assert stop_response.status_code == 200
    assert stop_response.json()["updated_count"] == 2
    with SqliteApplicationContext(db_path) as app_context:
        first_stopped = app_context.repositories.targets.get(first.id)
        second_stopped = app_context.repositories.targets.get(second.id)
        outside_after_stop = app_context.repositories.targets.get(outside.id)
        first_seen_after_stop = app_context.repositories.seen_items.has_seen(
            first.scope_id,
            "first-seen",
        )
        first_outbox_after_stop = (
            app_context.repositories.notification_outbox.get_by_idempotency_key(
                f"{first.id}:first-seen:desktop",
            )
        )
    assert first_stopped is not None and first_stopped.enabled and first_stopped.paused
    assert second_stopped is not None and second_stopped.enabled and second_stopped.paused
    assert outside_after_stop is not None and outside_after_stop.enabled
    assert not outside_after_stop.paused
    assert first_seen_after_stop
    assert first_outbox_after_stop is not None

    start_response = client.post(f"/api/sidebar/groups/{group.id}/start")

    assert start_response.status_code == 200
    assert start_response.json()["updated_count"] == 2
    assert scheduler_manager.woken_count == 2
    with SqliteApplicationContext(db_path) as app_context:
        first_loaded = app_context.repositories.targets.get(first.id)
        second_loaded = app_context.repositories.targets.get(second.id)
        outside_loaded = app_context.repositories.targets.get(outside.id)
        first_state = app_context.repositories.runtime_states.get(first.id)
        second_state = app_context.repositories.runtime_states.get(second.id)
        outside_state = app_context.repositories.runtime_states.get(outside.id)
        first_seen = app_context.repositories.seen_items.has_seen(
            first.scope_id,
            "first-seen",
        )
        second_seen = app_context.repositories.seen_items.has_seen(
            second.scope_id,
            "second-seen",
        )
        outside_seen = app_context.repositories.seen_items.has_seen(
            outside.scope_id,
            "outside-seen",
        )
        first_outbox = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{first.id}:first-seen:desktop",
        )
        second_outbox = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{second.id}:second-seen:desktop",
        )
        outside_outbox = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{outside.id}:outside-seen:desktop",
        )

    assert first_loaded is not None and first_loaded.enabled and not first_loaded.paused
    assert second_loaded is not None and second_loaded.enabled and not second_loaded.paused
    assert outside_loaded is not None and outside_loaded.enabled and not outside_loaded.paused
    assert first_state is not None and first_state.scan_requested_at is not None
    assert second_state is not None and second_state.scan_requested_at is not None
    assert outside_state is not None and outside_state.scan_requested_at is not None
    assert first_seen
    assert second_seen
    assert outside_seen
    assert first_outbox is not None
    assert second_outbox is not None
    assert outside_outbox is not None
    assert scheduler_manager.started_count == 1


def test_start_route_supports_comments_target(tmp_path: Path) -> None:
    """Web UI comments target 的開始 route 保留 comments seen 並喚醒 scheduler。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
                ),
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="comment-before-start",
                item_kind=ItemKind.COMMENT,
            )
        )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(
        f"/targets/{target.id}/start",
        data={"return_to": f"#target-{target.id}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.runtime_states.get(target.id)
        has_seen = app_context.repositories.seen_items.has_seen(
            target.scope_id,
            "comment-before-start",
        )
    assert loaded is not None
    assert not loaded.paused
    assert state is not None
    assert state.scan_requested_at is not None
    assert has_seen
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.woken_count == 1


def test_scan_once_requests_resident_scan_for_posts_and_comments(tmp_path: Path) -> None:
    """Web UI scan-once 只排入 resident scan request，不啟動 one-shot debug。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        posts_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        comments_target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
                ),
            )
        )
        app_context.services.targets.restart_target_monitoring(posts_target.id)
        app_context.services.targets.restart_target_monitoring(comments_target.id)

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    posts_response = client.post(f"/targets/{posts_target.id}/scan-once", follow_redirects=False)
    comments_response = client.post(
        f"/targets/{comments_target.id}/scan-once",
        follow_redirects=False,
    )

    assert posts_response.status_code == 303
    assert comments_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        posts_state = app_context.repositories.runtime_states.get(posts_target.id)
        comments_state = app_context.repositories.runtime_states.get(comments_target.id)
    assert posts_state is not None
    assert posts_state.scan_requested_at is not None
    assert comments_state is not None
    assert comments_state.scan_requested_at is not None
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.woken_count == 2


def test_scan_once_requires_started_target(tmp_path: Path) -> None:
    """停止中的 target 不會被 scan-once 暗中送進 fallback worker。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
                ),
            )
        )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(f"/targets/{target.id}/scan-once", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert scheduler_manager.started_count == 0
    assert scheduler_manager.woken_count == 0


def test_target_action_db_failure_does_not_trigger_scheduler_side_effect(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """target action DB operation 失敗時不得喚醒或啟動 scheduler。"""

    async def raise_locked(*args: object, **kwargs: object) -> object:
        raise sqlite3.OperationalError("database is locked")

    scheduler_manager = FakeSchedulerManager()
    monkeypatch.setattr(target_action_routes, "run_web_db_operation", raise_locked)
    client = TestClient(
        create_app(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )

    response = client.post("/targets/target-a/start", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert scheduler_manager.started_count == 0
    assert scheduler_manager.woken_count == 0


def _seed_target_start_incident_race(tmp_path: Path) -> _TargetStartIncidentRace:
    """建立警告期Start確認與新incident共用generation的race fixture。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    warning_at = datetime.now(UTC)
    incident_at = warning_at + timedelta(microseconds=1)
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="start-incident-race",
                canonical_url="https://www.facebook.com/groups/start-incident-race",
            )
        )
        warning = app_context.services.facebook_temporary_block_warning.record(
            TemporaryBlockFinding(
                source_kind=FacebookWorkSourceKind.METADATA,
                operation_kind=FacebookProductOperationKind.GROUP_METADATA_ACCESS,
                action_kind=FacebookActionKind.GROUP_DOCUMENT,
                target_id=target.id,
            ),
            detected_at=warning_at,
        )
        app_context.services.targets.pause_all_target_monitoring()
    return _TargetStartIncidentRace(
        db_path=db_path,
        profile_dir=profile_dir,
        target_id=target.id,
        warning_generation=warning.generation,
        finding=TemporaryBlockFinding(
            source_kind=FacebookWorkSourceKind.METADATA,
            operation_kind=FacebookProductOperationKind.GROUP_METADATA_ACCESS,
            action_kind=FacebookActionKind.GROUP_DOCUMENT,
            target_id=target.id,
        ),
        incident_at=incident_at,
    )
