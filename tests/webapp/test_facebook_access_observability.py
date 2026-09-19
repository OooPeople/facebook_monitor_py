"""Profile-wide Facebook access circuit Web／diagnostics 契約測試。"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.application.facebook_access_observability import (
    build_facebook_access_safe_snapshot,
)
from facebook_monitor.application.facebook_access_observability import (
    read_existing_facebook_access_circuit,
)
from facebook_monitor.application.managed_profile_identity import (
    resolve_managed_profile_identity,
)
from facebook_monitor.automation.profile_identity import (
    load_or_create_managed_profile_identity,
)
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.core.facebook_access import FacebookAccessCircuitSnapshot
from facebook_monitor.core.facebook_access import FacebookAccessCircuitStatus
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookRecoveryRecipeKind
from facebook_monitor.runtime.paths import FACEBOOK_AUTOMATION_SESSION_GUARDS_DIR_NAME
from facebook_monitor.webapp.app import create_app as create_production_app
from facebook_monitor.webapp.facebook_access_recovery import (
    list_facebook_access_recovery_candidates,
)
from facebook_monitor.webapp.runtime_diagnostics import build_runtime_diagnostics_view
from facebook_monitor.worker.facebook_automation_session_guard import (
    derive_facebook_automation_profile_alias,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardStore,
)
from tests.helpers.webapp import FakeSchedulerManager
from tests.webapp.app_test_helpers import create_app


_OPENED_AT = datetime(2026, 7, 22, 1, 2, 3, tzinfo=UTC)
_COOLDOWN_UNTIL = datetime(2099, 7, 22, 2, 2, 3, tzinfo=UTC)


def _facebook_access_banner_fragment(page_text: str) -> str:
    """截取 safety banner HTML，避免把 target cards 的正式 id 契約混入檢查。"""

    start = page_text.index("data-facebook-access-circuit-banner")
    end = page_text.index("data-profile-session-warning", start)
    return page_text[start:end]


@pytest.mark.parametrize("state", ["open", "half_open"])
def test_dashboard_full_and_partial_show_global_circuit_banner_safely(
    tmp_path: Path,
    state: str,
) -> None:
    """Open/half-open 只顯示全域安全摘要，partial update 契約一致。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    scope_key = _seed_circuit(db_path, profile_dir, state=state)
    client = TestClient(create_app(db_path=db_path, profile_dir=profile_dir))

    page = client.get("/")
    partial = client.get("/api/dashboard-cards")

    assert page.status_code == 200
    assert partial.status_code == 200
    assert page.text.count("data-facebook-access-circuit-banner") == 1
    assert "Facebook 自動存取已安全暫停" in page.text
    assert "目前受管 profile" in page.text
    payload = partial.json()["facebook_access_circuit_banner"]
    assert set(payload) == {
        "visible",
        "title",
        "message",
        "profile_scope",
        "state",
        "reason",
        "cooldown_active",
        "cooldown_until",
        "probe_pending",
        "last_probe_result",
        "last_probe_result_label",
        "recovery_enabled",
        "recovery_disabled_reason",
        "recovery_status_message",
    }
    assert payload["visible"] is True
    assert payload["title"] == "Facebook 自動存取已安全暫停"
    assert payload["profile_scope"] == "managed_profile"
    assert payload["state"] == state
    assert payload["reason"] == FACEBOOK_TEMPORARY_BLOCK_REASON
    assert payload["cooldown_active"] is True
    assert payload["cooldown_until"] == _COOLDOWN_UNTIL.isoformat()
    assert payload["recovery_enabled"] is False
    assert "Facebook 暫時限制存取" in payload["message"]
    assert "最早可檢查時間" in payload["message"]
    combined = page.text + partial.text
    assert scope_key not in combined
    assert "private-episode" not in combined
    assert "private-half-open-token" not in combined


