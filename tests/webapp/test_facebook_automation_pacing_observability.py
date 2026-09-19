"""Facebook automation pacing runtime diagnostics 契約測試。"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.facebook_automation_pacing_observability import (
    build_facebook_automation_pacing_safe_snapshot,
)
from facebook_monitor.automation.profile_identity import (
    load_or_create_managed_profile_identity,
)
from facebook_monitor.core.facebook_automation_pacing import (
    FacebookAutomationPacingSnapshot,
)
from facebook_monitor.runtime.paths import resolve_runtime_paths
from facebook_monitor.webapp.runtime_diagnostics import build_runtime_diagnostics_view
from tests.webapp.app_test_helpers import create_app


_NOW = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)
_LEASE_EXPIRES = datetime(2099, 7, 22, 4, 5, tzinfo=UTC)


def test_runtime_diagnostics_projects_active_pacing_without_owner_identity(
    tmp_path: Path,
) -> None:
    """Runtime diagnostics 只顯示 active/work/lease 等 bounded 欄位。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "automation"
    identity = load_or_create_managed_profile_identity(
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    private_operation = "private-operation-123"
    private_session = "private-session-456"
    with SqliteApplicationContext(db_path) as app:
        result = app.repositories.facebook_automation_pacing.try_acquire(
            identity.profile_scope_key,
            operation_id=private_operation,
            work_kind="target_scan",
            owner_session_id=private_session,
            started_at=_NOW,
            lease_expires_at=_LEASE_EXPIRES,
        )
        assert result.token is not None

    diagnostics = build_runtime_diagnostics_view(
        SimpleNamespace(
            db_path=db_path,
            profile_dir=profile_dir,
            scheduler_manager=None,
        )
    )
    pacing = next(
        field
        for field in diagnostics.fields
        if field.label == "Facebook automation pacing"
    )

    assert "profile_scope=managed_profile" in pacing.value
    assert "active=true" in pacing.value
    assert "work_kind=target_scan" in pacing.value
    assert f"lease_expires_at={_LEASE_EXPIRES.isoformat()}" in pacing.value
    assert identity.profile_scope_key not in diagnostics.copy_text
    assert private_operation not in diagnostics.copy_text
    assert private_session not in diagnostics.copy_text


def test_pacing_safe_snapshot_replaces_unknown_codes() -> None:
    """Unknown work/outcome 不得原樣進入 diagnostics。"""

    private_work = "private-work-account-123"
    private_outcome = "private-outcome-account-456"
    snapshot = build_facebook_automation_pacing_safe_snapshot(
        FacebookAutomationPacingSnapshot(
            profile_scope_key="private-profile",
            active_operation_id="private-operation",
            active_work_kind=private_work,
            owner_session_id="private-session",
            active_lease_expires_at=_LEASE_EXPIRES,
            last_outcome=private_outcome,
            updated_at=_NOW,
        ),
        profile_scope="managed_profile",
        now=_NOW,
    )

    assert snapshot.active_work_kind == "unrecognized_code"
    assert snapshot.last_outcome == "unrecognized_code"
    assert private_work not in str(snapshot)
    assert private_outcome not in str(snapshot)
    assert "private-operation" not in str(snapshot)
    assert "private-session" not in str(snapshot)


def test_settings_support_bundle_route_includes_redacted_pacing_snapshot(
    tmp_path: Path,
) -> None:
    """Settings route 下載的 bundle 含 pacing section 且不洩漏 owner。"""

    paths = resolve_runtime_paths(
        data_dir=tmp_path / "data",
        app_base_dir=tmp_path / "app",
    )
    paths.ensure_writable_dirs()
    identity = load_or_create_managed_profile_identity(
        profiles_root=paths.profiles_dir,
        profile_dir=paths.profile_dir,
    )
    private_operation = "route-private-operation"
    private_session = "route-private-session"
    with SqliteApplicationContext(paths.db_path) as app_context:
        app_context.repositories.facebook_automation_pacing.try_acquire(
            identity.profile_scope_key,
            operation_id=private_operation,
            work_kind="metadata_refresh",
            owner_session_id=private_session,
            started_at=_NOW,
            lease_expires_at=_LEASE_EXPIRES,
        )
    app = create_app(db_path=paths.db_path, profile_dir=paths.profile_dir)
    app.state.runtime_paths = paths
    client = TestClient(app)

    response = client.post("/settings/support-bundle")

    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        payload = json.loads(
            archive.read("facebook_automation_pacing.json").decode("utf-8")
        )
        runtime_text = archive.read("runtime_diagnostics.txt").decode("utf-8")
    assert payload["active"] is True
    assert payload["active_work_kind"] == "metadata_refresh"
    assert "Facebook automation pacing:" in runtime_text
    assert private_operation not in json.dumps(payload) + runtime_text
    assert private_session not in json.dumps(payload) + runtime_text
    assert identity.profile_scope_key not in json.dumps(payload) + runtime_text
