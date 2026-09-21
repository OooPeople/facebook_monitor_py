"""Facebook temporary-block warning Web read-model 契約測試。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC
from datetime import datetime
from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.facebook_temporary_block_warning_service import (
    FacebookTemporaryBlockWarningService,
)
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.facebook_temporary_block import FacebookActionKind
from facebook_monitor.core.facebook_temporary_block import FacebookProductOperationKind
from facebook_monitor.core.facebook_temporary_block import FacebookWorkSourceKind
from facebook_monitor.core.facebook_temporary_block import TemporaryBlockFinding
from facebook_monitor.core.facebook_temporary_block import TemporaryBlockWarningSnapshot
from facebook_monitor.webapp.dashboard_warnings import (
    build_facebook_temporary_block_warning,
)
from tests.webapp.app_test_helpers import create_app


_DETECTED_AT = datetime(2099, 7, 22, 1, 2, 3, tzinfo=UTC)


def _finding(target_id: str) -> TemporaryBlockFinding:
    """建立不含私人內容的正式 temporary-block finding。"""

    return TemporaryBlockFinding(
        source_kind=FacebookWorkSourceKind.SCAN,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
        action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
        target_id=target_id,
    )


def _seed_warning(db_path: Path) -> tuple[str, int]:
    """建立 target 與有效 warning，回傳 target id / generation。"""

    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="temporary-block-warning-group",
                canonical_url=(
                    "https://www.facebook.com/groups/temporary-block-warning-group"
                ),
            )
        )
        app_context.services.targets.restart_target_monitoring(target.id)
        warning = app_context.services.facebook_temporary_block_warning.record(
            _finding(target.id),
            detected_at=_DETECTED_AT,
        )
    return target.id, warning.generation


def test_dashboard_full_and_partial_share_minimal_temporary_block_warning(
    tmp_path: Path,
) -> None:
    """Full/partial 都只公開同一個最小 advisory warning，不再顯示 recovery CTA。"""

    db_path = tmp_path / "app.db"
    target_id, generation = _seed_warning(db_path)
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.pause_target_monitoring(target_id)
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    page = client.get("/")
    partial = client.get("/api/dashboard-cards")

    assert page.status_code == 200
    assert partial.status_code == 200
    assert page.text.count("\n        data-temporary-block-warning\n") == 1
    assert "Facebook 暫時限制存取警告" in page.text
    assert "警告期間仍可按「開始」並確認風險" in page.text
    assert "data-temporary-block-confirm-submit" in page.text
    assert f'data-warning-generation="{generation}"' in page.text
    assert f'value="{generation}"' in page.text
    payload = partial.json()["facebook_temporary_block_warning"]
    assert set(payload) == {
        "active",
        "title",
        "message",
        "warning_until",
        "generation",
    }
    assert payload["active"] is True
    assert payload["generation"] == generation
    assert payload["title"] == "Facebook 暫時限制存取警告"
    assert "警告期間仍可按「開始」並確認風險" in payload["message"]
    assert payload["warning_until"]
    assert partial.json()["cards"][0]["target_id"] == target_id
    combined = page.text + partial.text
    assert "recovery-check" not in combined
    assert "data-circuit-recovery" not in combined
    assert "facebook_access_recovery_candidates" not in combined


def test_expired_warning_is_hidden_and_does_not_expose_confirmation_state(
    tmp_path: Path,
) -> None:
    """過期 warning 可留在 DB，但 Web read model 必須回 inactive。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="expired-warning-group",
                canonical_url="https://www.facebook.com/groups/expired-warning-group",
            )
        )
        app_context.services.facebook_temporary_block_warning.record(
            _finding(target.id),
            detected_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    page = client.get("/")
    payload = client.get("/api/dashboard-cards").json()[
        "facebook_temporary_block_warning"
    ]

    assert 'data-temporary-block-warning\n' in page.text
    assert "data-temporary-block-confirm-submit" not in page.text
    assert payload == {
        "active": False,
        "title": "",
        "message": "",
        "warning_until": "",
        "generation": -1,
    }


def test_recovery_route_is_not_a_temporary_block_product_action(tmp_path: Path) -> None:
    """Temporary block 是可確認 warning，不再提供 recovery probe route。"""

    client = TestClient(
        create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile")
    )

    response = client.post("/facebook-access/recovery-check")

    assert response.status_code == 404


def test_dashboard_warning_and_cards_use_one_sqlite_snapshot(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Writer 在 warning read 後提交時，partial response 不得混入新版 target state。"""

    db_path = tmp_path / "app.db"
    target_id, first_generation = _seed_warning(db_path)
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    warning_read = Event()
    writer_committed = Event()
    original_get = FacebookTemporaryBlockWarningService.get

    def pause_after_warning_read(self: FacebookTemporaryBlockWarningService):
        snapshot = original_get(self)
        warning_read.set()
        assert writer_committed.wait(timeout=5)
        return snapshot

    monkeypatch.setattr(FacebookTemporaryBlockWarningService, "get", pause_after_warning_read)

    with ThreadPoolExecutor(max_workers=2) as pool:
        read_future = pool.submit(client.get, "/api/dashboard-cards")
        assert warning_read.wait(timeout=5)
        with SqliteApplicationContext(db_path) as app_context:
            second = app_context.services.facebook_temporary_block_warning.record(
                _finding(target_id),
                detected_at=datetime(2099, 7, 22, 2, 2, 3, tzinfo=UTC),
            )
            app_context.services.targets.pause_all_target_monitoring()
        writer_committed.set()
        before = read_future.result(timeout=10)

    monkeypatch.setattr(FacebookTemporaryBlockWarningService, "get", original_get)
    after = client.get("/api/dashboard-cards")

    assert before.status_code == 200
    assert before.json()["facebook_temporary_block_warning"]["generation"] == first_generation
    assert before.json()["cards"][0]["monitoring_action"] == "stop"
    assert after.json()["facebook_temporary_block_warning"]["generation"] == second.generation
    assert after.json()["cards"][0]["monitoring_action"] == "start"


def test_warning_presenter_uses_injected_deadline() -> None:
    """Presenter 在 deadline 前 active、到點即 inactive，便於 deterministic 驗證。"""

    snapshot = TemporaryBlockWarningSnapshot(
        generation=7,
        detected_at=datetime(2099, 7, 22, 1, 0, 0, tzinfo=UTC),
        warning_until=datetime(2099, 7, 22, 13, 0, 0, tzinfo=UTC),
        source_kind=FacebookWorkSourceKind.SCAN,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
        action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
        updated_at=datetime(2099, 7, 22, 1, 0, 0, tzinfo=UTC),
    )

    active = build_facebook_temporary_block_warning(
        snapshot,
        now=datetime(2099, 7, 22, 12, 59, 59, tzinfo=UTC),
    )
    expired = build_facebook_temporary_block_warning(
        snapshot,
        now=datetime(2099, 7, 22, 13, 0, 0, tzinfo=UTC),
    )

    assert active.active
    assert active.generation == snapshot.generation
    assert not expired.active
    assert expired.generation == -1
