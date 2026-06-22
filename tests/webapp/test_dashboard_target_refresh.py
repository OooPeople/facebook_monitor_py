"""Dashboard target refresh presenter 測試。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from types import SimpleNamespace

import pytest

from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.webapp import dashboard_target_refresh
from facebook_monitor.webapp.dashboard_models import TargetRow
from facebook_monitor.webapp.dashboard_target_refresh import NextRefreshDisplay
from facebook_monitor.webapp.dashboard_target_refresh import format_countdown_seconds
from facebook_monitor.webapp.dashboard_target_refresh import next_refresh_display


NOW = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("seconds", "label"),
    [
        (0, "0s"),
        (59, "59s"),
        (60, "1m"),
        (61, "1m 1s"),
        (3600, "1h"),
        (3661, "1h 1m"),
    ],
)
def test_format_countdown_seconds(seconds: int, label: str) -> None:
    """raw formatter 需維持既有短格式輸出。"""

    assert format_countdown_seconds(seconds) == label


@pytest.mark.parametrize(
    ("row_update", "runtime_update", "expected"),
    [
        ({"target": SimpleNamespace(enabled=False, paused=False)}, {}, "未排程"),
        ({"target": SimpleNamespace(enabled=True, paused=True)}, {}, "未排程"),
        ({}, {"desired_state": TargetDesiredState.STOPPED}, "未排程"),
        ({}, {"runtime_status": TargetRuntimeStatus.ERROR}, "未排程"),
        ({}, {"runtime_status": TargetRuntimeStatus.QUEUED}, "排隊中"),
        ({}, {"runtime_status": TargetRuntimeStatus.RUNNING}, "掃描中"),
        ({}, {"scan_requested_at": NOW}, "即將刷新"),
    ],
)
def test_next_refresh_display_reports_non_countdown_states(
    monkeypatch: pytest.MonkeyPatch,
    row_update: dict[str, object],
    runtime_update: dict[str, object],
    expected: str,
) -> None:
    """非 idle active 狀態不可暴露倒數秒數。"""

    monkeypatch.setattr(dashboard_target_refresh, "utc_now", lambda: NOW)
    row = _row()
    row.runtime_state = replace(row.runtime_state, **runtime_update)
    for key, value in row_update.items():
        setattr(row, key, value)

    assert next_refresh_display(row) == NextRefreshDisplay(label=expected)


def test_next_refresh_display_uses_display_next_due_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """runtime 提供 display_next_due_at 時，UI 應使用該時間倒數。"""

    monkeypatch.setattr(dashboard_target_refresh, "utc_now", lambda: NOW)
    row = _row()
    row.runtime_state = replace(
        row.runtime_state,
        display_next_due_at=NOW + timedelta(seconds=125),
    )

    assert next_refresh_display(row) == NextRefreshDisplay(label="2m 5s", seconds=125)


def test_next_refresh_display_prioritizes_display_next_due_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """display_next_due_at 應優先於 last_started_at 與 latest scan fallback。"""

    monkeypatch.setattr(dashboard_target_refresh, "utc_now", lambda: NOW)
    row = _row()
    row.runtime_state = replace(
        row.runtime_state,
        display_next_due_at=NOW + timedelta(seconds=125),
        last_started_at=NOW - timedelta(seconds=20),
    )
    row.latest_scan_run = ScanRun(
        target_id=row.target_id,
        status=ScanStatus.SUCCESS,
        started_at=NOW - timedelta(seconds=40),
        finished_at=NOW - timedelta(seconds=15),
    )

    assert next_refresh_display(row) == NextRefreshDisplay(label="2m 5s", seconds=125)


@pytest.mark.parametrize("due_at", [NOW, NOW - timedelta(seconds=1)])
def test_next_refresh_display_reports_due_when_due_at_expired(
    monkeypatch: pytest.MonkeyPatch,
    due_at: datetime,
) -> None:
    """display_next_due_at 已到期或過期時，UI 應顯示即將刷新。"""

    monkeypatch.setattr(dashboard_target_refresh, "utc_now", lambda: NOW)
    row = _row()
    row.runtime_state = replace(row.runtime_state, display_next_due_at=due_at)

    assert next_refresh_display(row) == NextRefreshDisplay(label="即將刷新")


def test_next_refresh_display_falls_back_to_last_started_at_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """沒有 display_next_due_at 時，presenter 會用最後開始時間加 refresh interval。"""

    monkeypatch.setattr(dashboard_target_refresh, "utc_now", lambda: NOW)
    row = _row()
    row.runtime_state = replace(
        row.runtime_state,
        last_started_at=NOW - timedelta(seconds=20),
    )

    assert next_refresh_display(row) == NextRefreshDisplay(label="40s", seconds=40)


def test_next_refresh_display_falls_back_to_latest_scan_finished_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未記錄 last_started_at 時，最近一輪完成時間仍可支撐倒數。"""

    monkeypatch.setattr(dashboard_target_refresh, "utc_now", lambda: NOW)
    row = _row()
    row.latest_scan_run = ScanRun(
        target_id=row.target_id,
        status=ScanStatus.SUCCESS,
        started_at=NOW - timedelta(seconds=40),
        finished_at=NOW - timedelta(seconds=15),
    )

    assert next_refresh_display(row) == NextRefreshDisplay(label="45s", seconds=45)


def test_next_refresh_display_reports_due_when_no_schedule_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """沒有任何排程參考時間時，UI 應顯示即將刷新而不是假倒數。"""

    monkeypatch.setattr(dashboard_target_refresh, "utc_now", lambda: NOW)

    assert next_refresh_display(_row()) == NextRefreshDisplay(label="即將刷新")


def test_target_row_next_refresh_display_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一 TargetRow 內 label/seconds 應共用同一次倒數計算結果。"""

    monkeypatch.setattr(dashboard_target_refresh, "utc_now", lambda: NOW)
    target_id = "target-1"
    row = TargetRow(
        target=_target_descriptor(target_id),
        config=TargetConfig(target_id=target_id, fixed_refresh_sec=60),
        runtime_state=TargetRuntimeState(
            target_id=target_id,
            desired_state=TargetDesiredState.ACTIVE,
            runtime_status=TargetRuntimeStatus.IDLE,
            display_next_due_at=NOW + timedelta(seconds=90),
        ),
    )

    assert row.next_refresh_label == "1m 30s"
    monkeypatch.setattr(
        dashboard_target_refresh,
        "utc_now",
        lambda: NOW + timedelta(seconds=30),
    )
    assert row.next_refresh_seconds == 90


def _row() -> SimpleNamespace:
    """建立 target refresh presenter 所需的最小 row 物件。"""

    target_id = "target-1"
    return SimpleNamespace(
        target_id=target_id,
        target=SimpleNamespace(enabled=True, paused=False),
        runtime_state=TargetRuntimeState(
            target_id=target_id,
            desired_state=TargetDesiredState.ACTIVE,
            runtime_status=TargetRuntimeStatus.IDLE,
        ),
        config=TargetConfig(
            target_id=target_id,
            fixed_refresh_sec=60,
            jitter_enabled=False,
        ),
        settings_presenter=SimpleNamespace(fixed_refresh_value=60),
        latest_scan_run=None,
    )


def _target_descriptor(target_id: str) -> TargetDescriptor:
    """建立 TargetRow 測試用 target descriptor。"""

    return TargetDescriptor(
        id=target_id,
        name="Test target",
        target_kind=TargetKind.POSTS,
        group_id="group-1",
        scope_id="group-1",
        canonical_url="https://www.facebook.com/groups/group-1",
        enabled=True,
        paused=False,
    )
