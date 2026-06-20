"""TargetRow scan diagnostics presenter helper。"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import NotificationOutboxSummary
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.webapp.scan_diagnostics_presenter import (
    build_scan_diagnostics_summary,
)
from facebook_monitor.webapp.scan_diagnostics_presenter import build_scan_diagnostics_text
from facebook_monitor.webapp.scan_reason_presenters import format_scan_cycle_result_reason


@dataclass(frozen=True)
class TargetDiagnosticsPresenter:
    """整理 target scan diagnostics 與右側 scan result label。"""

    target: TargetDescriptor
    config: TargetConfig
    runtime_state: TargetRuntimeState
    latest_scan_run: ScanRun | None = None
    latest_scan_items: tuple[LatestScanItem, ...] = ()
    notification_outbox_summary: NotificationOutboxSummary | None = None
    latest_failed_scan_run: ScanRun | None = None

    @property
    def scan_cycle_result_label(self) -> str:
        """回傳右側結果 panel 使用的最近一輪結束原因。"""

        if not self.latest_scan_run:
            return ""
        metadata = self.latest_scan_run.metadata or {}
        reason = str(metadata.get("stop_reason") or "")
        if not reason:
            return ""
        return f"本輪：{format_scan_cycle_result_reason(reason)}"

    @property
    def latest_scan_diagnostics_summary(self) -> str:
        """回傳最近成功掃描的診斷短摘要。"""

        return build_scan_diagnostics_summary(
            latest_scan_run=self.latest_scan_run,
        )

    @property
    def latest_scan_diagnostics_text(self) -> str:
        """回傳可複製的 scan-level diagnostics。"""

        return build_scan_diagnostics_text(
            target=self.target,
            config=self.config,
            runtime_state=self.runtime_state,
            latest_scan_run=self.latest_scan_run,
            latest_scan_items=self.latest_scan_items,
            notification_outbox_summary=self.notification_outbox_summary,
            latest_failed_scan_run=self.latest_failed_scan_run,
        )


__all__ = [
    "TargetDiagnosticsPresenter",
]
