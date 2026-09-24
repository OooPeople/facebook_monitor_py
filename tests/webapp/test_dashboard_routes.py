"""FastAPI Web UI tests。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.application.scan_recording_service import RecordScanRequest
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.facebook_temporary_block import FacebookActionKind
from facebook_monitor.core.facebook_temporary_block import FacebookProductOperationKind
from facebook_monitor.core.facebook_temporary_block import FacebookWorkSourceKind
from facebook_monitor.core.facebook_temporary_block import TemporaryBlockFinding
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.scan_failures import CONTENT_UNAVAILABLE_REASON
from facebook_monitor.persistence.invariants import validate_database_invariants
from facebook_monitor.persistence.repositories.facebook_temporary_block_warning import (
    FacebookTemporaryBlockWarningRepository,
)
from facebook_monitor.persistence.repositories.facebook_temporary_block_warning import (
    TemporaryBlockWarningDecodeError,
)
from facebook_monitor.persistence.repositories.latest_scan_items import LatestScanItemRepository
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.repositories.app_settings import ProfileSessionState
from facebook_monitor.persistence.sqlite_connection import SqliteConnection
from facebook_monitor.webapp.dashboard_queries import get_dashboard_view
from facebook_monitor.webapp.assets import ASSET_VERSION
from tests.helpers.webapp import FakeSchedulerManager


from tests.webapp.app_test_helpers import create_app


def test_index_and_partial_payload_show_profile_needs_login_warning(
    tmp_path: Path,
) -> None:
    """Facebook session 失效時，首頁與 partial update 都帶右上角警告。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        status = app_context.repositories.app_settings.mark_profile_needs_login(
            reason="login_required",
            source="resident_main",
        )
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    index_response = client.get("/")
    cards_response = client.get("/api/dashboard-cards")

    assert status.state == ProfileSessionState.NEEDS_LOGIN
    assert index_response.status_code == 200
    assert "Facebook 需要重新登入" in index_response.text
    payload = cards_response.json()
    warning = payload["profile_session_warning"]
    assert warning["needs_login"] is True
    assert warning["reason"] == "login_required"
    assert "重新開啟程式" in warning["message"]


