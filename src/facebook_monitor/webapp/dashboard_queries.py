"""Dashboard read-side query facade。"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.persistence.invariants import validate_database_invariants
from facebook_monitor.webapp.dashboard_collections import read_configs_by_target
from facebook_monitor.webapp.dashboard_collections import read_dashboard_collections
from facebook_monitor.webapp.dashboard_models import SidebarTargetItem
from facebook_monitor.webapp.dashboard_models import TargetRow
from facebook_monitor.webapp.dashboard_read_models import DashboardReadResult
from facebook_monitor.webapp.dashboard_read_models import DashboardReadUnavailable
from facebook_monitor.webapp.dashboard_read_models import DashboardViewModel
from facebook_monitor.webapp.dashboard_read_models import DatabaseInvariantWarning
from facebook_monitor.webapp.dashboard_rows import build_dashboard_rows
from facebook_monitor.webapp.dashboard_rows import read_target_card_row
from facebook_monitor.webapp.dashboard_sidebar import build_sidebar_groups
from facebook_monitor.webapp.dashboard_warnings import build_database_invariant_warning
from facebook_monitor.webapp.dashboard_warnings import build_profile_session_warning
from facebook_monitor.webapp.read_model_context import read_application_context
from facebook_monitor.webapp.read_model_context import (
    raise_dashboard_read_unavailable_if_locked,
)
from facebook_monitor.webapp.read_model_invariants import inactive_invariant_target_ids
from facebook_monitor.webapp.read_model_invariants import ReadModelInvariantMapperError
from facebook_monitor.webapp.read_model_invariants import read_mapper_value


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
        profile_session_warning=build_profile_session_warning(
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
    """讀取單一 target card read model，供 partial update 使用。"""

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
            config = read_configs_by_target(app_context, [target])[target.id]
            runtime_state = read_mapper_value(
                lambda: app_context.repositories.runtime_states.get(target.id),
                tables=("target_runtime_state",),
                violations=violations,
            ) or TargetRuntimeState(target_id=target.id)
            return read_target_card_row(
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
        database_invariant_warning = build_database_invariant_warning(
            database_invariant_violations
        )
        try:
            collections = read_dashboard_collections(
                app_context,
                violations=database_invariant_violations,
                session_started_at=session_started_at,
            )
            rows = build_dashboard_rows(collections)
            sidebar_groups = build_sidebar_groups(
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


def _degraded_dashboard_read_result(
    app_context: ApplicationContext,
    *,
    database_invariant_warning: DatabaseInvariantWarning,
) -> DashboardReadResult:
    """DB invariant 已壞且 mapper 無法讀取時，回傳可顯示警告的降級首頁。"""

    return DashboardReadResult(
        rows=(),
        sidebar_groups=build_sidebar_groups(
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


def _is_missing_schema_error(exc: sqlite3.OperationalError) -> bool:
    """判斷 read path 是否遇到尚未初始化的空資料庫。"""

    return "no such table" in str(exc).lower()
