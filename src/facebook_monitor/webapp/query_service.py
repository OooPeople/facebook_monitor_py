"""Web UI read-side query helpers。

職責：集中整理首頁需要的 target/config/runtime/latest 資料，避免 FastAPI
route 直接散讀 repository，讓 UI route 專注處理 HTTP 流程。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.webapp.schemas import LatestScanItemRow
from facebook_monitor.webapp.schemas import TargetRow


@dataclass(frozen=True)
class DashboardRevision:
    """保存 dashboard 變更偵測用的輕量 revision。"""

    revision: str
    last_changed_at: str = ""


def list_target_rows(db_path: Path) -> list[TargetRow]:
    """讀取所有 target 與 config，整理為首頁 target row。"""

    with SqliteApplicationContext(db_path) as app_context:
        rows: list[TargetRow] = []
        for target in app_context.repositories.targets.list_all():
            config = app_context.services.targets.get_config_for_target(target)
            runtime_state = app_context.services.targets.ensure_runtime_state(target.id)
            latest_scan_items = tuple(
                LatestScanItemRow(item=item)
                for item in app_context.repositories.latest_scan_items.list_by_target(
                    target.id,
                    limit=config.max_items_per_scan,
                )
            )
            rows.append(
                TargetRow(
                    target=target,
                    config=config,
                    runtime_state=runtime_state,
                    latest_scan_run=app_context.repositories.scan_runs.latest_by_target(target.id),
                    latest_failed_scan_run=app_context.repositories.scan_runs.latest_by_target(
                        target.id,
                        status=ScanStatus.FAILED,
                    ),
                    latest_notification_event=(
                        app_context.repositories.notification_events.latest_by_target(target.id)
                    ),
                    latest_scan_items=latest_scan_items,
                )
            )
        return rows


def get_dashboard_revision(db_path: Path) -> DashboardRevision:
    """產生首頁資料 revision，供前端條件式刷新使用。"""

    rows = list_target_rows(db_path)
    payload: list[dict[str, object]] = []
    changed_values: list[str] = []
    for row in rows:
        latest_scan = row.latest_scan_run
        latest_notification = row.latest_notification_event
        values = {
            "target_id": row.target.id,
            "target_kind": row.target.target_kind.value,
            "group_id": row.target.group_id,
            "parent_post_id": row.target.parent_post_id,
            "scope_id": row.target.scope_id,
            "target_updated_at": row.target.updated_at.isoformat(),
            "config": {
                "include_keywords": row.config.include_keywords,
                "exclude_keywords": row.config.exclude_keywords,
                "fixed_refresh_sec": row.config.fixed_refresh_sec,
                "max_items_per_scan": row.config.max_items_per_scan,
                "auto_load_more": row.config.auto_load_more,
                "auto_adjust_sort": row.config.auto_adjust_sort,
                "enable_desktop_notification": row.config.enable_desktop_notification,
                "enable_ntfy": row.config.enable_ntfy,
                "ntfy_topic": row.config.ntfy_topic,
                "enable_discord_notification": row.config.enable_discord_notification,
                "discord_webhook": row.config.discord_webhook,
            },
            "runtime_status": row.runtime_state.runtime_status.value,
            "runtime_updated_at": row.runtime_state.updated_at.isoformat(),
            "runtime_last_error": row.runtime_state.last_error,
            "runtime_last_skip_reason": row.runtime_state.last_skip_reason,
            "runtime_active_worker_id": row.runtime_state.active_worker_id,
            "runtime_active_page_id": row.runtime_state.active_page_id,
            "runtime_last_page_reloaded_at": row.runtime_state.last_page_reloaded_at.isoformat()
            if row.runtime_state.last_page_reloaded_at
            else "",
            "runtime_enqueue_reason": row.runtime_state.enqueue_reason,
            "runtime_last_enqueued_at": row.runtime_state.last_enqueued_at.isoformat()
            if row.runtime_state.last_enqueued_at
            else "",
            "runtime_last_started_at": row.runtime_state.last_started_at.isoformat()
            if row.runtime_state.last_started_at
            else "",
            "runtime_last_finished_at": row.runtime_state.last_finished_at.isoformat()
            if row.runtime_state.last_finished_at
            else "",
            "runtime_scan_guard_count": row.runtime_state.scan_guard_count,
            "latest_scan_finished_at": latest_scan.finished_at.isoformat()
            if latest_scan
            else "",
            "latest_notification_created_at": latest_notification.created_at.isoformat()
            if latest_notification
            else "",
            "latest_item_keys": [item.item.item_key for item in row.latest_scan_items],
        }
        payload.append(values)
        changed_values.extend(
            value
            for value in (
                values["target_updated_at"],
                values["runtime_updated_at"],
                values["latest_scan_finished_at"],
                values["latest_notification_created_at"],
            )
            if isinstance(value, str) and value
        )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return DashboardRevision(
        revision=hashlib.sha256(encoded).hexdigest(),
        last_changed_at=max(changed_values, default=""),
    )