def test_index_and_partial_payload_show_database_invariant_warning(
    tmp_path: Path,
) -> None:
    """污染資料只顯示 invariant 警告與支援包提示，不在 read path 靜默修復。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="corrupt",
                canonical_url="https://www.facebook.com/groups/corrupt",
                group_name="異常測試社團",
            )
        )
        connection = app_context.repositories.configs.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE target_configs SET auto_load_more = 2 WHERE target_id = ?",
            (target.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    index_response = client.get("/")
    cards_response = client.get("/api/dashboard-cards")

    assert index_response.status_code == 200
    assert "異常測試社團" in index_response.text
    assert "目前畫面讀取範圍偵測到 1 個資料 invariant 異常" in index_response.text
    assert "設定下載支援包" in index_response.text
    payload = cards_response.json()
    warning = payload["database_invariant_warning"]
    assert payload["dashboard_degraded"] is False
    assert warning["has_violations"] is True
    assert warning["violation_count"] == 1
    assert warning["tables"] == ["target_configs"]
    assert "系統不會自動修復資料" in warning["message"]
    assert target.id not in warning["message"]
    assert target.group_id not in warning["message"]


def test_database_invariant_warning_degrades_mapper_breaking_rows_without_ids(
    tmp_path: Path,
) -> None:
    """mapper 無法載入的壞 enum row 不可讓 dashboard 500 或洩漏 row id。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="mapper-corrupt",
                canonical_url="https://www.facebook.com/groups/mapper-corrupt",
                group_name="mapper 異常測試社團",
            )
        )
        connection = app_context.repositories.targets.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE targets SET target_kind = 'invalid-kind', enabled = 1, paused = 0 WHERE id = ?",
            (target.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    index_response = client.get("/")
    cards_response = client.get("/api/dashboard-cards")

    assert index_response.status_code == 200
    assert "目前畫面讀取範圍偵測到 1 個資料 invariant 異常" in index_response.text
    assert "資料暫時無法載入" in index_response.text
    assert "目前沒有 target" not in index_response.text
    payload = cards_response.json()
    warning = payload["database_invariant_warning"]
    assert payload["dashboard_degraded"] is True
    assert warning["has_violations"] is True
    assert warning["violation_count"] == 1
    assert warning["tables"] == ["targets"]
    assert "系統不會自動修復資料" in warning["message"]
    assert target.id not in warning["message"]
    assert target.group_id not in warning["message"]
    assert payload["cards"] == []


@pytest.mark.parametrize(
    ("field", "corrupt_value"),
    [
        ("generation", "dashboard-raw-generation"),
        ("source_kind", "dashboard-raw-source"),
        ("operation_kind", "dashboard-raw-operation"),
        ("action_kind", "dashboard-raw-action"),
        ("detected_at", "dashboard-raw-datetime"),
        ("warning_until", "2026-08-02T03:04:05+08:00"),
    ],
)
def test_corrupt_temporary_block_warning_degrades_all_dashboard_reads(
    tmp_path: Path,
    field: str,
    corrupt_value: object,
) -> None:
    """已由 invariant 定位的 warning 壞 row 應安全降級 full/partial reads。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="warning-row-corrupt",
                canonical_url="https://www.facebook.com/groups/warning-row-corrupt",
            )
        )
        app_context.services.facebook_temporary_block_warning.record(
            TemporaryBlockFinding(
                source_kind=FacebookWorkSourceKind.SCAN,
                operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
                action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
                target_id=target.id,
            ),
            detected_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        connection = app_context.repositories.targets.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            f"UPDATE facebook_temporary_block_warning SET {field} = ? WHERE id = 1",
            (corrupt_value,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    index_response = client.get("/")
    cards_response = client.get("/api/dashboard-cards")
    sidebar_response = client.get("/api/sidebar")

    assert index_response.status_code == 200
    assert "資料暫時無法載入" in index_response.text
    assert str(corrupt_value) not in index_response.text
    assert cards_response.status_code == 200
    payload = cards_response.json()
    assert payload["dashboard_degraded"] is True
    assert payload["database_invariant_warning"]["tables"] == [
        "facebook_temporary_block_warning"
    ]
    assert payload["facebook_temporary_block_warning"]["active"] is False
    assert payload["cards"] == []
    assert str(corrupt_value) not in cards_response.text
    assert sidebar_response.status_code == 200
    assert sidebar_response.json()["items"] == []


@pytest.mark.parametrize(
    ("detected_at", "warning_until"),
    [
        ("2026-08-01T03:04:05+00:00", "2026-08-01T03:04:05Z"),
        ("2026-08-01T03:04:05.1+00:00", "2026-08-01T03:04:05Z"),
    ],
)
def test_corrupt_warning_window_degrades_full_and_partial_dashboard_reads(
    tmp_path: Path,
    detected_at: str,
    warning_until: str,
) -> None:
    """合法 UTC ISO 的等值或反序 window 也必須以語意比較後降級。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="warning-window-corrupt",
                canonical_url="https://www.facebook.com/groups/warning-window-corrupt",
            )
        )
        app_context.services.facebook_temporary_block_warning.record(
            TemporaryBlockFinding(
                source_kind=FacebookWorkSourceKind.SCAN,
                operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
                action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
            ),
            detected_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        connection = app_context.repositories.targets.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE facebook_temporary_block_warning
            SET detected_at = ?, warning_until = ?
            WHERE id = 1
            """,
            (detected_at, warning_until),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    index_response = client.get("/")
    cards_response = client.get("/api/dashboard-cards")
    sidebar_response = client.get("/api/sidebar")

    assert index_response.status_code == 200
    assert "資料暫時無法載入" in index_response.text
    assert cards_response.status_code == 200
    payload = cards_response.json()
    assert payload["dashboard_degraded"] is True
    assert payload["database_invariant_warning"]["tables"] == [
        "facebook_temporary_block_warning"
    ]
    assert payload["facebook_temporary_block_warning"]["active"] is False
    assert payload["cards"] == []
    assert sidebar_response.status_code == 200
    assert sidebar_response.json()["items"] == []


def test_warning_decode_error_requires_matching_invariant_field(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Warning typed error 不得借用同表其他欄位的 invariant 靜默降級。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.facebook_temporary_block_warning.record(
            TemporaryBlockFinding(
                source_kind=FacebookWorkSourceKind.SCAN,
                operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
                action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
            ),
            detected_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        connection = app_context.repositories.targets.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE facebook_temporary_block_warning
            SET generation = ?
            WHERE id = 1
            """,
            ("mismatched-generation",),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    def raise_mismatched_decode_error(
        self: FacebookTemporaryBlockWarningRepository,
    ) -> None:
        raise TemporaryBlockWarningDecodeError("source_kind")

    monkeypatch.setattr(
        FacebookTemporaryBlockWarningRepository,
        "get",
        raise_mismatched_decode_error,
    )

    with pytest.raises(TemporaryBlockWarningDecodeError) as error:
        get_dashboard_view(db_path)

    assert error.value.field == "source_kind"


def test_database_invariant_warning_skips_inactive_corrupt_target_row(
    tmp_path: Path,
) -> None:
    """inactive 壞 target row 不應讓 dashboard 全頁降級或遮蔽正常 target。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        normal = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="normal",
                canonical_url="https://www.facebook.com/groups/normal",
                group_name="正常社團",
            )
        )
        corrupt = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="inactive-corrupt",
                canonical_url="https://www.facebook.com/groups/inactive-corrupt",
                group_name="inactive 壞資料",
            )
        )
        connection = app_context.repositories.targets.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE targets SET target_kind = 'invalid-kind', paused = 1 WHERE id = ?",
            (corrupt.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    index_response = client.get("/")
    cards_response = client.get("/api/dashboard-cards")

    assert index_response.status_code == 200
    assert "正常社團" in index_response.text
    assert "inactive 壞資料" not in index_response.text
    assert "資料暫時無法載入" not in index_response.text
    payload = cards_response.json()
    warning = payload["database_invariant_warning"]
    assert payload["dashboard_degraded"] is False
    assert warning["has_violations"] is True
    assert warning["tables"] == ["targets"]
    assert [card["target_id"] for card in payload["cards"]] == [normal.id]
    assert corrupt.id not in warning["message"]


def test_database_invariant_warning_skips_inactive_corrupt_runtime_row(
    tmp_path: Path,
) -> None:
    """inactive runtime 壞 enum 不應讓 dashboard/sidebar partial update 500。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        normal = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="normal",
                canonical_url="https://www.facebook.com/groups/normal",
                group_name="正常社團",
            )
        )
        corrupt = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="inactive-runtime-corrupt",
                canonical_url="https://www.facebook.com/groups/inactive-runtime-corrupt",
                group_name="inactive runtime 壞資料",
            )
        )
        app_context.services.targets.pause_target_monitoring(corrupt.id)
        connection = app_context.repositories.runtime_states.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE target_runtime_state SET runtime_status = ? WHERE target_id = ?",
            ("banana", corrupt.id),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    index_response = client.get("/")
    cards_response = client.get("/api/dashboard-cards")
    sidebar_response = client.get("/api/sidebar")

    assert index_response.status_code == 200
    assert cards_response.status_code == 200
    assert sidebar_response.status_code == 200
    assert "正常社團" in index_response.text
    assert "inactive runtime 壞資料" not in index_response.text
    payload = cards_response.json()
    assert payload["dashboard_degraded"] is False
    assert payload["database_invariant_warning"]["tables"] == ["target_runtime_state"]
    assert [card["target_id"] for card in payload["cards"]] == [normal.id]
    assert [item["target_id"] for item in sidebar_response.json()["items"]] == [normal.id]


