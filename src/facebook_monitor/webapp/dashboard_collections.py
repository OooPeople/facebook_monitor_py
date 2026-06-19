"""Dashboard read model 的批次讀取與 sidebar layout 排序。"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone

from facebook_monitor.application.context import ApplicationContext
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
from facebook_monitor.webapp.read_model_invariants import inactive_runtime_invariant_row_ids
from facebook_monitor.webapp.read_model_invariants import inactive_target_invariant_row_ids
from facebook_monitor.webapp.read_model_invariants import is_invariant_backed_mapper_error
from facebook_monitor.webapp.read_model_invariants import list_targets_excluding_ids
from facebook_monitor.webapp.read_model_invariants import ReadModelInvariantMapperError
from facebook_monitor.webapp.read_model_invariants import read_mapper_value


_MISSING_SIDEBAR_TEMPLATE_UPDATED_AT = datetime.fromtimestamp(0, tz=timezone.utc)


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


def read_dashboard_collections(
    app_context: ApplicationContext,
    *,
    violations: tuple[DatabaseInvariantViolation, ...],
    session_started_at: datetime | None,
) -> DashboardReadCollections:
    """批次讀取 dashboard rows 需要的 repository 資料。"""

    layout = read_dashboard_layout(app_context, violations=violations)
    targets = _skip_inactive_runtime_invariant_targets(
        app_context,
        targets=layout.targets,
        violations=violations,
    )
    target_ids = [target.id for target in targets]
    row_data = read_dashboard_row_data(
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


def read_dashboard_layout(
    app_context: ApplicationContext,
    *,
    violations: tuple[DatabaseInvariantViolation, ...],
) -> DashboardLayoutCollections:
    """讀取 target 與 sidebar layout，並套用 sidebar 排序。"""

    targets = read_dashboard_targets(app_context, violations=violations)
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


def read_dashboard_row_data(
    app_context: ApplicationContext,
    *,
    targets: list[TargetDescriptor],
    target_ids: list[str],
    violations: tuple[DatabaseInvariantViolation, ...],
    session_started_at: datetime | None,
) -> DashboardRowReadCollections:
    """批次讀取每張 target card 需要的 target-scoped 資料。"""

    configs_by_target = read_configs_by_target(app_context, targets)
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


def read_dashboard_targets(
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


def read_configs_by_target(
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
            template = app_context.repositories.sidebar_layout.get_template(group_id)
            if template is not None:
                return template
            return SidebarGroupConfigTemplate(
                sidebar_group_id=group_id,
                updated_at=_MISSING_SIDEBAR_TEMPLATE_UPDATED_AT,
            )

        templates[group.id] = read_mapper_value(
            read_template,
            tables=("sidebar_group_config_templates",),
            violations=violations,
        )
    return templates