def test_closed_circuit_does_not_show_global_pause_banner(tmp_path: Path) -> None:
    """Closed circuit 保留 snapshot，但不顯示安全暫停 banner。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    identity = load_or_create_managed_profile_identity(
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    with SqliteApplicationContext(db_path) as app:
        app.repositories.facebook_access_circuit.ensure_closed(
            identity.profile_scope_key,
            updated_at=_OPENED_AT,
        )
    client = TestClient(create_app(db_path=db_path, profile_dir=profile_dir))

    page = client.get("/")
    payload = client.get("/api/dashboard-cards").json()["facebook_access_circuit_banner"]

    assert "Facebook 自動存取已安全暫停" not in page.text
    assert payload["visible"] is False
    assert payload["state"] == "unknown"


def test_observability_read_does_not_create_profile_marker(tmp_path: Path) -> None:
    """可觀測 read path 不得為了顯示狀態建立 profile 或 marker。"""

    profile_dir = tmp_path / "profiles" / "automation"

    circuit = read_existing_facebook_access_circuit(
        db_path=tmp_path / "missing.db",
        profile_dir=profile_dir,
    )

    assert circuit is None
    assert not profile_dir.exists()


def test_runtime_diagnostics_uses_only_safe_circuit_summary(tmp_path: Path) -> None:
    """Runtime diagnostics 不輸出 raw scope、episode 或 half-open token。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    scope_key = _seed_circuit(db_path, profile_dir, state="half_open")

    diagnostics = build_runtime_diagnostics_view(
        SimpleNamespace(
            db_path=db_path,
            profile_dir=profile_dir,
            scheduler_manager=None,
        )
    )
    circuit_field = next(
        field for field in diagnostics.fields if field.label == "Facebook access circuit"
    )

    assert "profile_scope=managed_profile" in circuit_field.value
    assert "state=half_open" in circuit_field.value
    assert f"reason={FACEBOOK_TEMPORARY_BLOCK_REASON}" in circuit_field.value
    assert scope_key not in diagnostics.copy_text
    assert "private-episode" not in diagnostics.copy_text
    assert "private-half-open-token" not in diagnostics.copy_text


def test_safe_snapshot_replaces_unknown_reason_instead_of_echoing_it() -> None:
    """DB 內未知 reason 不得原樣進入 Web/support diagnostics。"""

    snapshot = build_facebook_access_safe_snapshot(
        FacebookAccessCircuitSnapshot(
            profile_scope_key="private-scope",
            status=FacebookAccessCircuitStatus.OPEN,
            reason_code="private reason https://example.test/account/123",
            cooldown_until=_COOLDOWN_UNTIL,
            updated_at=_OPENED_AT,
        ),
        profile_scope="managed_profile",
        now=_OPENED_AT,
    )

    assert snapshot.reason == "unrecognized_code"
    assert "private" not in snapshot.reason
    assert "example.test" not in snapshot.reason


def test_recovery_route_only_requests_probe_and_wakes_existing_scheduler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST 不 claim/browser/start scheduler，只寫 request 並 wake。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    scope_key, _ = _seed_posts_recovery_circuit(db_path, profile_dir)
    scheduler = FakeSchedulerManager()

    def reject_claim(*args: object, **kwargs: object) -> None:
        raise AssertionError("Web route must not claim half-open")

    monkeypatch.setattr(
        "facebook_monitor.application.facebook_access_circuit_service."
        "FacebookAccessCircuitService.claim_half_open",
        reject_claim,
    )
    client = TestClient(
        create_production_app(
            db_path=db_path,
            profile_dir=profile_dir,
            scheduler_manager=scheduler,
            csrf_token="known-token",
        )
    )
    ready = client.get("/api/dashboard-cards").json()[
        "facebook_access_circuit_banner"
    ]
    ready_page = client.get("/")
    assert ready["recovery_enabled"] is True
    assert ready["recovery_disabled_reason"] == ""
    assert "冷卻時間已結束" in ready["recovery_status_message"]
    assert "data-circuit-recovery-button" in ready_page.text
    assert "data-circuit-recovery-form" in ready_page.text

    rejected = client.post(
        "/facebook-access/recovery-check",
        follow_redirects=False,
    )
    response = client.post(
        "/facebook-access/recovery-check",
        data={"csrf_token": "known-token"},
        follow_redirects=False,
    )

    assert rejected.status_code == 403
    assert response.status_code == 303
    assert "facebook_access_recovery_requested" in response.headers["location"]
    assert scheduler.woken_count == 1
    assert scheduler.started_count == 0
    pending = client.get("/api/dashboard-cards").json()[
        "facebook_access_circuit_banner"
    ]
    assert pending["probe_pending"] is True
    assert pending["recovery_enabled"] is False
    assert pending["recovery_disabled_reason"] == "probe_pending"
    assert "已排程" in pending["recovery_status_message"]
    with SqliteApplicationContext(db_path) as app:
        state = app.services.facebook_access_circuit.get(scope_key)
        assert state is not None
        assert state.status == FacebookAccessCircuitStatus.OPEN
        assert state.probe_request_id
        assert state.half_open_token == ""