def test_database_invariant_warning_skips_inactive_missing_runtime_updated_at(
    tmp_path: Path,
) -> None:
    """inactive runtime 缺 required datetime 也應走 invariant skip，不讓 dashboard 500。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        normal = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="normal",
                canonical_url="https://www.facebook.com/groups/normal",
                group_name="正常社團",
            )
        )
        corrupt = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="inactive-runtime-datetime",
                canonical_url="https://www.facebook.com/groups/inactive-runtime-datetime",
                group_name="inactive runtime 日期壞資料",
            )
        )
        app_context.services.targets.pause_target_monitoring(corrupt.id)
        connection = app_context.repositories.runtime_states.connection
        connection.execute(
            "UPDATE target_runtime_state SET updated_at = ? WHERE target_id = ?",
            ("", corrupt.id),
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/api/dashboard-cards")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dashboard_degraded"] is False
    assert payload["database_invariant_warning"]["tables"] == ["target_runtime_state"]
    assert [card["target_id"] for card in payload["cards"]] == [normal.id]


def test_database_invariant_warning_degrades_corrupt_latest_scan_datetime(
    tmp_path: Path,
) -> None:
    """latest scan mapper datetime 壞掉時，dashboard 應降級顯示 invariant warning。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="latest-datetime-corrupt",
                canonical_url="https://www.facebook.com/groups/latest-datetime-corrupt",
                group_name="latest datetime 壞資料",
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="bad-latest-datetime",
                    item_index=0,
                    text="日期壞掉的最近掃描",
                )
            ],
        )
        app_context.repositories.latest_scan_items.connection.execute(
            "UPDATE latest_scan_items SET scanned_at = ? WHERE target_id = ?",
            ("not-a-datetime", target.id),
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get("/api/dashboard-cards")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dashboard_degraded"] is True
    assert payload["database_invariant_warning"]["tables"] == ["latest_scan_items"]
    assert payload["cards"] == []


def test_database_invariant_warning_degrades_corrupt_outbox_summary_datetime(
    tmp_path: Path,
) -> None:
    """outbox summary datetime 壞掉時，dashboard 應降級而不是未處理 500。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="outbox-datetime-corrupt",
                canonical_url="https://www.facebook.com/groups/outbox-datetime-corrupt",
                group_name="outbox datetime 壞資料",
            )
        )
        app_context.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:outbox-datetime:desktop",
                target_id=target.id,
                item_key="outbox-datetime",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.DESKTOP,
                title="title",
                message="message",
            )
        )
        app_context.repositories.notification_outbox.connection.execute(
            "UPDATE notification_outbox SET updated_at = ? WHERE target_id = ?",
            ("outbox-bad-datetime", target.id),
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    cards_response = client.get("/api/dashboard-cards")
    card_response = client.get(f"/api/targets/{target.id}/card")

    assert cards_response.status_code == 200
    cards_payload = cards_response.json()
    assert cards_payload["dashboard_degraded"] is True
    assert cards_payload["database_invariant_warning"]["tables"] == ["notification_outbox"]
    assert cards_payload["cards"] == []
    assert card_response.status_code == 503


def test_dashboard_scope_ignores_unloaded_history_but_full_audit_reports_it(
    tmp_path: Path,
) -> None:
    """Dashboard 只驗 preview rows；全庫 audit 仍可抓到較舊壞 history。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="scoped-dashboard-history",
                canonical_url="https://www.facebook.com/groups/scoped-dashboard-history",
                group_name="dashboard scope 測試",
            )
        )
    app = create_app(db_path=db_path, profile_dir=tmp_path / "profile")
    with SqliteApplicationContext(db_path) as app_context:
        for index in range(6):
            recorded_at = app.state.session_started_at + timedelta(seconds=index + 1)
            app_context.repositories.match_history.add(
                MatchHistoryEntry(
                    target_id=target.id,
                    group_id=target.group_id,
                    group_name=target.group_name,
                    item_kind=ItemKind.POST,
                    item_key=f"dashboard-scope-{index}",
                    text=f"scope item {index}",
                    include_rule="scope",
                    recorded_at=recorded_at,
                    created_at=recorded_at,
                )
            )
        app_context.repositories.match_history.connection.execute(
            "UPDATE match_history SET created_at = ? WHERE item_key = ?",
            ("not-a-datetime", "dashboard-scope-0"),
        )
        violations = validate_database_invariants(
            app_context.repositories.match_history.connection
        )

    response = TestClient(app).get("/api/dashboard-cards")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dashboard_degraded"] is False
    assert payload["database_invariant_warning"]["has_violations"] is False
    assert payload["cards"][0]["hit_record_total_count"] == 6
    assert any(
        violation.table == "match_history" and violation.field == "created_at"
        for violation in violations
    )


