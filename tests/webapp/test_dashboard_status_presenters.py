"""Dashboard target status presenter 測試。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.webapp.dashboard_status_presenters import TargetStatusPresenter


@pytest.mark.parametrize(
    (
        "enabled",
        "paused",
        "runtime_status",
        "scanning_supported",
        "label",
        "css_class",
    ),
    [
        (False, None, TargetRuntimeStatus.IDLE, True, "停用", "muted"),
        (None, True, TargetRuntimeStatus.IDLE, True, "已停止", "stopped"),
        (None, True, TargetRuntimeStatus.QUEUED, True, "已停止", "stopped"),
        (None, True, TargetRuntimeStatus.RUNNING, True, "已停止", "stopped"),
        (False, None, TargetRuntimeStatus.ERROR, True, "停用", "muted"),
        (False, None, TargetRuntimeStatus.RUNNING, True, "停用", "muted"),
        (None, None, TargetRuntimeStatus.IDLE, False, "尚未接上掃描", "enabled"),
        (None, None, TargetRuntimeStatus.IDLE, True, "已啟用", "enabled"),
        (None, None, TargetRuntimeStatus.QUEUED, True, "排隊中", "queued"),
        (None, None, TargetRuntimeStatus.RUNNING, True, "掃描中", "running"),
        (None, None, TargetRuntimeStatus.ERROR, True, "錯誤", "error"),
    ],
)
def test_target_status_presenter_label_and_css_class_matrix(
    enabled: bool | None,
    paused: bool | None,
    runtime_status: TargetRuntimeStatus,
    scanning_supported: bool,
    label: str,
    css_class: str,
) -> None:
    """target status label 與 CSS class 應維持既有 UI contract。"""

    target = _target_descriptor()
    if enabled is not None:
        target = replace(target, enabled=enabled)
    if paused is not None:
        target = replace(target, paused=paused)
    presenter = TargetStatusPresenter(
        target=target,
        runtime_state=TargetRuntimeState(
            target_id=target.id,
            runtime_status=runtime_status,
        ),
        scanning_supported=scanning_supported,
    )

    assert presenter.label == label
    assert presenter.css_class == css_class


def _target_descriptor() -> TargetDescriptor:
    """建立 status presenter 測試用 target descriptor。"""

    return TargetDescriptor(
        id="target-1",
        name="Test target",
        target_kind=TargetKind.POSTS,
        group_id="group-1",
        scope_id="group-1",
        canonical_url="https://www.facebook.com/groups/group-1",
        enabled=True,
        paused=False,
    )
