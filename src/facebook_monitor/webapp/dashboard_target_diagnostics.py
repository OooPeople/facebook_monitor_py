"""TargetRow scan diagnostics presenter helper。"""

from __future__ import annotations

from typing import Any

from facebook_monitor.webapp.diagnostics_presenter import build_scan_diagnostics_view
from facebook_monitor.webapp.diagnostics_presenter import format_scan_cycle_result_reason


def scan_cycle_result_label(row: Any) -> str:
    """回傳右側結果 panel 使用的最近一輪結束原因。"""

    if not row.latest_scan_run:
        return ""
    metadata = row.latest_scan_run.metadata or {}
    reason = str(metadata.get("stop_reason") or "")
    if not reason:
        return ""
    return f"本輪：{format_scan_cycle_result_reason(reason)}"


def latest_scan_diagnostics_summary(row: Any) -> str:
    """回傳最近成功掃描的診斷短摘要。"""

    return build_scan_diagnostics_view(
        target=row.target,
        config=row.config,
        runtime_state=row.runtime_state,
        latest_scan_run=row.latest_scan_run,
        notification_outbox_summary=row.notification_outbox_summary,
        latest_failed_scan_run=row.latest_failed_scan_run,
    ).summary


def latest_scan_diagnostics_text(row: Any) -> str:
    """回傳可複製的 scan-level diagnostics。"""

    return build_scan_diagnostics_view(
        target=row.target,
        config=row.config,
        runtime_state=row.runtime_state,
        latest_scan_run=row.latest_scan_run,
        latest_scan_items=tuple(item.item for item in row.latest_scan_items),
        notification_outbox_summary=row.notification_outbox_summary,
        latest_failed_scan_run=row.latest_failed_scan_run,
    ).text


__all__ = [
    "latest_scan_diagnostics_summary",
    "latest_scan_diagnostics_text",
    "scan_cycle_result_label",
]