def test_dashboard_scope_ignores_unused_terminal_outbox_datetimes(
    tmp_path: Path,
) -> None:
    """Outbox summary 不映射 terminal datetime，scope warning 也不得宣稱已驗該欄位。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="terminal-outbox-scope",
                canonical_url="https://www.facebook.com/groups/terminal-outbox-scope",
                group_name="terminal outbox scope",
            )
        )
        app_context.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:terminal:desktop",
                target_id=target.id,
                item_key="terminal",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.DESKTOP,
                title="title",
                message="message",
            )
        )
        connection = app_context.repositories.notification_outbox.connection
        connection.execute(
            """
            UPDATE notification_outbox
            SET status = 'sent', created_at = ?, updated_at = ?
            WHERE target_id = ?
            """,
            ("bad-terminal-created", "bad-terminal-updated", target.id),
        )
        violations = validate_database_invariants(connection)

    response = TestClient(
        create_app(db_path=db_path, profile_dir=tmp_path / "profile")
    ).get("/api/dashboard-cards")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dashboard_degraded"] is False
    assert payload["database_invariant_warning"]["has_violations"] is False
    assert any(
        violation.table == "notification_outbox"
        and violation.field in {"created_at", "updated_at"}
        for violation in violations
    )


def test_target_card_scope_ignores_other_target_corruption(tmp_path: Path) -> None:
    """單卡 read 不掃描其他 target 的 latest rows。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        current = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="current-card-scope",
                canonical_url="https://www.facebook.com/groups/current-card-scope",
                group_name="目前單卡",
            )
        )
        other = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="other-card-scope",
                canonical_url="https://www.facebook.com/groups/other-card-scope",
                group_name="其他單卡",
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            other.id,
            [
                LatestScanItem(
                    target_id=other.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="other-bad-latest",
                    item_index=0,
                    text="其他 target 壞資料",
                )
            ],
        )
        app_context.repositories.latest_scan_items.connection.execute(
            "UPDATE latest_scan_items SET scanned_at = ? WHERE target_id = ?",
            ("not-a-datetime", other.id),
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get(f"/api/targets/{current.id}/card")

    assert response.status_code == 200
    assert response.json()["target_id"] == current.id


@pytest.mark.parametrize("corrupt_value", (-1, 0, "not-an-integer", 1.5, 11))
def test_active_corrupt_max_items_degrades_without_latest_item_query(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    corrupt_value: object,
) -> None:
    """Active max-items 壞資料須降級/503，且不得流入 latest-items repository。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id=f"active-corrupt-limit-{corrupt_value}",
                canonical_url=(
                    "https://www.facebook.com/groups/"
                    f"active-corrupt-limit-{corrupt_value}"
                ),
                group_name="active corrupt max items",
            )
        )
        app_context.services.targets.restart_target_monitoring(target.id)
        connection = app_context.repositories.configs.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE target_configs SET max_items_per_scan = ? WHERE target_id = ?",
            (corrupt_value, target.id),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    def fail_latest_items_query(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("invalid max_items_per_scan reached latest-items repository")

    monkeypatch.setattr(
        LatestScanItemRepository,
        "list_by_targets",
        fail_latest_items_query,
    )
    monkeypatch.setattr(
        LatestScanItemRepository,
        "list_by_target",
        fail_latest_items_query,
    )
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    index_response = client.get("/")
    cards_response = client.get("/api/dashboard-cards")
    card_response = client.get(f"/api/targets/{target.id}/card")

    assert index_response.status_code == 200
    assert "資料暫時無法載入" in index_response.text
    assert "目前沒有 target" not in index_response.text
    assert cards_response.status_code == 200
    cards_payload = cards_response.json()
    assert cards_payload["dashboard_degraded"] is True
    assert cards_payload["cards"] == []
    assert cards_payload["database_invariant_warning"]["tables"] == ["target_configs"]
    assert card_response.status_code == 503


@pytest.mark.parametrize("enabled,paused", ((False, False), (True, True)))
def test_inactive_corrupt_max_items_is_skipped_with_warning(
    tmp_path: Path,
    enabled: bool,
    paused: bool,
) -> None:
    """Inactive/paused 壞 config 不拖垮首頁，單卡維持 404。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        valid = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id=f"valid-neighbor-{enabled}-{paused}",
                canonical_url=(
                    "https://www.facebook.com/groups/"
                    f"valid-neighbor-{enabled}-{paused}"
                ),
                group_name="valid neighbor",
            )
        )
        corrupt = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id=f"inactive-corrupt-{enabled}-{paused}",
                canonical_url=(
                    "https://www.facebook.com/groups/"
                    f"inactive-corrupt-{enabled}-{paused}"
                ),
                group_name="inactive corrupt max items",
            )
        )
        connection = app_context.repositories.configs.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE target_configs SET max_items_per_scan = 11 WHERE target_id = ?",
            (corrupt.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")
        connection.execute(
            "UPDATE targets SET enabled = ?, paused = ? WHERE id = ?",
            (int(enabled), int(paused), corrupt.id),
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    cards_response = client.get("/api/dashboard-cards")
    card_response = client.get(f"/api/targets/{corrupt.id}/card")

    assert cards_response.status_code == 200
    cards_payload = cards_response.json()
    assert cards_payload["dashboard_degraded"] is False
    assert [card["target_id"] for card in cards_payload["cards"]] == [valid.id]
    assert cards_payload["database_invariant_warning"]["tables"] == ["target_configs"]
    assert card_response.status_code == 404


def test_corrupt_sidebar_template_max_items_degrades_dashboard(tmp_path: Path) -> None:
    """Sidebar template max-items 壞資料須安全降級，不影響單卡 scope。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="sidebar-template-corrupt",
                canonical_url="https://www.facebook.com/groups/sidebar-template-corrupt",
                group_name="sidebar template corrupt",
            )
        )
        group = app_context.services.sidebar_layout.create_group("corrupt template")
        app_context.services.sidebar_layout.save_placements([(group.id, [target.id])])
        connection = app_context.repositories.sidebar_layout.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE sidebar_group_config_templates
            SET max_items_per_scan = 'not-an-integer'
            WHERE sidebar_group_id = ?
            """,
            (group.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    cards_response = client.get("/api/dashboard-cards")
    card_response = client.get(f"/api/targets/{target.id}/card")

    assert cards_response.status_code == 200
    cards_payload = cards_response.json()
    assert cards_payload["dashboard_degraded"] is True
    assert cards_payload["cards"] == []
    assert cards_payload["database_invariant_warning"]["tables"] == [
        "sidebar_group_config_templates"
    ]
    assert card_response.status_code == 200


@pytest.mark.parametrize("configured_limit", (None, 1, 3, 10))
def test_valid_or_missing_max_items_keeps_validator_repository_scope_parity(
    tmp_path: Path,
    configured_limit: int | None,
) -> None:
    """合法與缺 config 的 validator/repository 都只讀同一個 bounded prefix。"""

    expected_limit = (
        configured_limit
        if configured_limit is not None
        else PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan
    )
    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id=f"valid-limit-{configured_limit}",
                canonical_url=(
                    "https://www.facebook.com/groups/"
                    f"valid-limit-{configured_limit}"
                ),
                group_name="valid max items parity",
                config=(
                    TargetConfigPatch(max_items_per_scan=configured_limit)
                    if configured_limit is not None
                    else TargetConfigPatch()
                ),
            )
        )
        if configured_limit is None:
            app_context.repositories.configs.connection.execute(
                "DELETE FROM target_configs WHERE target_id = ?",
                (target.id,),
            )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key=f"valid-limit-item-{index}",
                    item_index=index,
                    text=f"valid limit item {index}",
                )
                for index in range(expected_limit + 1)
            ],
        )
        app_context.repositories.latest_scan_items.connection.execute(
            "UPDATE latest_scan_items SET scanned_at = ? WHERE item_index = ?",
            ("outside-bounded-prefix", expected_limit),
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    cards_response = client.get("/api/dashboard-cards")
    card_response = client.get(f"/api/targets/{target.id}/card")

    assert cards_response.status_code == 200
    assert cards_response.json()["dashboard_degraded"] is False
    assert card_response.status_code == 200
    preview_html = card_response.json()["latest_scan_preview_html"]
    for index in range(expected_limit):
        assert f"valid limit item {index}" in preview_html
    assert f"valid limit item {expected_limit}" not in preview_html


def test_web_reads_do_not_call_full_database_invariant_audit(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """日常 dashboard/card/hit reads 不得再進 persistence full audit。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="no-full-audit",
                canonical_url="https://www.facebook.com/groups/no-full-audit",
                group_name="不跑全庫 audit",
            )
        )

    statements: list[str] = []
    original_enter = SqliteConnection.__enter__

    def traced_enter(connection_manager: SqliteConnection) -> SqliteConnection:
        entered = original_enter(connection_manager)
        entered.require_connection().set_trace_callback(statements.append)
        return entered

    def fail_full_audit(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Web read entered full database invariant audit")

    monkeypatch.setattr(SqliteConnection, "__enter__", traced_enter)
    monkeypatch.setattr(
        "facebook_monitor.persistence.invariants.validate_database_invariants",
        fail_full_audit,
    )
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    assert client.get("/api/dashboard-cards").status_code == 200
    assert client.get(f"/api/targets/{target.id}/card").status_code == 200
    assert client.get(f"/api/targets/{target.id}/hit-records/preview").status_code == 200
    assert client.get(f"/api/targets/{target.id}/hit-records/count").status_code == 200
    assert client.get(f"/api/targets/{target.id}/hit-records").status_code == 200
    normalized_statements = [" ".join(statement.lower().split()) for statement in statements]
    assert not any(" from seen_items " in statement for statement in normalized_statements)
    assert not any(" from logical_items " in statement for statement in normalized_statements)


def test_target_card_returns_404_for_inactive_corrupt_target_row(
    tmp_path: Path,
) -> None:
    """單卡 partial read 對 inactive 壞 target row 應回 404，不應 500。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        corrupt = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="inactive-card-corrupt",
                canonical_url="https://www.facebook.com/groups/inactive-card-corrupt",
                group_name="inactive 單卡壞資料",
            )
        )
        connection = app_context.repositories.targets.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE targets SET target_kind = 'invalid-kind', paused = 1 WHERE id = ?",
            (corrupt.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get(f"/api/targets/{corrupt.id}/card")

    assert response.status_code == 404


def test_target_card_returns_404_for_inactive_corrupt_runtime_row(
    tmp_path: Path,
) -> None:
    """單卡 partial read 對 inactive runtime 壞 row 應回 404，不應 500。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        corrupt = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="inactive-runtime-card-corrupt",
                canonical_url="https://www.facebook.com/groups/inactive-runtime-card-corrupt",
                group_name="inactive runtime 單卡壞資料",
            )
        )
        app_context.services.targets.pause_target_monitoring(corrupt.id)
        connection = app_context.repositories.runtime_states.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE target_runtime_state SET runtime_status = ? WHERE target_id = ?",
            ("banana", corrupt.id),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get(f"/api/targets/{corrupt.id}/card")

    assert response.status_code == 404


def test_target_card_returns_503_for_active_corrupt_runtime_row(
    tmp_path: Path,
) -> None:
    """單卡 partial read 對 active runtime 壞 row 應明確失敗，不可修復或 404。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        corrupt = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="active-runtime-card-corrupt",
                canonical_url="https://www.facebook.com/groups/active-runtime-card-corrupt",
                group_name="active runtime 單卡壞資料",
            )
        )
        app_context.services.targets.restart_target_monitoring(corrupt.id)
        connection = app_context.repositories.runtime_states.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE target_runtime_state SET runtime_status = ? WHERE target_id = ?",
            ("banana", corrupt.id),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get(f"/api/targets/{corrupt.id}/card")

    assert response.status_code == 503


