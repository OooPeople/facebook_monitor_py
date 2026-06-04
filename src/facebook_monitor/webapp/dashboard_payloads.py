"""Dashboard partial update payload serializers。

職責：集中 dashboard / sidebar partial response 的 JSON shape 與 Jinja partial
HTML rendering，讓 route 只負責 HTTP wiring 與 read operation。
"""

from __future__ import annotations

from typing import Any

from fastapi.templating import Jinja2Templates

from facebook_monitor.webapp.dashboard_models import SidebarTargetItem
from facebook_monitor.webapp.dashboard_models import TargetRow
from facebook_monitor.webapp.query_service import ProfileSessionWarning


def serialize_sidebar_item(item: SidebarTargetItem) -> dict[str, object]:
    """序列化 sidebar item read model。"""

    return {
        "target_id": item.target_id,
        "display_name": item.display_name,
        "anchor_id": item.anchor_id,
        "base_status_summary": item.base_status_summary,
        "status_class": item.status_class,
        "status_detail": item.status_detail,
        "status_summary": item.status_summary,
        "mode_label": item.mode_label,
        "mode_class": item.mode_class,
        "hit_count": item.hit_count,
        "latest_error_summary": item.latest_error_summary,
        "thumbnail_url": item.thumbnail_url,
        "active": item.active,
    }


def serialize_sidebar_payload(dashboard: Any) -> dict[str, object]:
    """序列化 sidebar partial payload，包含 group/order 結構簽章。"""

    return {
        "layout_signature": getattr(dashboard, "sidebar_layout_signature", ""),
        "items": [serialize_sidebar_item(row.sidebar_item) for row in dashboard.rows],
    }


def serialize_profile_session_warning(
    warning: ProfileSessionWarning,
) -> dict[str, object]:
    """序列化首頁 Facebook session 警告。"""

    return {
        "needs_login": warning.needs_login,
        "message": warning.message,
        "reason": warning.reason,
    }


def serialize_target_card(row: TargetRow, templates: Jinja2Templates) -> dict[str, object]:
    """序列化 target card partial update read model。"""

    return {
        "target_id": row.target_id,
        "anchor_id": row.anchor_id,
        "display_name": row.display_name,
        "rename_display_name": row.rename_display_name,
        "thumbnail_url": row.thumbnail_url,
        "status_label": row.status_label,
        "status_class": row.status_class,
        "header_summary_label": row.header_summary_label,
        "mode_label": row.mode_label,
        "mode_class": row.mode_class,
        "monitoring_action": row.monitoring_action,
        "monitoring_button_label": row.monitoring_button_label,
        "runtime_error": row.runtime_error,
        "runtime_skip_reason": row.runtime_skip_reason,
        "has_latest_failed_scan": bool(row.latest_failed_scan_run),
        "latest_error_indicator_label": row.latest_error_indicator_label,
        "latest_error_indicator_title": row.latest_error_indicator_title,
        "latest_error_indicator_kind": row.latest_error_indicator_kind,
        "latest_scan_header_label": f"最近掃描 {row.latest_scan_header_time_label}",
        "next_refresh_label": f"下次刷新：{row.next_refresh_label}",
        "next_refresh_seconds": row.next_refresh_seconds,
        "scan_cycle_result_label": row.scan_cycle_result_label,
        "latest_scan_diagnostics_summary": row.latest_scan_diagnostics_summary,
        "latest_scan_diagnostics_text": row.latest_scan_diagnostics_text,
        "hit_record_total_count": row.hit_record_total_count,
        "card_summary_html": render_collapsed_summary_html(templates, row),
        "latest_scan_preview_html": render_preview_rows_html(
            templates,
            row.latest_scan_preview_rows,
            "尚無掃描紀錄",
            "latest_scan",
        ),
        "hit_record_preview_html": render_preview_rows_html(
            templates,
            row.hit_record_preview_rows,
            "尚無命中紀錄",
            "hit_records",
        ),
    }


def render_collapsed_summary_html(templates: Jinja2Templates, row: TargetRow) -> str:
    """以 Jinja 單一來源產生收合摘要 partial HTML。"""

    template = templates.env.get_template("_collapsed_summary_fields.html")
    return template.render(summary_sections=row.card_summary.sections).strip()


def render_preview_rows_html(
    templates: Jinja2Templates,
    rows: object,
    empty_text: str,
    empty_kind: str,
) -> str:
    """以 Jinja 單一來源產生 preview rows partial HTML。"""

    template = templates.env.get_template("_preview_rows.html")
    preview_rows = getattr(template.module, "preview_rows")
    return str(preview_rows(rows, empty_text, empty_kind)).strip()
