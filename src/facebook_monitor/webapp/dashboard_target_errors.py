"""TargetRow runtime 與最近錯誤 presenter helper。"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.user_messages import format_runtime_skip_message
from facebook_monitor.webapp.dashboard_error_presenters import (
    format_latest_error_indicator_label,
)
from facebook_monitor.webapp.dashboard_error_presenters import (
    format_latest_error_indicator_title,
)
from facebook_monitor.webapp.dashboard_error_presenters import (
    format_latest_failed_scan_summary,
)
from facebook_monitor.webapp.dashboard_error_presenters import format_runtime_error_message
from facebook_monitor.webapp.dashboard_error_presenters import (
    is_content_unavailable_runtime_error,
)
from facebook_monitor.webapp.dashboard_error_presenters import is_content_unavailable_scan
from facebook_monitor.webapp.dashboard_error_presenters import is_retrying_failure_scan
from facebook_monitor.webapp.time_presenters import format_datetime_for_ui


@dataclass(frozen=True)
class TargetErrorPresenter:
    """整理 target runtime error 與最近 failed scan 顯示狀態。"""

    runtime_state: TargetRuntimeState
    latest_scan_run: ScanRun | None = None
    latest_failed_scan_run: ScanRun | None = None

    @property
    def runtime_error(self) -> str:
        """回傳 runtime error 顯示文字。"""

        if self.runtime_state.runtime_status != TargetRuntimeStatus.ERROR:
            return ""
        return format_runtime_error_message(self.runtime_state.last_error)

    @property
    def runtime_skip_reason(self) -> str:
        """回傳最近一次 scan guard skip 原因。"""

        return format_runtime_skip_message(self.runtime_state.last_skip_reason)

    @property
    def latest_error_label(self) -> str:
        """回傳最近錯誤時間。"""

        if not self.latest_failed_scan_run:
            return ""
        return format_datetime_for_ui(self.latest_failed_scan_run.finished_at)

    @property
    def latest_failed_scan_summary(self) -> str:
        """回傳最近失敗掃描摘要。"""

        return format_latest_failed_scan_summary(
            self.latest_failed_scan_run,
            content_unavailable_current=self.content_unavailable_current,
        )

    @property
    def latest_error_indicator_label(self) -> str:
        """回傳 target header 的最近錯誤短標籤。"""

        return format_latest_error_indicator_label(
            self.latest_failed_scan_run,
            content_unavailable_current=self.content_unavailable_current,
            retrying_current=self.retrying_failure_current,
        )

    @property
    def latest_error_indicator_title(self) -> str:
        """回傳 target header 最近錯誤說明。"""

        return format_latest_error_indicator_title(
            self.latest_failed_scan_run,
            content_unavailable_current=self.content_unavailable_current,
            retrying_current=self.retrying_failure_current,
        )

    @property
    def latest_error_indicator_kind(self) -> str:
        """回傳最近錯誤 UI 類型。"""

        if self.content_unavailable_current:
            return "content-unavailable"
        if self.retrying_failure_current:
            return "retrying"
        return "error" if self.latest_failed_scan_run else ""

    @property
    def retrying_failure_current(self) -> bool:
        """回傳最近 failed scan 是否仍代表等待下輪重試的目前狀態。"""

        failed_scan = self.latest_failed_scan_run
        if not is_retrying_failure_scan(failed_scan):
            return False
        latest_scan = self.latest_scan_run
        if latest_scan is None:
            return True
        if failed_scan is None:
            return False
        return failed_scan.finished_at >= latest_scan.finished_at

    @property
    def content_unavailable_current(self) -> bool:
        """回傳連結失效是否仍代表目前狀態。"""

        failed_scan = self.latest_failed_scan_run
        if not is_content_unavailable_scan(failed_scan):
            return False
        if (
            self.runtime_state.runtime_status == TargetRuntimeStatus.ERROR
            and is_content_unavailable_runtime_error(self.runtime_state.last_error)
        ):
            return True
        latest_scan = self.latest_scan_run
        if latest_scan is None:
            return True
        if failed_scan is None:
            return False
        return failed_scan.finished_at >= latest_scan.finished_at


__all__ = [
    "TargetErrorPresenter",
]