def test_database_invariant_warning_does_not_degrade_unrelated_value_error(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """即使 DB 有 invariant violation，非 enum mapper 錯誤仍不可被降級吞掉。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="mapper-corrupt",
                canonical_url="https://www.facebook.com/groups/mapper-corrupt",
                group_name="mapper 異常測試社團",
            )
        )
        connection = app_context.repositories.targets.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE targets SET target_kind = 'invalid-kind' WHERE id = ?",
            (target.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    def raise_unrelated_value_error(self: TargetRepository) -> list[object]:
        raise ValueError("'x' is not a valid SurpriseBug")

    monkeypatch.setattr(TargetRepository, "list_all", raise_unrelated_value_error)

    with pytest.raises(ValueError, match="SurpriseBug"):
        get_dashboard_view(db_path)


def test_database_invariant_warning_does_not_degrade_unrelated_datetime_value_error(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """datetime invariant 存在時，也不可吞掉不同壞值的 mapper-like ValueError。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="datetime-mapper-bug",
                canonical_url="https://www.facebook.com/groups/datetime-mapper-bug",
                group_name="datetime mapper bug",
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="datetime-mapper-bug",
                    item_index=0,
                    text="datetime mapper bug",
                )
            ],
        )
        app_context.repositories.latest_scan_items.connection.execute(
            "UPDATE latest_scan_items SET scanned_at = ? WHERE target_id = ?",
            ("stored-bad-datetime", target.id),
        )

    def raise_unrelated_datetime_value_error(
        self: LatestScanItemRepository,
        target_ids: list[str],
        *,
        limit_per_target: int,
    ) -> dict[str, list[object]]:
        raise ValueError("Invalid isoformat string: 'unrelated-bug'")

    monkeypatch.setattr(
        LatestScanItemRepository,
        "list_by_targets",
        raise_unrelated_datetime_value_error,
    )

    with pytest.raises(ValueError, match="unrelated-bug"):
        get_dashboard_view(db_path)


