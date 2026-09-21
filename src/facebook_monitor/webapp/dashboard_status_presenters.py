"""Dashboard target status presenter。"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus


@dataclass(frozen=True)
class TargetStatusPresenter:
    """整理 target 啟停與 runtime 狀態顯示。"""

    target: TargetDescriptor
    runtime_state: TargetRuntimeState
    scanning_supported: bool

    @property
    def label(self) -> str:
        """回傳 target 啟停狀態文字。"""

        if not self.target.enabled:
            return "停用"
        if self.target.paused:
            return "已停止"
        if not self.scanning_supported:
            return "尚未接上掃描"
        labels = {
            TargetRuntimeStatus.IDLE: "已啟用",
            TargetRuntimeStatus.QUEUED: "排隊中",
            TargetRuntimeStatus.RUNNING: "掃描中",
            TargetRuntimeStatus.ERROR: "錯誤",
        }
        return labels.get(self.runtime_state.runtime_status, "已啟用")

    @property
    def css_class(self) -> str:
        """回傳 target 狀態對應 CSS class。"""

        if not self.target.enabled:
            return "muted"
        if self.target.paused:
            return "stopped"
        if self.runtime_state.runtime_status == TargetRuntimeStatus.QUEUED:
            return "queued"
        if self.runtime_state.runtime_status == TargetRuntimeStatus.RUNNING:
            return "running"
        if self.runtime_state.runtime_status == TargetRuntimeStatus.ERROR:
            return "error"
        return "enabled"
