"""Dashboard target row 與 target card read model 組裝。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import NotificationOutboxSummary
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.persistence.invariants import DatabaseInvariantViolation
from facebook_monitor.webapp.dashboard_collections import DashboardReadCollections
from facebook_monitor.webapp.dashboard_models import TargetRow
from facebook_monitor.webapp.preview_models import HitRecordPreviewRow
from facebook_monitor.webapp.preview_models import LatestScanItemRow
from facebook_monitor.webapp.read_model_invariants import read_mapper_value


def build_dashboard_rows(
    collections: DashboardReadCollections,
) -> tuple[TargetRow, ...]:
    """將 dashboard 批次讀取結果組成 target rows。"""

    rows: list[TargetRow] = []
    for target in collections.targets:
        config = collections.configs_by_target[target.id]
        rows.append(
            build_target_row(
                target=target,
                config=config,
                runtime_state=collections.runtime_by_target.get(
                    target.id,
                    TargetRuntimeState(target_id=target.id),
                ),
                latest_scan_items=collections.latest_scan_items.get(target.id, ())[
                    : config.max_items_per_scan
                ],
                hit_record_preview_items=collections.hit_record_preview_items.get(
                    target.id,
                    (),
                ),
                latest_scan_run=collections.latest_scan_runs.get(target.id),
                latest_failed_scan_run=collections.latest_failed_scan_runs.get(
                    target.id
                ),
                notification_outbox_summary=collections.outbox_summaries.get(target.id),
                hit_record_total_count=collections.hit_record_counts.get(target.id, 0),
            )
        )
    return tuple(rows)


def build_target_row(
    *,
    target: TargetDescriptor,
    config: TargetConfig,
    runtime_state: TargetRuntimeState,
    latest_scan_items: Sequence[LatestScanItem],
    hit_record_preview_items: Sequence[MatchHistoryEntry],
    latest_scan_run: ScanRun | None,
    latest_failed_scan_run: ScanRun | None,
    notification_outbox_summary: NotificationOutboxSummary | None,
    hit_record_total_count: int,
) -> TargetRow:
    """將 repository raw models 組成 dashboard target row。"""

    return TargetRow(
        target=target,
        config=config,
        runtime_state=runtime_state,
        latest_scan_run=latest_scan_run,
        latest_failed_scan_run=latest_failed_scan_run,
        notification_outbox_summary=notification_outbox_summary,
        latest_scan_items=tuple(LatestScanItemRow(item=item) for item in latest_scan_items),
        hit_record_preview_items=tuple(
            HitRecordPreviewRow(entry=entry) for entry in hit_record_preview_items
        ),
        hit_record_total_count=hit_record_total_count,
    )


def read_target_card_row(
    *,
    app_context: ApplicationContext,
    target: TargetDescriptor,
    config: TargetConfig,
    runtime_state: TargetRuntimeState,
    violations: tuple[DatabaseInvariantViolation, ...],
    session_started_at: datetime | None,
) -> TargetRow:
    """讀取並組裝單張 target card row。"""

    target_id = target.id
    return build_target_row(
        target=target,
        config=config,
        runtime_state=runtime_state,
        latest_scan_items=read_mapper_value(
            lambda: app_context.repositories.latest_scan_items.list_by_target(
                target_id,
                limit=config.max_items_per_scan,
            ),
            tables=("latest_scan_items",),
            violations=violations,
        ),
        hit_record_preview_items=read_mapper_value(
            lambda: app_context.repositories.match_history.list_by_target(
                target_id,
                limit=5,
                recorded_since=session_started_at,
            ),
            tables=("match_history",),
            violations=violations,
        ),
        latest_scan_run=read_mapper_value(
            lambda: app_context.repositories.scan_runs.latest_by_target(target_id),
            tables=("scan_runs",),
            violations=violations,
        ),
        latest_failed_scan_run=read_mapper_value(
            lambda: app_context.repositories.scan_runs.latest_by_target(
                target_id,
                status=ScanStatus.FAILED,
            ),
            tables=("scan_runs",),
            violations=violations,
        ),
        notification_outbox_summary=read_mapper_value(
            lambda: (
                app_context.repositories.notification_outbox
                .summarize_by_targets([target_id])
                .get(target_id)
            ),
            tables=("notification_outbox",),
            violations=violations,
        ),
        hit_record_total_count=app_context.repositories.match_history.count_by_target(
            target_id,
            recorded_since=session_started_at,
        ),
    )