def test_dashboard_uses_external_versioned_scripts_without_importmap(tmp_path: Path) -> None:
    """Dashboard HTML 不再需要 inline importmap，入口 script 仍保留版本 key。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get("/")

    assert response.status_code == 200
    assert '<script type="importmap">' not in response.text
    assert f"/static/dashboard/main.js?v={ASSET_VERSION}" in response.text
    assert '<script id="page-feedback" type="application/json">' not in response.text


def test_index_renders_runtime_state_and_error(tmp_path: Path) -> None:
    """首頁會顯示 scheduler runtime state 與 last error。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        running_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="掃描測試社團",
            )
        )
        error_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
                group_name="錯誤社團",
            )
        )
        stopped_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="333",
                canonical_url="https://www.facebook.com/groups/333",
                group_name="停止社團",
            )
        )
        idle_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="444",
                canonical_url="https://www.facebook.com/groups/444",
                group_name="啟用等待社團",
            )
        )
        app_context.services.targets.restart_target_monitoring(running_target.id)
        app_context.services.targets.mark_target_running(running_target.id, "worker-1")
        app_context.services.targets.restart_target_monitoring(error_target.id)
        app_context.services.targets.mark_target_error(error_target.id, "login_required: 需要登入")
        app_context.services.targets.pause_target_monitoring(stopped_target.id)
        app_context.services.targets.restart_target_monitoring(idle_target.id)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "已啟用" in response.text
    assert "掃描中" in response.text
    assert "錯誤" in response.text
    assert "需要重新登入" in response.text
    assert "Facebook 要求重新登入" in response.text
    assert "login_required: 需要登入" not in response.text
    assert "已停止" in response.text
    assert "閒置" not in response.text
    assert "執行中" not in response.text


def test_index_does_not_render_queue_position_runtime_note(tmp_path: Path) -> None:
    """排隊資訊不應以會推動 card 高度的 queue_position raw note 顯示。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="排隊測試社團",
            )
        )
        app_context.services.targets.restart_target_monitoring(target.id)
        app_context.services.targets.mark_target_queued(target.id, "due")

    scheduler_manager = FakeSchedulerManager()
    scheduler_manager.running = True
    scheduler_manager.queued_target_ids = (target.id,)
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.get("/")

    assert response.status_code == 200
    assert "排隊測試社團" in response.text
    assert "排隊中" in response.text
    assert "queue_position=" not in response.text


def test_dashboard_card_payload_labels_content_unavailable_failure(
    tmp_path: Path,
) -> None:
    """dashboard card 以持續無法查看呈現 terminal，不臆測連結永久失效。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message=(
                    "content_unavailable: Facebook content is unavailable or no longer visible."
                ),
                metadata={
                    "reason": CONTENT_UNAVAILABLE_REASON,
                    "worker": "resident_main",
                    "target_kind": "posts",
                    "retryable": False,
                    "retry_streak": 3,
                    "retry_limit": 3,
                },
            )
        )
        app_context.services.targets.restart_target_monitoring(target.id)
        app_context.services.targets.mark_target_error(
            target.id,
            "content_unavailable: Facebook content is unavailable or no longer visible.",
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/api/dashboard-cards")

    assert response.status_code == 200
    card_payload = response.json()["cards"][0]
    assert card_payload["has_latest_failed_scan"] is True
    assert card_payload["latest_error_indicator_label"] == "內容無法查看"
    assert card_payload["latest_error_indicator_kind"] == "content-unavailable"
    assert card_payload["status_label"] == "錯誤"
    assert (
        card_payload["runtime_error"]
        == "內容持續無法查看：Facebook 在連續三次頁面確認中都顯示目前無法查看此內容，監視已停止。"
    )
    assert card_payload["next_refresh_label"] == "下次刷新：未排程"
    assert "Facebook 連續三次顯示目前無法查看此內容" in card_payload["latest_error_indicator_title"]
    assert "status=failed · reason=Facebook 內容無法查看" in card_payload["latest_scan_diagnostics_summary"]
    assert "failure_reason=Facebook 內容無法查看" in card_payload["latest_scan_diagnostics_text"]
    assert "內容無法查看" in card_payload["card_summary_html"]


def test_dashboard_card_payload_keeps_legacy_unavailable_record_neutral(
    tmp_path: Path,
) -> None:
    """舊版一次即停止的紀錄仍可辨識，但不得誤稱已確認三次。"""

    db_path = tmp_path / "app.db"
    legacy_error = "連結已失效：Facebook 顯示目前無法查看此內容。"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="legacy-content-unavailable",
                canonical_url="https://www.facebook.com/groups/legacy-content-unavailable",
                group_name="舊紀錄測試社團",
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message=legacy_error,
                metadata={"worker": "resident_main", "retryable": False},
            )
        )
        app_context.services.targets.restart_target_monitoring(target.id)
        app_context.services.targets.mark_target_error(target.id, legacy_error)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/api/dashboard-cards")

    assert response.status_code == 200
    card_payload = response.json()["cards"][0]
    assert card_payload["latest_error_indicator_label"] == "內容無法查看"
    assert card_payload["latest_error_indicator_kind"] == "content-unavailable"
    assert (
        card_payload["runtime_error"]
        == "內容無法查看：Facebook 顯示目前無法查看此內容，監視已停止。"
    )
    assert (
        card_payload["latest_error_indicator_title"]
        == "Facebook 顯示目前無法查看此內容，監視已停止。"
    )
    assert "連續三次" not in card_payload["runtime_error"]


