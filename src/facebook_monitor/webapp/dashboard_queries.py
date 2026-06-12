"""Dashboard read-side queries."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import NotificationOutboxSummary
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.sidebar_models import SidebarGroup
from facebook_monitor.core.sidebar_models import SidebarGroupConfigTemplate
from facebook_monitor.core.sidebar_models import SidebarTargetPlacement
from facebook_monitor.persistence.invariants import DatabaseInvariantViolation
from facebook_monitor.persistence.invariants import validate_database_invariants
from facebook_monitor.webapp.dashboard_models import SidebarGroupSection
from facebook_monitor.webapp.dashboard_models import SidebarTargetItem
from facebook_monitor.webapp.dashboard_models import TargetRow
from facebook_monitor.webapp.dashboard_read_models import DashboardReadResult
from facebook_monitor.webapp.dashboard_read_models import DashboardReadUnavailable
from facebook_monitor.webapp.dashboard_read_models import DashboardViewModel
from facebook_monitor.webapp.dashboard_read_models import DatabaseInvariantWarning
from facebook_monitor.webapp.dashboard_read_models import ProfileSessionWarning
from facebook_monitor.webapp.preview_models import HitRecordPreviewRow
from facebook_monitor.webapp.preview_models import LatestScanItemRow
from facebook_monitor.webapp.read_model_invariants import inactive_invariant_target_ids
from facebook_monitor.webapp.read_model_invariants import inactive_runtime_invariant_row_ids
from facebook_monitor.webapp.read_model_invariants import inactive_target_invariant_row_ids
from facebook_monitor.webapp.read_model_invariants import is_invariant_backed_mapper_error
from facebook_monitor.webapp.read_model_invariants import list_targets_excluding_ids
from facebook_monitor.webapp.read_model_invariants import read_mapper_value
from facebook_monitor.webapp.read_model_invariants import ReadModelInvariantMapperError
from facebook_monitor.webapp.read_model_context import read_application_context
from facebook_monitor.webapp.read_model_context import (
    raise_dashboard_read_unavailable_if_locked,
)


@dataclass(frozen=True)
class DashboardReadCollections:
    """保存 dashboard row assembly 需要的批次 repository 讀取結果。"""

    targets: list[TargetDescriptor]
    placements_by_target: dict[str, SidebarTargetPlacement]
    groups: tuple[SidebarGroup, ...]
    configs_by_target: dict[str, TargetConfig]
    runtime_by_target: Mapping[str, TargetRuntimeState]
    latest_scan_runs: Mapping[str, ScanRun]
    latest_failed_scan_runs: Mapping[str, ScanRun]
    outbox_summaries: Mapping[str, NotificationOutboxSummary]
    latest_scan_items: Mapping[str, Sequence[LatestScanItem]]
    hit_record_preview_items: Mapping[str, Sequence[MatchHistoryEntry]]
    hit_record_counts: Mapping[str, int]
    templates_by_group: dict[str, SidebarGroupConfigTemplate]


@dataclass(frozen=True)
class DashboardLayoutCollections:
    """保存 dashboard/sidebar 排序所需的 layout 資料。"""

    targets: list[TargetDescriptor]
    placements_by_target: dict[str, SidebarTargetPlacement]
    groups: tuple[SidebarGroup, ...]


@dataclass(frozen=True)
class DashboardRowReadCollections:
    """保存 dashboard target rows 需要的 target-scoped 批次資料。"""

    configs_by_target: dict[str, TargetConfig]
    runtime_by_target: Mapping[str, TargetRuntimeState]
    latest_scan_runs: Mapping[str, ScanRun]
    latest_failed_scan_runs: Mapping[str, ScanRun]
    outbox_summaries: Mapping[str, NotificationOutboxSummary]
    latest_scan_items: Mapping[str, Sequence[LatestScanItem]]
    hit_record_preview_items: Mapping[str, Sequence[MatchHistoryEntry]]
    hit_record_counts: Mapping[str, int]


def list_target_rows(
    db_path: Path,
    *,
    session_started_at: datetime | None = None,
) -> list[TargetRow]:
    """讀取所有 target 與 config，整理為首頁 target row。"""

    return list(
        _read_dashboard_model(
            db_path,
            initialize_schema_on_enter=False,
            session_started_at=session_started_at,
        ).rows
    )


def get_dashboard_view(
    db_path: Path,
    *,
    session_started_at: datetime | None = None,
) -> DashboardViewModel:
    """讀取 dashboard read model。"""

    result = _read_dashboard_model(
        db_path,
        initialize_schema_on_enter=False,
        session_started_at=session_started_at,
    )
    return DashboardViewModel(
        rows=result.rows,
        sidebar_groups=result.sidebar_groups,
        profile_session_warning=_build_profile_session_warning(
            result.profile_session_status
        ),
        database_invariant_warning=result.database_invariant_warning,
        dashboard_degraded=result.dashboard_degraded,
    )


def list_sidebar_items(
    db_path: Path,
    *,
    session_started_at: datetime | None = None,
) -> tuple[SidebarTargetItem, ...]:
    """讀取 sidebar partial update 所需的 target 摘要。"""

    return get_dashboard_view(db_path, session_started_at=session_started_at).sidebar_items


def get_target_card(
    db_path: Path,
    target_id: str,
    *,
    session_started_at: datetime | None = None,
) -> TargetRow | None:
    """讀取單一 target card read model，供 Phase 10 partial update 使用。"""

    try:
        with read_application_context(db_path) as app_context:
            violations = validate_database_invariants(
                app_context.repositories.targets.connection
            )
            if target_id in inactive_invariant_target_ids(
                app_context.repositories.targets.connection,
                violations=violations,
            ):
                return None
            target = read_mapper_value(
                lambda: app_context.repositories.targets.get(target_id),
                tables=("targets",),
                violations=violations,
            )
            if target is None:
                return None
            config = _configs_by_target(app_context, [target])[target.id]
            runtime_state = read_mapper_value(
                lambda: app_context.repositories.runtime_states.get(target.id),
                tables=("target_runtime_state",),
                violations=violations,
            ) or TargetRuntimeState(target_id=target.id)
            return _read_target_card_row(
                app_context=app_context,
                target=target,
                config=config,
                runtime_state=runtime_state,
                violations=violations,
                session_started_at=session_started_at,
            )
    except sqlite3.OperationalError as exc:
        raise_dashboard_read_unavailable_if_locked(exc)
        raise
    except ReadModelInvariantMapperError as exc:
        raise DashboardReadUnavailable(str(exc)) from exc


def _read_dashboard_model(
    db_path: Path,
    *,
    initialize_schema_on_enter: bool,
    session_started_at: datetime | None,
) -> DashboardReadResult:
    """讀取 dashboard rows 與 sidebar group sections。"""

    try:
        return _list_target_rows(
            db_path,
            initialize_schema_on_enter=initialize_schema_on_enter,
            session_started_at=session_started_at,
        )
    except sqlite3.OperationalError as exc:
        if not initialize_schema_on_enter and _is_missing_schema_error(exc):
            return _read_dashboard_model(
                db_path,
                initialize_schema_on_enter=True,
                session_started_at=session_started_at,
            )
        raise_dashboard_read_unavailable_if_locked(exc)
        raise


def _list_target_rows(
    db_path: Path,
    *,
    initialize_schema_on_enter: bool,
    session_started_at: datetime | None,
) -> DashboardReadResult:
    """執行 target row read；必要時供空 DB 首次讀取補 schema 後重試。"""

    with SqliteApplicationContext(
        db_path,
        initialize_schema_on_enter=initialize_schema_on_enter,
    ) as app_context:
        database_invariant_violations = validate_database_invariants(
            app_context.repositories.runtime_states.connection
        )
        database_invariant_warning = _build_database_invariant_warning(
            database_invariant_violations
        )
        try:
            collections = _read_dashboard_collections(
                app_context,
                violations=database_invariant_violations,
                session_started_at=session_started_at,
            )
            rows = _build_dashboard_rows(collections)
            sidebar_groups = _build_sidebar_groups(
                rows=rows,
                placements_by_target=collections.placements_by_target,
                groups=collections.groups,
                templates_by_group=collections.templates_by_group,
            )
        except ReadModelInvariantMapperError:
            return _degraded_dashboard_read_result(
                app_context,
                database_invariant_warning=database_invariant_warning,
            )
        return DashboardReadResult(
            rows=rows,
            sidebar_groups=sidebar_groups,
            profile_session_status=app_context.repositories.app_settings
            .get_profile_session_status(),
            database_invariant_warning=database_invariant_warning,
        )


def _read_dashboard_collections(
    app_context: ApplicationContext,
    *,
    violations: tuple[DatabaseInvariantViolation, ...],
    session_started_at: datetime | None,
) -> DashboardReadCollections:
    """批次讀取 dashboard rows 需要的 repository 資料。"""

    layout = _read_dashboard_layout(app_context, violations=violations)
    targets = _skip_inactive_runtime_invariant_targets(
        app_context,
        targets=layout.targets,
        violations=violations,
    )
    target_ids = [target.id for target in targets]
    row_data = _read_dashboard_row_data(
        app_context,
        targets=targets,
        target_ids=target_ids,
        violations=violations,
        session_started_at=session_started_at,
    )
    return DashboardReadCollections(
        targets=targets,
        placements_by_target=layout.placements_by_target,
        groups=layout.groups,
        configs_by_target=row_data.configs_by_target,
        runtime_by_target=row_data.runtime_by_target,
        latest_scan_runs=row_data.latest_scan_runs,
        latest_failed_scan_runs=row_data.latest_failed_scan_runs,
        outbox_summaries=row_data.outbox_summaries,
        latest_scan_items=row_data.latest_scan_items,
        hit_record_preview_items=row_data.hit_record_preview_items,
        hit_record_counts=row_data.hit_record_counts,
        templates_by_group=_sidebar_templates_by_group(
            app_context,
            groups=layout.groups,
            violations=violations,
        ),
    )


def _read_dashboard_layout(
    app_context: ApplicationContext,
    *,
    violations: tuple[DatabaseInvariantViolation, ...],
) -> DashboardLayoutCollections:
    """讀取 target 與 sidebar layout，並套用 sidebar 排序。"""

    targets = _read_dashboard_targets(app_context, violations=violations)
    placements_by_target = read_mapper_value(
        lambda: app_context.repositories.sidebar_layout.list_placements(),
        tables=("sidebar_target_placements",),
        violations=violations,
    )
    groups = read_mapper_value(
        lambda: app_context.repositories.sidebar_layout.list_groups(),
        tables=("sidebar_groups",),
        violations=violations,
    )
    return DashboardLayoutCollections(
        targets=_order_targets_by_sidebar(targets, placements_by_target, groups),
        placements_by_target=placements_by_target,
        groups=groups,
    )


def _read_dashboard_row_data(
    app_context: ApplicationContext,
    *,
    targets: list[TargetDescriptor],
    target_ids: list[str],
    violations: tuple[DatabaseInvariantViolation, ...],
    session_started_at: datetime | None,
) -> DashboardRowReadCollections:
    """批次讀取每張 target card 需要的 target-scoped 資料。"""

    configs_by_target = _configs_by_target(app_context, targets)
    max_items_limit = max(
        (config.max_items_per_scan for config in configs_by_target.values()),
        default=1,
    )
    return DashboardRowReadCollections(
        configs_by_target=configs_by_target,
        runtime_by_target=read_mapper_value(
            lambda: app_context.repositories.runtime_states.list_by_targets(target_ids),
            tables=("target_runtime_state",),
            violations=violations,
        ),
        latest_scan_runs=read_mapper_value(
            lambda: app_context.repositories.scan_runs.latest_by_targets(target_ids),
            tables=("scan_runs",),
            violations=violations,
        ),
        latest_failed_scan_runs=read_mapper_value(
            lambda: app_context.repositories.scan_runs.latest_by_targets(
                target_ids,
                status=ScanStatus.FAILED,
            ),
            tables=("scan_runs",),
            violations=violations,
        ),
        outbox_summaries=read_mapper_value(
            lambda: app_context.repositories.notification_outbox.summarize_by_targets(
                target_ids
            ),
            tables=("notification_outbox",),
            violations=violations,
        ),
        latest_scan_items=read_mapper_value(
            lambda: app_context.repositories.latest_scan_items.list_by_targets(
                target_ids,
                limit_per_target=max_items_limit,
            ),
            tables=("latest_scan_items",),
            violations=violations,
        ),
        hit_record_preview_items=read_mapper_value(
            lambda: app_context.repositories.match_history.list_by_targets(
                target_ids,
                limit_per_target=5,
                notified_since=session_started_at,
            ),
            tables=("match_history",),
            violations=violations,
        ),
        hit_record_counts=app_context.repositories.match_history.count_by_targets(
            target_ids,
            notified_since=session_started_at,
        ),
    )


def _build_dashboard_rows(
    collections: DashboardReadCollections,
) -> tuple[TargetRow, ...]:
    """將 dashboard 批次讀取結果組成 target rows。"""

    rows: list[TargetRow] = []
    for target in collections.targets:
        config = collections.configs_by_target[target.id]
        rows.append(
            _build_target_row(
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


def _read_dashboard_targets(
    app_context: ApplicationContext,
    *,
    violations: tuple[DatabaseInvariantViolation, ...],
) -> list[TargetDescriptor]:
    """讀取 dashboard targets；inactive 壞 invariant row 只跳過，不拖垮首頁。"""

    try:
        return read_mapper_value(
            lambda: app_context.repositories.targets.list_all(),
            tables=("targets",),
            violations=violations,
        )
    except ReadModelInvariantMapperError:
        skipped_ids = inactive_target_invariant_row_ids(
            app_context.repositories.targets.connection,
            violations=violations,
        )
        if not skipped_ids:
            raise
        try:
            return list_targets_excluding_ids(
                app_context.repositories.targets.connection,
                skipped_ids=skipped_ids,
            )
        except ValueError as exc:
            if is_invariant_backed_mapper_error(
                exc,
                violations=violations,
                tables=("targets",),
            ):
                raise ReadModelInvariantMapperError(str(exc)) from exc
            raise


def _skip_inactive_runtime_invariant_targets(
    app_context: ApplicationContext,
    *,
    targets: list[TargetDescriptor],
    violations: tuple[DatabaseInvariantViolation, ...],
) -> list[TargetDescriptor]:
    """移除 inactive 且 runtime invariant 壞掉的 target，避免 read model 全頁降級。"""

    skipped_ids = inactive_runtime_invariant_row_ids(
        app_context.repositories.runtime_states.connection,
        violations=violations,
    )
    if not skipped_ids:
        return targets
    return [target for target in targets if target.id not in skipped_ids]


def _degraded_dashboard_read_result(
    app_context: ApplicationContext,
    *,
    database_invariant_warning: DatabaseInvariantWarning,
) -> DashboardReadResult:
    """DB invariant 已壞且 mapper 無法讀取時，回傳可顯示警告的降級首頁。"""

    return DashboardReadResult(
        rows=(),
        sidebar_groups=_build_sidebar_groups(
            rows=(),
            placements_by_target={},
            groups=(),
            templates_by_group={},
        ),
        profile_session_status=app_context.repositories.app_settings
        .get_profile_session_status(),
        database_invariant_warning=database_invariant_warning,
        dashboard_degraded=True,
    )


def _build_profile_session_warning(
    status: object,
) -> ProfileSessionWarning:
    """將 repository 狀態轉成首頁顯示用警告文案。"""

    if not getattr(status, "needs_login", False):
        return ProfileSessionWarning()
    return ProfileSessionWarning(
        needs_login=True,
        reason=str(getattr(status, "reason", "")),
        message=(
            "Facebook 需要重新登入。請關閉並重新開啟程式，"
            "系統會先開啟 Facebook 登入視窗；完成登入後會自動進入 Web UI。"
        ),
    )


def _build_database_invariant_warning(
    violations: tuple[DatabaseInvariantViolation, ...],
) -> DatabaseInvariantWarning:
    """將 DB invariant 結果轉成首頁警告，不洩漏 row id。"""

    if not violations:
        return DatabaseInvariantWarning()
    tables = tuple(sorted({violation.table for violation in violations}))
    table_summary = "、".join(tables[:3])
    extra = f"（{table_summary}）" if table_summary else ""
    return DatabaseInvariantWarning(
        has_violations=True,
        violation_count=len(violations),
        tables=tables,
        message=(
            f"資料庫偵測到 {len(violations)} 個資料 invariant 異常{extra}。"
            "請到設定下載支援包或執行資料檢查工具；系統不會自動修復資料。"
        ),
    )


def _order_targets_by_sidebar(
    targets: list[TargetDescriptor],
    placements_by_target: dict[str, SidebarTargetPlacement],
    groups: tuple[SidebarGroup, ...],
) -> list[TargetDescriptor]:
    """依 sidebar group/order 排列 dashboard rows，不改 target repository 語義。"""

    target_by_id = {target.id: target for target in targets}
    original_index = {target.id: index for index, target in enumerate(targets)}
    group_order = {group.id: index for index, group in enumerate(groups)}

    def sort_key(target: TargetDescriptor) -> tuple[int, int, int, int]:
        placement = placements_by_target.get(target.id)
        group_id = placement.sidebar_group_id if placement else None
        sort_order = placement.sort_order if placement else original_index[target.id]
        if group_id in group_order:
            return (0, group_order[group_id], sort_order, original_index[target.id])
        return (1, 0, sort_order, original_index[target.id])

    return sorted(target_by_id.values(), key=sort_key)


def _build_sidebar_groups(
    *,
    rows: tuple[TargetRow, ...],
    placements_by_target: dict[str, SidebarTargetPlacement],
    groups: tuple[SidebarGroup, ...],
    templates_by_group: dict[str, SidebarGroupConfigTemplate],
) -> tuple[SidebarGroupSection, ...]:
    """依 rows 與 placement 建立 sidebar sections。"""

    rows_by_group: dict[str | None, list[TargetRow]] = {group.id: [] for group in groups}
    rows_by_group[None] = []
    known_group_ids = {group.id for group in groups}
    for row in rows:
        placement = placements_by_target.get(row.target_id)
        group_id = placement.sidebar_group_id if placement else None
        if group_id not in known_group_ids:
            group_id = None
        rows_by_group.setdefault(group_id, []).append(row)

    sections: list[SidebarGroupSection] = []
    for group in groups:
        sections.append(
            SidebarGroupSection(
                group_id=group.id,
                name=group.name,
                collapsed=group.collapsed,
                items=tuple(row.sidebar_item for row in rows_by_group.get(group.id, ())),
                template=templates_by_group.get(group.id),
            )
        )
    ungrouped_rows = rows_by_group.get(None, [])
    sections.append(
        SidebarGroupSection(
            group_id=None,
            name="未分組",
            items=tuple(row.sidebar_item for row in ungrouped_rows),
            is_system=True,
        )
    )
    return tuple(sections)


def _sidebar_templates_by_group(
    app_context: ApplicationContext,
    *,
    groups: tuple[SidebarGroup, ...],
    violations: tuple[DatabaseInvariantViolation, ...],
) -> dict[str, SidebarGroupConfigTemplate]:
    """讀取 sidebar group templates，讓壞 datetime row 走 invariant 降級。"""

    templates: dict[str, SidebarGroupConfigTemplate] = {}
    for group in groups:
        group_id = group.id

        def read_template() -> SidebarGroupConfigTemplate:
            return (
                app_context.repositories.sidebar_layout.get_template(group_id)
                or SidebarGroupConfigTemplate(sidebar_group_id=group_id)
            )

        templates[group.id] = read_mapper_value(
            read_template,
            tables=("sidebar_group_config_templates",),
            violations=violations,
        )
    return templates


def _configs_by_target(
    app_context: ApplicationContext,
    targets: list[TargetDescriptor],
) -> dict[str, TargetConfig]:
    """批次讀取 target-scoped config。"""

    target_configs = app_context.repositories.configs.list_for_targets(
        [target.id for target in targets]
    )
    configs: dict[str, TargetConfig] = {}
    for target in targets:
        config = target_configs.get(target.id)
        if config is None:
            config = app_context.services.targets.get_config_for_target(target)
        configs[target.id] = config
    return configs


def _build_target_row(
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


def _read_target_card_row(
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
    return _build_target_row(
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
                notified_since=session_started_at,
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
            notified_since=session_started_at,
        ),
    )


def _is_missing_schema_error(exc: sqlite3.OperationalError) -> bool:
    """判斷 read path 是否遇到尚未初始化的空資料庫。"""

    return "no such table" in str(exc).lower()
