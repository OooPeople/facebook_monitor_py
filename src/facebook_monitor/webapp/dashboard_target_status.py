"""TargetRow 狀態與主操作 presenter helper。"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.webapp.dashboard_status_presenters import TargetStatusPresenter


@dataclass(frozen=True)
class TargetMonitoringPresenter:
    """整理 target 啟停狀態與主操作按鈕文案。"""

    target: TargetDescriptor
    runtime_state: TargetRuntimeState

    @property
    def scanning_supported(self) -> bool:
        """回傳目前 target 是否已接上 worker 掃描流程。"""

        return self.target.target_kind in {TargetKind.POSTS, TargetKind.COMMENTS}

    @property
    def status_presenter(self) -> TargetStatusPresenter:
        """建立 target 狀態 presenter。"""

        return TargetStatusPresenter(
            target=self.target,
            runtime_state=self.runtime_state,
            scanning_supported=self.scanning_supported,
        )

    @property
    def status_label(self) -> str:
        """回傳 target 啟停狀態文字。"""

        return self.status_presenter.label

    @property
    def status_class(self) -> str:
        """回傳 target 狀態對應 CSS class。"""

        return self.status_presenter.css_class

    @property
    def monitoring_action(self) -> str:
        """回傳主操作按鈕應提交的 monitoring action。"""

        return "start" if self.target.paused or not self.target.enabled else "stop"

    @property
    def monitoring_button_label(self) -> str:
        """回傳主操作按鈕文字，維持開始 / 暫停語義。"""

        return "開始" if self.monitoring_action == "start" else "停止"


__all__ = [
    "TargetMonitoringPresenter",
]