def test_dashboard_card_payload_does_not_keep_content_unavailable_after_success(
    tmp_path: Path,
) -> None:
    """內容無法查看後若已有成功掃描，不應繼續顯示目前仍不可見。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message=(
                    "content_unavailable: Facebook content is unavailable or no longer visible."
                ),
                metadata={
                    "reason": CONTENT_UNAVAILABLE_REASON,
                    "worker": "resident_main",
                    "target_kind": "posts",
                    "retryable": False,
                },
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                item_count=1,
                matched_count=0,
                metadata={
                    "worker": "posts_scan",
                    "collection_strategy": "feed_visible_window",
                    "candidate_count": 1,
                    "round_count": 1,
                    "stop_reason": "target_count_reached",
                },
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/api/dashboard-cards")

    assert response.status_code == 200
    card_payload = response.json()["cards"][0]
    assert card_payload["has_latest_failed_scan"] is True
    assert card_payload["latest_error_indicator_label"] == "最近有錯誤"
    assert card_payload["latest_error_indicator_kind"] == "error"
    assert "曾偵測到 Facebook 內容無法查看" in card_payload["card_summary_html"]
    assert (
        "status=failed · reason=Facebook 內容無法查看"
        not in card_payload["latest_scan_diagnostics_summary"]
    )


def test_dashboard_card_payload_localizes_page_load_timeout_errors(
    tmp_path: Path,
) -> None:
    """Playwright raw navigation error 不應直接出現在 dashboard payload。"""

    db_path = tmp_path / "app.db"
    raw_error = (
        "page_load_timeout: Page.evaluate: Execution context was destroyed, "
        "most likely because of a navigation."
    )
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message=raw_error,
                metadata={
                    "reason": "page_load_timeout",
                    "worker": "resident_main",
                    "target_kind": "posts",
                    "retryable": False,
                },
            )
        )
        app_context.services.targets.restart_target_monitoring(target.id)
        app_context.services.targets.mark_target_error(target.id, raw_error)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/api/dashboard-cards")

    assert response.status_code == 200
    card_payload = response.json()["cards"][0]
    assert "頁面載入逾時" in card_payload["runtime_error"]
    assert "頁面載入逾時" in card_payload["latest_error_indicator_title"]
    assert "error=頁面載入逾時" in card_payload["latest_scan_diagnostics_text"]
    assert "Page.evaluate" not in card_payload["runtime_error"]
    assert "Execution context was destroyed" not in card_payload["latest_error_indicator_title"]
    assert "most likely because of a navigation" not in card_payload["latest_scan_diagnostics_text"]


def test_dashboard_card_payload_shows_retrying_page_load_timeout(
    tmp_path: Path,
) -> None:
    """未達上限的 page_load_timeout 只顯示將重試，不顯示 runtime error。"""

    db_path = tmp_path / "app.db"
    raw_error = (
        "page_load_timeout: Page.evaluate: Execution context was destroyed, "
        "most likely because of a navigation."
    )
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="測試社團",
            )
        )
        app_context.services.targets.restart_target_monitoring(target.id)
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message=raw_error,
                metadata={
                    "reason": "page_load_timeout",
                    "worker": "resident_main",
                    "target_kind": "posts",
                    "retryable": True,
                    "runtime_action": "will_retry",
                    "retry_streak": 1,
                    "retry_limit": 3,
                },
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/api/dashboard-cards")

    assert response.status_code == 200
    card_payload = response.json()["cards"][0]
    assert card_payload["runtime_error"] == ""
    assert card_payload["latest_error_indicator_label"] == "將重試"
    assert card_payload["latest_error_indicator_kind"] == "retrying"
    assert "1/3" in card_payload["latest_error_indicator_title"]
    assert "頁面載入逾時" in card_payload["latest_error_indicator_title"]
    assert "retryable=True" in card_payload["latest_scan_diagnostics_text"]
    assert "runtime_action=will_retry" in card_payload["latest_scan_diagnostics_text"]
    assert "retry_streak=1" in card_payload["latest_scan_diagnostics_text"]
    assert "Page.evaluate" not in card_payload["latest_error_indicator_title"]
    assert "Execution context was destroyed" not in card_payload["latest_scan_diagnostics_text"]


def test_dashboard_card_payload_shows_retrying_content_unavailable(
    tmp_path: Path,
) -> None:
    """內容不可見未達確認上限時顯示將重試，不先宣告連結失效。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="retrying-content-unavailable",
                canonical_url=(
                    "https://www.facebook.com/groups/retrying-content-unavailable"
                ),
                group_name="測試社團",
            )
        )
        app_context.services.targets.restart_target_monitoring(target.id)
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message=(
                    "content_unavailable: Facebook 顯示目前無法查看此內容。"
                ),
                metadata={
                    "reason": "content_unavailable",
                    "worker": "resident_main",
                    "target_kind": "posts",
                    "retryable": True,
                    "runtime_action": "will_retry",
                    "retry_streak": 1,
                    "retry_limit": 3,
                    "retry_delay_seconds": 30,
                },
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/api/dashboard-cards")

    assert response.status_code == 200
    card_payload = response.json()["cards"][0]
    assert card_payload["runtime_error"] == ""
    assert card_payload["latest_error_indicator_label"] == "將重試"
    assert card_payload["latest_error_indicator_kind"] == "retrying"
    assert "1/3" in card_payload["latest_error_indicator_title"]
    assert "等待 30 秒" in card_payload["latest_error_indicator_title"]
    assert "內容無法查看" not in card_payload["latest_error_indicator_title"]