def test_comments_recovery_is_explicitly_disabled_in_read_model_and_post(
    tmp_path: Path,
) -> None:
    """Comments 沒有 approved recipe 時，按鈕與偽造 POST 都 fail closed。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    scope_key = _seed_circuit(db_path, profile_dir, state="open")
    scheduler = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=profile_dir,
            scheduler_manager=scheduler,
        )
    )

    payload = client.get("/api/dashboard-cards").json()[
        "facebook_access_circuit_banner"
    ]
    response = client.post(
        "/facebook-access/recovery-check",
        follow_redirects=False,
    )

    assert payload["recovery_enabled"] is False
    assert payload["recovery_disabled_reason"] == (
        "comments_recovery_recipe_unavailable"
    )
    assert "留言監視尚未有核准" in payload["recovery_status_message"]
    assert response.status_code == 303
    assert "留言監視尚未有核准" in unquote(response.headers["location"])
    assert scheduler.woken_count == 0
    with SqliteApplicationContext(db_path) as app:
        state = app.services.facebook_access_circuit.get(scope_key)
        assert state is not None
        assert state.probe_request_id == ""


def test_unclean_session_hold_disables_recovery_without_leaking_marker(
    tmp_path: Path,
) -> None:
    """Stale normal marker 顯示非 block 安全 hold，且不暴露 marker identity。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    identity = load_or_create_managed_profile_identity(
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    store = _session_guard_store(tmp_path, identity.profile_scope_key)
    marker = store.start_normal_session(started_at=_OPENED_AT)
    scheduler = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=profile_dir,
            scheduler_manager=scheduler,
        )
    )

    page = client.get("/")
    payload = client.get("/api/dashboard-cards").json()[
        "facebook_access_circuit_banner"
    ]
    response = client.post(
        "/facebook-access/recovery-check",
        follow_redirects=False,
    )

    assert payload["visible"] is True
    assert payload["state"] == "unclean_session_hold"
    assert payload["recovery_enabled"] is False
    assert payload["recovery_disabled_reason"] == (
        "unclean_session_healthcheck_unavailable"
    )
    assert "不代表已偵測到 Facebook 封鎖" in payload["message"]
    assert response.status_code == 303
    assert scheduler.woken_count == 0
    combined = page.text + str(payload) + response.headers["location"]
    assert marker.session_id not in combined
    assert store.marker_path.name not in combined


def test_unclean_session_web_lists_candidates_and_persists_probe_request(
    tmp_path: Path,
) -> None:
    """Stale normal hold經quiet gap後只由Web持久化顯式operation/target request。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    identity = load_or_create_managed_profile_identity(
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    store = _session_guard_store(tmp_path, identity.profile_scope_key)
    marker = store.start_normal_session(
        started_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="stale-session-canary",
                canonical_url="https://www.facebook.com/groups/stale-session-canary",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.facebook_session_recovery.reconcile_stale_session(
            identity.profile_scope_key,
            marker_session_id=marker.session_id,
            reconciled_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
    scheduler = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=profile_dir,
            scheduler_manager=scheduler,
        )
    )
    with SqliteApplicationContext(db_path) as app:
        candidates = list_facebook_access_recovery_candidates(
            app,
            profile_dir=profile_dir,
        )
    assert candidates
    request_value = candidates[0].request_value

    page = client.get("/")
    response = client.post(
        "/facebook-access/recovery-check",
        data={"candidate": request_value},
        follow_redirects=False,
    )

    assert f'value="{request_value}"' in page.text
    assert target.id not in _facebook_access_banner_fragment(page.text)
    assert response.status_code == 303
    assert "facebook_session_recovery_requested" in response.headers["location"]
    assert scheduler.woken_count == 1
    with SqliteApplicationContext(db_path) as app:
        recovery = app.services.facebook_session_recovery.get(
            identity.profile_scope_key
        )
    assert recovery is not None
    assert recovery.status.value == "probe_pending"
    assert recovery.requested_operation_kind == (
        FacebookProductOperationKind.POSTS_ACCESS
    )
    assert recovery.requested_target_id == target.id
    combined = page.text + response.headers["location"]
    assert marker.session_id not in combined
    assert identity.profile_scope_key not in combined


def test_storage_critical_hold_is_bounded_and_active_session_is_not_stale(
    tmp_path: Path,
) -> None:
    """Corrupt marker 只顯示 bounded storage hold；active normal marker 不誤判。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    identity = load_or_create_managed_profile_identity(
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    store = _session_guard_store(tmp_path, identity.profile_scope_key)
    store.directory.mkdir(parents=True)
    raw_secret = "private-marker-content"
    store.marker_path.write_text(raw_secret, encoding="utf-8")
    scheduler = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=profile_dir,
            scheduler_manager=scheduler,
        )
    )

    payload = client.get("/api/dashboard-cards").json()[
        "facebook_access_circuit_banner"
    ]

    assert payload["state"] == "storage_critical"
    assert payload["recovery_enabled"] is False
    assert raw_secret not in str(payload)
    assert store.marker_path.name not in str(payload)

    store.marker_path.unlink()
    store.start_normal_session(started_at=_OPENED_AT)
    scheduler.running = True
    active_payload = client.get("/api/dashboard-cards").json()[
        "facebook_access_circuit_banner"
    ]
    assert active_payload["visible"] is False


