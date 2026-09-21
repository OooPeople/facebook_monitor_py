"""現行 runtime diagnostics 與 scheduler support snapshot 契約測試。"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.facebook_temporary_block import FacebookActionKind
from facebook_monitor.core.facebook_temporary_block import FacebookProductOperationKind
from facebook_monitor.core.facebook_temporary_block import FacebookWorkSourceKind
from facebook_monitor.core.facebook_temporary_block import TemporaryBlockFinding
from facebook_monitor.runtime.paths import resolve_runtime_paths
from facebook_monitor.webapp.runtime_diagnostics import build_runtime_diagnostics_view
from facebook_monitor.webapp.settings_use_cases import support_bundle_scheduler_state


def test_runtime_diagnostics_projects_only_temporary_block_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """現行 warning diagnostics 不得投影 target、evidence 或 legacy safety state。"""

    paths = resolve_runtime_paths(
        data_dir=tmp_path / "data",
        app_base_dir=tmp_path / "app",
    )
    paths.ensure_writable_dirs()
    private_target = "runtime-private-target-123"
    private_evidence = "runtime_private_evidence"
    detected_at = datetime(2026, 9, 21, tzinfo=UTC)
    with SqliteApplicationContext(paths.db_path) as app:
        app.services.facebook_temporary_block_warning.record(
            TemporaryBlockFinding(
                source_kind=FacebookWorkSourceKind.COVER,
                operation_kind=FacebookProductOperationKind.COVER_METADATA_ACCESS,
                action_kind=FacebookActionKind.GROUP_DOCUMENT,
                target_id=private_target,
                evidence_code=private_evidence,
            ),
            detected_at=detected_at,
        )
    monkeypatch.setattr(
        "facebook_monitor.webapp.runtime_diagnostics.utc_now",
        lambda: detected_at,
    )

    diagnostics = build_runtime_diagnostics_view(
        SimpleNamespace(
            runtime_paths=paths,
            db_path=paths.db_path,
            profile_dir=paths.profile_dir,
            scheduler_manager=None,
        )
    )
    warning = next(
        field
        for field in diagnostics.fields
        if field.label == "Facebook temporary block warning"
    )

    assert warning.value == (
        "available=true; active=true; generation=1; "
        f"detected_at={detected_at.isoformat()}; "
        "warning_until=2026-09-21T12:00:00+00:00; "
        "source=cover; operation=cover_metadata_access; action=group_document"
    )
    assert "Facebook access circuit" not in diagnostics.copy_text
    assert "Facebook automation pacing" not in diagnostics.copy_text
    assert "coordinator" not in diagnostics.copy_text
    assert "probe" not in diagnostics.copy_text
    assert private_target not in diagnostics.copy_text
    assert private_evidence not in diagnostics.copy_text


def test_scheduler_diagnostics_keep_concurrency_and_page_pool_without_coordinator(
    tmp_path: Path,
) -> None:
    """Scheduler diagnostics 保留一般執行狀態，但不再輸出 safety coordinator。"""

    state = SimpleNamespace(
        running=True,
        current_queued_count=2,
        current_running_count=1,
        max_concurrent_scans=3,
        page_pool_size=3,
        last_opened_page_count=1,
        last_reused_page_count=2,
        last_closed_page_count=1,
        resident_browser_alive=True,
    )
    scheduler_manager = SimpleNamespace(state=lambda: state)
    paths = resolve_runtime_paths(
        data_dir=tmp_path / "data",
        app_base_dir=tmp_path / "app",
    )
    diagnostics = build_runtime_diagnostics_view(
        SimpleNamespace(runtime_paths=paths, scheduler_manager=scheduler_manager)
    )
    scheduler = next(
        field for field in diagnostics.fields if field.label == "Scheduler"
    )

    assert "running=1" in scheduler.value
    assert "queued=2" in scheduler.value
    assert "slots=3" in scheduler.value
    assert "page_pool_size=3" in scheduler.value
    assert "opened_pages=1" in scheduler.value
    assert "reused_pages=2" in scheduler.value
    assert "closed_pages=1" in scheduler.value
    assert "browser_alive=true" in scheduler.value
    assert "coordinator" not in scheduler.value


def test_support_bundle_scheduler_state_omits_safety_coordinator_fields() -> None:
    """Settings use case 不把已移除 coordinator 欄位交給 bundle collector。"""

    state = SimpleNamespace(
        running=True,
        max_concurrent_scans=4,
        page_pool_size=4,
    )

    payload = support_bundle_scheduler_state(
        SimpleNamespace(scheduler_manager=SimpleNamespace(state=lambda: state))
    )

    assert payload["running"] is True
    assert payload["max_concurrent_scans"] == 4
    assert payload["page_pool_size"] == 4
