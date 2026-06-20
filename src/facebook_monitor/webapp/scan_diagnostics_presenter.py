"""Web UI scan diagnostics summary/text entrypoints。"""

from __future__ import annotations

from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import NotificationOutboxSummary
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.webapp.scan_diagnostics_text import build_completed_scan_diagnostics_text
from facebook_monitor.webapp.scan_diagnostics_text import build_empty_scan_diagnostics_text
from facebook_monitor.webapp.scan_diagnostics_text import ScanDiagnosticsTextContext
from facebook_monitor.webapp.scan_reason_presenters import format_scan_failure_reason
from facebook_monitor.webapp.scan_reason_presenters import format_scan_stop_reason


def build_scan_diagnostics_summary(
    *,
    latest_scan_run: ScanRun | None,
) -> str:
    """建立 target card 使用的 scan diagnostics 摘要。"""

    if latest_scan_run is None:
        return "尚無掃描診斷"

    metadata = latest_scan_run.metadata or {}
    if latest_scan_run.status == ScanStatus.FAILED:
        failure_reason = format_scan_failure_reason(str(metadata.get("reason") or ""))
        return f"status=failed · reason={failure_reason}"
    round_count = metadata.get("round_count", 0)
    candidate_count = metadata.get("candidate_count", latest_scan_run.item_count)
    stop_reason = format_scan_stop_reason(str(metadata.get("stop_reason") or ""))
    return f"rounds={round_count} · candidates={candidate_count} · stop={stop_reason}"


def build_scan_diagnostics_text(
    *,
    target: TargetDescriptor,
    config: TargetConfig,
    runtime_state: TargetRuntimeState,
    latest_scan_run: ScanRun | None,
    latest_scan_items: tuple[LatestScanItem, ...] = (),
    notification_outbox_summary: NotificationOutboxSummary | None = None,
    latest_failed_scan_run: ScanRun | None = None,
) -> str:
    """建立可複製的 scan-level diagnostics 文字。"""

    if latest_scan_run is None:
        return build_empty_scan_diagnostics_text(
            target=target,
            runtime_state=runtime_state,
            notification_outbox_summary=notification_outbox_summary,
        )
    return build_completed_scan_diagnostics_text(
        ScanDiagnosticsTextContext(
            target=target,
            config=config,
            runtime_state=runtime_state,
            scan=latest_scan_run,
            latest_scan_items=latest_scan_items,
            notification_outbox_summary=notification_outbox_summary,
            latest_failed_scan_run=latest_failed_scan_run,
        )
    )