@pytest.mark.parametrize("marker_failure", ["missing", "corrupt", "mismatch"])
def test_profile_identity_failure_projects_storage_critical_banner(
    tmp_path: Path,
    marker_failure: str,
) -> None:
    """Profile identity 遺失、損毀或替換都必須顯示 fail-closed banner。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    _seed_circuit(db_path, profile_dir, state="open")
    identity = resolve_managed_profile_identity(
        db_path=db_path,
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    identity.marker_path.unlink()
    if marker_failure == "corrupt":
        identity.marker_path.write_text("private-corrupt-marker", encoding="ascii")
    elif marker_failure == "mismatch":
        replacement = load_or_create_managed_profile_identity(
            profiles_root=profile_dir.parent,
            profile_dir=profile_dir,
        )
        assert replacement.profile_scope_key != identity.profile_scope_key

    client = TestClient(create_app(db_path=db_path, profile_dir=profile_dir))
    page = client.get("/")
    payload = client.get("/api/dashboard-cards").json()[
        "facebook_access_circuit_banner"
    ]
    response = client.post(
        "/facebook-access/recovery-check",
        follow_redirects=False,
    )

    assert payload["visible"] is True
    assert payload["state"] == "storage_critical"
    assert payload["recovery_enabled"] is False
    assert "Facebook 自動化安全儲存狀態異常" in page.text
    assert response.status_code == 303
    combined = page.text + str(payload) + response.headers["location"]
    assert identity.profile_scope_key not in combined
    assert str(identity.marker_uuid) not in combined
    assert "private-corrupt-marker" not in combined


def test_legacy_unbound_marker_scope_mismatch_projects_storage_critical_banner(
    tmp_path: Path,
) -> None:
    """首次 binding 前 marker 與既有 safety scope 不符時 Web 也必須 fail closed。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    original_scope = _seed_circuit(db_path, profile_dir, state="open")
    original = load_or_create_managed_profile_identity(
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    original.marker_path.unlink()
    replacement = load_or_create_managed_profile_identity(
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    assert replacement.profile_scope_key != original_scope

    client = TestClient(create_app(db_path=db_path, profile_dir=profile_dir))
    payload = client.get("/api/dashboard-cards").json()[
        "facebook_access_circuit_banner"
    ]
    response = client.post(
        "/facebook-access/recovery-check",
        follow_redirects=False,
    )

    assert payload["visible"] is True
    assert payload["state"] == "storage_critical"
    assert payload["recovery_enabled"] is False
    assert response.status_code == 303
    combined = str(payload) + response.headers["location"]
    assert original_scope not in combined
    assert replacement.profile_scope_key not in combined
    with SqliteApplicationContext(db_path) as app:
        assert app.repositories.managed_profile_identity.get() is None


def test_probe_readiness_reports_cooldown_and_missing_trigger_target(
    tmp_path: Path,
) -> None:
    """CTA 只在 cooldown 結束且 trigger target active 時開放。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    _, target_id = _seed_posts_recovery_circuit(
        db_path,
        profile_dir,
        cooldown_until=_COOLDOWN_UNTIL,
    )
    client = TestClient(create_app(db_path=db_path, profile_dir=profile_dir))
    cooldown = client.get("/api/dashboard-cards").json()[
        "facebook_access_circuit_banner"
    ]
    assert cooldown["recovery_enabled"] is False
    assert cooldown["recovery_disabled_reason"] == "cooldown_active"

    with SqliteApplicationContext(db_path) as app:
        app.services.targets.pause_target_monitoring(target_id)
        app.repositories.facebook_access_circuit.connection.execute(
            """
            UPDATE facebook_access_circuit_state
            SET cooldown_until = ?
            """,
            ((datetime(2020, 1, 1, tzinfo=UTC)).isoformat(),),
        )
    unavailable = client.get("/api/dashboard-cards").json()[
        "facebook_access_circuit_banner"
    ]
    assert unavailable["recovery_enabled"] is False
    assert unavailable["recovery_disabled_reason"] == "trigger_target_unavailable"


def test_recovery_form_selects_same_operation_alternative_canary(
    tmp_path: Path,
) -> None:
    """Trigger 失效時 Web 顯示替代 posts canary，並把顯式選擇寫入 request。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    scope_key, trigger_id = _seed_posts_recovery_circuit(db_path, profile_dir)
    with SqliteApplicationContext(db_path) as app:
        alternative = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="alternative-recovery-group",
                canonical_url=(
                    "https://www.facebook.com/groups/alternative-recovery-group"
                ),
            )
        )
        wrong_operation = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="alternative-recovery-group",
                parent_post_id="post-a",
                canonical_url=(
                    "https://www.facebook.com/groups/alternative-recovery-group/"
                    "posts/post-a"
                ),
            )
        )
        app.services.targets.restart_target_monitoring(alternative.id)
        app.services.targets.restart_target_monitoring(wrong_operation.id)
        app.services.targets.pause_target_monitoring(trigger_id)
    scheduler = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=profile_dir,
            scheduler_manager=scheduler,
        )
    )

    payload = client.get("/api/dashboard-cards").json()[
        "facebook_access_circuit_banner"
    ]
    page = client.get("/")
    with SqliteApplicationContext(db_path) as app:
        candidates = list_facebook_access_recovery_candidates(
            app,
            profile_dir=profile_dir,
        )
    assert len(candidates) == 1
    alternative_handle = candidates[0].request_value
    rejected = client.post(
        "/facebook-access/recovery-check",
        data={"candidate": "candidate-forged"},
        follow_redirects=False,
    )
    requested = client.post(
        "/facebook-access/recovery-check",
        data={"candidate": alternative_handle},
        follow_redirects=False,
    )

    assert payload["recovery_enabled"] is True
    assert "data-circuit-recovery-target" in page.text
    assert f'value="{alternative_handle}"' in page.text
    banner = _facebook_access_banner_fragment(page.text)
    assert alternative.id not in banner
    assert wrong_operation.id not in banner
    assert rejected.status_code == 303
    assert "請重新選擇安全檢查對象" in unquote(rejected.headers["location"])
    assert requested.status_code == 303
    assert "facebook_access_recovery_requested" in requested.headers["location"]
    assert scheduler.woken_count == 1
    with SqliteApplicationContext(db_path) as app:
        state = app.services.facebook_access_circuit.get(scope_key)
        assert state is not None
        assert state.requested_target_id == alternative.id