def test_dashboard_view_model_includes_sidebar_preview_and_settings_summary(
    tmp_path: Path,
) -> None:
    """dashboard read model 會帶入 sidebar、hit preview 與設定摘要。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="測試社團",
                config=TargetConfigPatch(
                    fixed_refresh_sec=None,
                    min_refresh_sec=25,
                    max_refresh_sec=35,
                    jitter_enabled=True,
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="latest-1",
                    item_index=0,
                    author="王小明",
                    text="最近掃描內容",
                    display_text="最近掃描內容\n第二行",
                    matched_keyword="票券",
                ),
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="latest-2",
                    item_index=1,
                    author="林小美",
                    text="較新的最近掃描內容",
                    matched_keyword="票券",
                ),
            ],
        )
        app_context.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                item_kind=ItemKind.POST,
                item_key="history-1",
                author="陳小華",
                text="歷史命中內容",
                display_text="歷史命中內容\n第二行",
                include_rule="票券",
            )
        )

    dashboard = get_dashboard_view(db_path)
    row = dashboard.rows[0]
    latest_preview = row.preview_presenter.latest_scan_preview_rows[0]
    hit_preview = row.preview_presenter.hit_record_preview_rows[0]

    assert dashboard.sidebar_items[0].display_name == "測試社團"
    assert dashboard.sidebar_items[0].mode_label == "貼文"
    assert dashboard.sidebar_items[0].mode_class == "posts"
    assert dashboard.sidebar_items[0].hit_count == 1
    assert "命中 1 筆" in dashboard.sidebar_items[0].status_summary
    assert row.hit_record_total_count == 1
    assert row.preview_presenter.hit_records_heading == "命中紀錄（1）"
    assert row.settings_presenter.settings_summary.lines[0].icon_key == "refresh"
    assert row.settings_presenter.settings_summary.lines[0].label == "刷新"
    assert row.settings_presenter.settings_summary.lines[0].value == "浮動 25-35 秒"
    assert row.settings_presenter.settings_summary.lines[-1].icon_key == "notification"
    assert row.settings_presenter.settings_summary.lines[-1].label == "通知"
    assert row.settings_presenter.settings_summary.lines[-1].value == "ntfy"
    assert latest_preview.author_name == "王小明"
    assert latest_preview.badge_kind == "hit"
    assert latest_preview.content_preview == "最近掃描內容 第二行"
    assert latest_preview.link_label == "開啟連結"
    assert not latest_preview.has_debug
    assert hit_preview.author_name == "陳小華"
    assert hit_preview.badge_text == "命中: 票券"
    assert hit_preview.content_preview == "歷史命中內容 第二行"
    assert hit_preview.link_label == "開啟連結"
    assert not hit_preview.has_debug

    response = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile")).get("/")
    assert response.status_code == 200
    assert "最近掃描" in response.text
    assert "命中紀錄 0" in response.text
    assert "最近掃描內容 第二行" in response.text


def test_dashboard_partial_payload_changes_sidebar_layout_signature_for_groups(
    tmp_path: Path,
) -> None:
    """dashboard partial payload 需帶 group/order 簽章，讓前端遇到結構變更時 reload。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="第一個社團",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
                group_name="第二個社團",
            )
        )
        group = app_context.services.sidebar_layout.create_group("工作")
        app_context.services.sidebar_layout.save_placements(
            [(group.id, [first.id]), (None, [second.id])]
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    page_response = client.get("/")
    first_payload = client.get("/api/dashboard-cards").json()
    first_signature = first_payload["sidebar"]["layout_signature"]
    first_template_signature = first_payload["sidebar"]["template_signature"]

    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.sidebar_layout.save_placements(
            [(group.id, [first.id, second.id]), (None, [])]
        )
    second_payload = client.get("/api/dashboard-cards").json()
    second_signature = second_payload["sidebar"]["layout_signature"]

    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.sidebar_layout.rename_group(group.id, "重新命名")
    renamed_payload = client.get("/api/dashboard-cards").json()

    with SqliteApplicationContext(db_path) as app_context:
        template = app_context.services.sidebar_layout.get_template_or_default(group.id)
        app_context.services.sidebar_layout.save_template(
            replace(
                template,
                include_keywords=("模板更新",),
                updated_at=template.updated_at + timedelta(seconds=1),
            )
        )
    template_payload = client.get("/api/dashboard-cards").json()

    assert page_response.status_code == 200
    assert f'data-sidebar-layout-signature="{first_signature}"' in page_response.text
    assert f'data-sidebar-template-signature="{first_template_signature}"' in page_response.text
    assert first_signature
    assert first_template_signature
    assert second_signature != first_signature
    assert renamed_payload["sidebar"]["layout_signature"] != second_signature
    assert template_payload["sidebar"]["layout_signature"] == renamed_payload["sidebar"][
        "layout_signature"
    ]
    assert (
        template_payload["sidebar"]["template_signature"]
        != renamed_payload["sidebar"]["template_signature"]
    )
    assert [item["target_id"] for item in second_payload["sidebar"]["items"]] == [
        first.id,
        second.id,
    ]


def test_dashboard_sidebar_template_signature_is_stable_without_template_row(
    tmp_path: Path,
) -> None:
    """缺少 template row 的舊資料不應讓 sidebar partial payload 每次讀取都漂移。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="舊資料社團",
            )
        )
        group = app_context.services.sidebar_layout.create_group("舊群組")
        app_context.services.sidebar_layout.save_placements([(group.id, [target.id])])
        app_context.repositories.sidebar_layout.connection.execute(
            "DELETE FROM sidebar_group_config_templates WHERE sidebar_group_id = ?",
            (group.id,),
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    first_payload = client.get("/api/dashboard-cards").json()
    second_payload = client.get("/api/dashboard-cards").json()

    assert first_payload["sidebar"]["template_signature"]
    assert first_payload["sidebar"]["template_signature"] == second_payload["sidebar"][
        "template_signature"
    ]


def test_index_renders_scan_guard_skip_reason(tmp_path: Path) -> None:
    """首頁會顯示同 target 重入被 guard 擋下的原因。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="重入測試社團",
            )
        )
        app_context.services.targets.mark_target_running(target.id, "worker-a")
        locked_state = app_context.services.targets.try_claim_target_running(
            target.id,
            "worker-b",
        )

    assert locked_state is None

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "重入測試社團" in response.text
    assert "監視項目已在掃描中，本輪排程已略過。" in response.text
    assert "scan_guard_skipped: target_already_running" not in response.text