def test_last_probe_result_is_presented_without_internal_probe_identity(
    tmp_path: Path,
) -> None:
    """Banner 可顯示上次 probe 結果，不帶 request/token identity。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    scope_key, _ = _seed_posts_recovery_circuit(
        db_path,
        profile_dir,
        cooldown_until=_COOLDOWN_UNTIL,
    )
    with SqliteApplicationContext(db_path) as app:
        app.repositories.facebook_access_circuit.connection.execute(
            """
            UPDATE facebook_access_circuit_state
            SET last_probe_result = 'blocked',
                last_probe_finished_at = ?
            WHERE profile_scope_key = ?
            """,
            (_OPENED_AT.isoformat(), scope_key),
        )
    client = TestClient(create_app(db_path=db_path, profile_dir=profile_dir))

    payload = client.get("/api/dashboard-cards").json()[
        "facebook_access_circuit_banner"
    ]
    page = client.get("/")

    assert payload["last_probe_result"] == "blocked"
    assert payload["last_probe_result_label"] == (
        "最近一次恢復檢查：仍受到 Facebook 限制"
    )
    assert payload["last_probe_result_label"] in page.text
    assert "probe_request_id" not in page.text + str(payload)
    assert "half_open_token" not in page.text + str(payload)


def _seed_circuit(db_path: Path, profile_dir: Path, *, state: str) -> str:
    """建立符合 schema invariant 的 open/half-open circuit fixture。"""

    identity = load_or_create_managed_profile_identity(
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    half_open_started_at = _OPENED_AT + timedelta(minutes=1)
    half_open_expires_at = half_open_started_at + timedelta(minutes=10)
    with SqliteApplicationContext(db_path) as app:
        repository = app.repositories.facebook_access_circuit
        repository.ensure_closed(identity.profile_scope_key, updated_at=_OPENED_AT)
        repository.connection.execute(
            """
            UPDATE facebook_access_circuit_state
            SET state = ?,
                episode_id = 'private-episode',
                generation = 1,
                reason_code = ?,
                source_kind = 'scan',
                operation_kind = 'comments_access',
                trigger_action_kind = 'direct_document',
                opened_at = ?,
                last_detected_at = ?,
                cooldown_until = ?,
                detection_count = 1,
                half_open_token = ?,
                half_open_started_at = ?,
                half_open_lease_expires_at = ?,
                updated_at = ?
            WHERE profile_scope_key = ?
            """,
            (
                state,
                FACEBOOK_TEMPORARY_BLOCK_REASON,
                _OPENED_AT.isoformat(),
                _OPENED_AT.isoformat(),
                _COOLDOWN_UNTIL.isoformat(),
                "private-half-open-token" if state == "half_open" else "",
                half_open_started_at.isoformat() if state == "half_open" else "",
                half_open_expires_at.isoformat() if state == "half_open" else "",
                _OPENED_AT.isoformat(),
                identity.profile_scope_key,
            ),
        )
    return identity.profile_scope_key


def _seed_posts_recovery_circuit(
    db_path: Path,
    profile_dir: Path,
    *,
    cooldown_until: datetime = datetime(2020, 1, 1, tzinfo=UTC),
) -> tuple[str, str]:
    """Seed 一筆可供 Web manual recovery 使用的 posts circuit。"""

    identity = load_or_create_managed_profile_identity(
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="recovery-group",
                canonical_url="https://www.facebook.com/groups/recovery-group",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        repository = app.repositories.facebook_access_circuit
        repository.ensure_closed(identity.profile_scope_key, updated_at=_OPENED_AT)
        repository.connection.execute(
            """
            UPDATE facebook_access_circuit_state
            SET state = 'open',
                episode_id = 'private-recovery-episode',
                generation = 1,
                reason_code = ?,
                source_kind = 'scan',
                operation_kind = 'posts_access',
                trigger_action_kind = 'direct_document',
                recovery_recipe_kind = ?,
                trigger_target_id = ?,
                opened_at = ?,
                last_detected_at = ?,
                cooldown_until = ?,
                detection_count = 1,
                updated_at = ?
            WHERE profile_scope_key = ?
            """,
            (
                FACEBOOK_TEMPORARY_BLOCK_REASON,
                FacebookRecoveryRecipeKind.GROUP_FEED_DOCUMENT_GUARD_V1.value,
                target.id,
                _OPENED_AT.isoformat(),
                _OPENED_AT.isoformat(),
                cooldown_until.isoformat(),
                _OPENED_AT.isoformat(),
                identity.profile_scope_key,
            ),
        )
    return identity.profile_scope_key, target.id


def _session_guard_store(
    data_dir: Path,
    profile_scope_key: str,
) -> FacebookAutomationSessionGuardStore:
    """建立與 resident runtime 相同路徑推導的 guard store。"""

    return FacebookAutomationSessionGuardStore(
        data_dir / FACEBOOK_AUTOMATION_SESSION_GUARDS_DIR_NAME,
        profile_alias=derive_facebook_automation_profile_alias(profile_scope_key),
    )
