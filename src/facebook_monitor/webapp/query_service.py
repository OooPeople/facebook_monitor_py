"""Web UI read-side query helpers。

職責：集中整理首頁需要的 target/config/runtime/latest 資料，避免 FastAPI
route 直接散讀 repository，讓 UI route 專注處理 HTTP 流程。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.defaults import PYTHON_WEBUI_RUNTIME_DEFAULTS
from facebook_monitor.core.sidebar_models import SidebarGroup
from facebook_monitor.core.sidebar_models import SidebarGroupConfigTemplate
from facebook_monitor.core.sidebar_models import SidebarTargetPlacement
from facebook_monitor.persistence.repositories.app_settings import ProfileSessionStatus
from facebook_monitor.persistence.invariants import DatabaseInvariantViolation
from facebook_monitor.persistence.invariants import validate_database_invariants
from facebook_monitor.persistence.row_mappers import target_from_row
from facebook_monitor.persistence.schema_contract import DATETIME_CONTRACTS
from facebook_monitor.persistence.schema_contract import ENUM_CONTRACTS
from facebook_monitor.persistence.sqlite_codec import decode_datetime
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.persistence.sqlite_retry import is_sqlite_lock_error
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import NotificationOutboxSummary
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.webapp.dashboard_models import SidebarTargetItem
from facebook_monitor.webapp.dashboard_models import SidebarGroupSection
from facebook_monitor.webapp.dashboard_models import TargetRow
from facebook_monitor.webapp.hit_record_models import FullHitRecordRow
from facebook_monitor.webapp.preview_models import HitRecordPreviewRow
from facebook_monitor.webapp.preview_models import LatestScanItemRow

_ReadValue = TypeVar("_ReadValue")

_ENUM_ERROR_TYPE_BY_TABLE_FIELD = {
    ("targets", "target_kind"): "TargetKind",
    ("targets", "metadata_status"): "TargetMetadataStatus",
    ("targets", "worker_mode"): "WorkerMode",
    ("seen_items", "item_kind"): "ItemKind",
    ("match_history", "item_kind"): "ItemKind",
    ("latest_scan_items", "item_kind"): "ItemKind",
    ("logical_items", "item_kind"): "ItemKind",
    ("scan_runs", "status"): "ScanStatus",
    ("scan_runs", "worker_mode"): "WorkerMode",
    ("notification_events", "channel"): "NotificationChannel",
    ("notification_events", "status"): "NotificationStatus",
    ("notification_events", "event_kind"): "NotificationEventKind",
    ("notification_outbox", "item_kind"): "ItemKind",
    ("notification_outbox", "channel"): "NotificationChannel",
    ("notification_outbox", "status"): "NotificationOutboxStatus",
    ("notification_outbox", "event_kind"): "NotificationEventKind",
    ("notification_dedupe", "event_kind"): "NotificationEventKind",
    ("notification_dedupe", "channel"): "NotificationChannel",
    ("notification_dedupe", "item_kind"): "ItemKind",
    ("notification_dedupe", "status"): "NotificationDedupeStatus",
    ("target_runtime_state", "desired_state"): "TargetDesiredState",
    ("target_runtime_state", "runtime_status"): "TargetRuntimeStatus",
    ("target_cover_image_refresh_state", "status"): "TargetCoverImageRefreshStatus",
    ("target_cover_image_refresh_state", "last_result"): "TargetCoverImageRefreshResult",
}

_DATETIME_ERROR_FRAGMENTS_BY_TABLE = {
    "targets": ("target row has invalid datetime fields",),
    "match_history": ("match history row has invalid created_at",),
    "latest_scan_items": ("latest scan item row has invalid scanned_at",),
    "scan_runs": ("scan run row has invalid datetime fields",),
    "notification_events": ("notification event row has invalid created_at",),
    "notification_outbox": ("notification outbox row has invalid datetime fields",),
    "target_runtime_state": ("target runtime state row has invalid updated_at",),
    "target_cover_image_refresh_state": (
        "cover image refresh row has invalid updated_at",
    ),
    "sidebar_groups": ("sidebar group row has invalid datetime fields",),
    "sidebar_target_placements": (
        "sidebar target placement row has invalid updated_at",
    ),
    "sidebar_group_config_templates": (
        "sidebar group template row has invalid updated_at",
    ),
}


@dataclass(frozen=True)
class DashboardRevision:
    """保存 dashboard 變更偵測用的輕量 revision。"""

    revision: str
    last_changed_at: str = ""


class DashboardRevisionUnavailable(RuntimeError):
    """表示 dashboard revision 暫時被 SQLite write lock 擋住。"""


class DashboardReadUnavailable(RuntimeError):
    """表示 dashboard read model 暫時被 SQLite write lock 擋住。"""


class _DashboardInvariantMapperError(ValueError):
    """表示 dashboard mapper failure 已被 DB enum invariant 定位。"""


@dataclass(frozen=True)
class ProfileSessionWarning:
    """保存首頁右上角 Facebook 重新登入警告。"""

    needs_login: bool = False
    message: str = ""
    reason: str = ""


@dataclass(frozen=True)
class DatabaseInvariantWarning:
    """保存首頁資料 invariant 診斷警告。"""

    has_violations: bool = False
    message: str = ""
    violation_count: int = 0
    tables: tuple[str, ...] = ()


@dataclass(frozen=True)
class DashboardViewModel:
    """保存 dashboard template 所需 read model。"""

    rows: tuple[TargetRow, ...]
    sidebar_groups: tuple[SidebarGroupSection, ...] = ()
    profile_session_warning: ProfileSessionWarning = ProfileSessionWarning()
    database_invariant_warning: DatabaseInvariantWarning = DatabaseInvariantWarning()
    dashboard_degraded: bool = False

    @property
    def sidebar_items(self) -> tuple[SidebarTargetItem, ...]:
        """回傳 Phase 5 sidebar 使用的 target 摘要。"""

        return tuple(row.sidebar_item for row in self.rows)

    @property
    def sidebar_layout_signature(self) -> str:
        """回傳 sidebar group/order 結構簽章，供 partial update 判斷是否需 reload。"""

        payload = [
            {
                "group_id": group.dom_group_id,
                "name": group.name,
                "target_ids": [item.target_id for item in group.items],
            }
            for group in self.sidebar_groups
        ]
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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


@dataclass(frozen=True)
class _DashboardReadResult:
    """保存 dashboard read 內部結果。"""

    rows: tuple[TargetRow, ...]
    sidebar_groups: tuple[SidebarGroupSection, ...]
    profile_session_status: ProfileSessionStatus
    database_invariant_warning: DatabaseInvariantWarning
    dashboard_degraded: bool = False


def _read_dashboard_model(
    db_path: Path,
    *,
    initialize_schema_on_enter: bool,
    session_started_at: datetime | None,
) -> _DashboardReadResult:
    """讀取 dashboard rows 與 sidebar group sections。"""

    try:
        return _list_target_rows(
            db_path,
            initialize_schema_on_enter=initialize_schema_on_enter,
            session_started_at=session_started_at,
        )
    except sqlite3.OperationalError as exc:
        if (
            not initialize_schema_on_enter
            and _is_missing_schema_error(exc)
        ):
            return _read_dashboard_model(
                db_path,
                initialize_schema_on_enter=True,
                session_started_at=session_started_at,
            )
        _raise_dashboard_read_unavailable_if_locked(exc)
        raise


def _list_target_rows(
    db_path: Path,
    *,
    initialize_schema_on_enter: bool,
    session_started_at: datetime | None,
) -> _DashboardReadResult:
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
            targets = _read_dashboard_targets(
                app_context,
                violations=database_invariant_violations,
            )
            target_ids = [target.id for target in targets]
            placements_by_target = _read_dashboard_mapper_value(
                lambda: app_context.repositories.sidebar_layout.list_placements(),
                tables=("sidebar_target_placements",),
                violations=database_invariant_violations,
            )
            groups = _read_dashboard_mapper_value(
                lambda: app_context.repositories.sidebar_layout.list_groups(),
                tables=("sidebar_groups",),
                violations=database_invariant_violations,
            )
            targets = _order_targets_by_sidebar(targets, placements_by_target, groups)
            targets = _skip_inactive_runtime_invariant_targets(
                app_context,
                targets=targets,
                violations=database_invariant_violations,
            )
            target_ids = [target.id for target in targets]
            configs_by_target = _configs_by_target(app_context, targets)
            runtime_by_target = _read_dashboard_mapper_value(
                lambda: app_context.repositories.runtime_states.list_by_targets(
                    target_ids
                ),
                tables=("target_runtime_state",),
                violations=database_invariant_violations,
            )
            latest_scan_runs = _read_dashboard_mapper_value(
                lambda: app_context.repositories.scan_runs.latest_by_targets(target_ids),
                tables=("scan_runs",),
                violations=database_invariant_violations,
            )
            latest_failed_scan_runs = _read_dashboard_mapper_value(
                lambda: app_context.repositories.scan_runs.latest_by_targets(
                    target_ids,
                    status=ScanStatus.FAILED,
                ),
                tables=("scan_runs",),
                violations=database_invariant_violations,
            )
            outbox_summaries = _read_dashboard_mapper_value(
                lambda: app_context.repositories.notification_outbox.summarize_by_targets(
                    target_ids
                ),
                tables=("notification_outbox",),
                violations=database_invariant_violations,
            )
            max_items_limit = max(
                (config.max_items_per_scan for config in configs_by_target.values()),
                default=1,
            )
            latest_scan_items = _read_dashboard_mapper_value(
                lambda: app_context.repositories.latest_scan_items.list_by_targets(
                    target_ids,
                    limit_per_target=max_items_limit,
                ),
                tables=("latest_scan_items",),
                violations=database_invariant_violations,
            )
            hit_record_preview_items = _read_dashboard_mapper_value(
                lambda: app_context.repositories.match_history.list_by_targets(
                    target_ids,
                    limit_per_target=5,
                    notified_since=session_started_at,
                ),
                tables=("match_history",),
                violations=database_invariant_violations,
            )
            hit_record_counts = app_context.repositories.match_history.count_by_targets(
                target_ids,
                notified_since=session_started_at,
            )
            rows = tuple(
                _build_target_row(
                    target=target,
                    config=configs_by_target[target.id],
                    runtime_state=runtime_by_target.get(
                        target.id,
                        TargetRuntimeState(target_id=target.id),
                    ),
                    latest_scan_items=latest_scan_items.get(target.id, ())[
                        : configs_by_target[target.id].max_items_per_scan
                    ],
                    hit_record_preview_items=hit_record_preview_items.get(target.id, ()),
                    latest_scan_run=latest_scan_runs.get(target.id),
                    latest_failed_scan_run=latest_failed_scan_runs.get(target.id),
                    notification_outbox_summary=outbox_summaries.get(target.id),
                    hit_record_total_count=hit_record_counts.get(target.id, 0),
                )
                for target in targets
            )
            sidebar_groups = _build_sidebar_groups(
                rows=rows,
                placements_by_target=placements_by_target,
                groups=groups,
                templates_by_group=_sidebar_templates_by_group(
                    app_context,
                    groups=groups,
                    violations=database_invariant_violations,
                ),
            )
        except _DashboardInvariantMapperError:
            return _degraded_dashboard_read_result(
                app_context,
                database_invariant_warning=database_invariant_warning,
            )
        return _DashboardReadResult(
            rows=rows,
            sidebar_groups=sidebar_groups,
            profile_session_status=app_context.repositories.app_settings
            .get_profile_session_status(),
            database_invariant_warning=database_invariant_warning,
        )


def _read_dashboard_targets(
    app_context: ApplicationContext,
    *,
    violations: tuple[DatabaseInvariantViolation, ...],
) -> list[TargetDescriptor]:
    """讀取 dashboard targets；inactive 壞 invariant row 只跳過，不拖垮首頁。"""

    try:
        return _read_dashboard_mapper_value(
            lambda: app_context.repositories.targets.list_all(),
            tables=("targets",),
            violations=violations,
        )
    except _DashboardInvariantMapperError:
        skipped_ids = _inactive_target_invariant_row_ids(
            app_context.repositories.targets.connection,
            violations=violations,
        )
        if not skipped_ids:
            raise
        try:
            return _list_targets_excluding_ids(
                app_context.repositories.targets.connection,
                skipped_ids=skipped_ids,
            )
        except ValueError as exc:
            if _is_invariant_backed_mapper_error(
                exc,
                violations=violations,
                tables=("targets",),
            ):
                raise _DashboardInvariantMapperError(str(exc)) from exc
            raise


def _skip_inactive_runtime_invariant_targets(
    app_context: ApplicationContext,
    *,
    targets: list[TargetDescriptor],
    violations: tuple[DatabaseInvariantViolation, ...],
) -> list[TargetDescriptor]:
    """移除 inactive 且 runtime invariant 壞掉的 target，避免 read model 全頁降級。"""

    skipped_ids = _inactive_runtime_invariant_row_ids(
        app_context.repositories.runtime_states.connection,
        violations=violations,
    )
    if not skipped_ids:
        return targets
    return [target for target in targets if target.id not in skipped_ids]


def _inactive_invariant_target_ids(
    connection: sqlite3.Connection,
    *,
    violations: tuple[DatabaseInvariantViolation, ...],
) -> set[str]:
    """回傳 Web read model 可安全略過的 inactive/paused 壞 target ids。"""

    return _inactive_target_invariant_row_ids(
        connection,
        violations=violations,
    ) | _inactive_runtime_invariant_row_ids(
        connection,
        violations=violations,
    )


def _inactive_target_invariant_row_ids(
    connection: sqlite3.Connection,
    *,
    violations: tuple[DatabaseInvariantViolation, ...],
) -> set[str]:
    """回傳 targets 表中 inactive/paused 且有 invariant violation 的 row id。"""

    candidate_ids = {
        violation.row_id
        for violation in violations
        if violation.table == "targets"
        and violation.field
        in {"target_kind", "metadata_status", "worker_mode", "created_at", "updated_at"}
    }
    return _inactive_target_ids(connection, candidate_ids)


def _inactive_runtime_invariant_row_ids(
    connection: sqlite3.Connection,
    *,
    violations: tuple[DatabaseInvariantViolation, ...],
) -> set[str]:
    """回傳 runtime 表中所屬 target 非 active hot path 的壞 row id。"""

    candidate_ids = {
        violation.row_id
        for violation in violations
        if violation.table == "target_runtime_state"
        and violation.field
        in {
            "desired_state",
            "runtime_status",
            "scan_requested_at",
            "last_enqueued_at",
            "last_started_at",
            "last_finished_at",
            "last_heartbeat_at",
            "last_page_reloaded_at",
            "display_next_due_at",
            "updated_at",
        }
    }
    return _inactive_target_ids(connection, candidate_ids)


def _inactive_target_ids(
    connection: sqlite3.Connection,
    candidate_ids: set[str],
) -> set[str]:
    """從候選 target ids 中挑出 scheduler hot path 以外的 row。"""

    if not candidate_ids:
        return set()
    placeholders = ",".join("?" for _ in candidate_ids)
    rows = connection.execute(
        f"""
        SELECT id
        FROM targets
        WHERE id IN ({placeholders})
          AND (enabled != 1 OR paused != 0)
        """,
        tuple(sorted(candidate_ids)),
    ).fetchall()
    return {str(row["id"]) for row in rows}


def _list_targets_excluding_ids(
    connection: sqlite3.Connection,
    *,
    skipped_ids: set[str],
) -> list[TargetDescriptor]:
    """直接讀取可 decode targets，排除已確認 inactive 的壞 row。"""

    placeholders = ",".join("?" for _ in skipped_ids)
    rows = connection.execute(
        f"""
        SELECT *
        FROM targets
        WHERE id NOT IN ({placeholders})
        ORDER BY created_at
        """,
        tuple(sorted(skipped_ids)),
    ).fetchall()
    return [target_from_row(row) for row in rows]


def _read_dashboard_mapper_value(
    operation: Callable[[], _ReadValue],
    *,
    tables: tuple[str, ...],
    violations: tuple[DatabaseInvariantViolation, ...],
) -> _ReadValue:
    """讀取會跑 row mapper 的 repository；只讓已知 invariant 壞資料降級。"""

    try:
        return operation()
    except ValueError as exc:
        if _is_invariant_backed_mapper_error(
            exc,
            violations=violations,
            tables=tables,
        ):
            raise _DashboardInvariantMapperError(str(exc)) from exc
        raise


def _is_invariant_backed_mapper_error(
    exc: ValueError,
    *,
    violations: tuple[DatabaseInvariantViolation, ...],
    tables: tuple[str, ...],
) -> bool:
    """判斷 mapper 錯誤是否可由已知 invariant violation 安全降級承接。"""

    enum_contract_fields = {(contract.table, contract.field) for contract in ENUM_CONTRACTS}
    datetime_contract_fields = {
        (contract.table, field)
        for contract in DATETIME_CONTRACTS
        for field in contract.fields
    }
    candidate_tables = set(tables)
    has_datetime_violation = any(
        violation.table in candidate_tables
        and (violation.table, violation.field) in datetime_contract_fields
        for violation in violations
    )
    datetime_violations = tuple(
        violation
        for violation in violations
        if violation.table in candidate_tables
        and (violation.table, violation.field) in datetime_contract_fields
    )
    violated_type_names = {
        type_name
        for violation in violations
        if violation.table in candidate_tables
        and (violation.table, violation.field) in enum_contract_fields
        for type_name in (
            _ENUM_ERROR_TYPE_BY_TABLE_FIELD.get((violation.table, violation.field)),
        )
        if type_name
    }
    message = str(exc)
    if has_datetime_violation and _matches_datetime_mapper_error(
        message,
        violations=datetime_violations,
    ):
        return True
    return any(f"is not a valid {type_name}" in message for type_name in violated_type_names)


def _matches_datetime_mapper_error(
    message: str,
    *,
    violations: tuple[DatabaseInvariantViolation, ...],
) -> bool:
    """判斷 ValueError 是否符合已知 datetime mapper failure 形狀。"""

    return any(
        fragment in message
        for table in {violation.table for violation in violations}
        for fragment in _DATETIME_ERROR_FRAGMENTS_BY_TABLE.get(table, ())
    ) or (
        "Invalid isoformat string" in message
        and any(_datetime_violation_value_matches(message, violation) for violation in violations)
    )


def _datetime_violation_value_matches(
    message: str,
    violation: DatabaseInvariantViolation,
) -> bool:
    """確認 raw datetime parser error 指到 invariant 已定位的同一個壞值。"""

    prefix = "invalid datetime value "
    if not violation.message.startswith(prefix):
        return False
    value_repr = violation.message[len(prefix):]
    return bool(value_repr and value_repr in message)


def _degraded_dashboard_read_result(
    app_context: ApplicationContext,
    *,
    database_invariant_warning: DatabaseInvariantWarning,
) -> _DashboardReadResult:
    """DB invariant 已壞且 mapper 無法讀取時，回傳可顯示警告的降級首頁。"""

    return _DashboardReadResult(
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


def _build_profile_session_warning(
    status: ProfileSessionStatus,
) -> ProfileSessionWarning:
    """將 repository 狀態轉成首頁顯示用警告文案。"""

    if not status.needs_login:
        return ProfileSessionWarning()
    return ProfileSessionWarning(
        needs_login=True,
        reason=status.reason,
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
        with _read_application_context(db_path) as app_context:
            violations = validate_database_invariants(
                app_context.repositories.targets.connection
            )
            if target_id in _inactive_invariant_target_ids(
                app_context.repositories.targets.connection,
                violations=violations,
            ):
                return None
            target = _read_dashboard_mapper_value(
                lambda: app_context.repositories.targets.get(target_id),
                tables=("targets",),
                violations=violations,
            )
            if target is None:
                return None
            config = _configs_by_target(app_context, [target])[target.id]
            runtime_state = _read_dashboard_mapper_value(
                lambda: app_context.repositories.runtime_states.get(target.id),
                tables=("target_runtime_state",),
                violations=violations,
            ) or TargetRuntimeState(target_id=target.id)
            return _build_target_row(
                target=target,
                config=config,
                runtime_state=runtime_state,
                latest_scan_items=_read_dashboard_mapper_value(
                    lambda: app_context.repositories.latest_scan_items.list_by_target(
                        target.id,
                        limit=config.max_items_per_scan,
                    ),
                    tables=("latest_scan_items",),
                    violations=violations,
                ),
                hit_record_preview_items=_read_dashboard_mapper_value(
                    lambda: app_context.repositories.match_history.list_by_target(
                        target.id,
                        limit=5,
                        notified_since=session_started_at,
                    ),
                    tables=("match_history",),
                    violations=violations,
                ),
                latest_scan_run=_read_dashboard_mapper_value(
                    lambda: app_context.repositories.scan_runs.latest_by_target(target.id),
                    tables=("scan_runs",),
                    violations=violations,
                ),
                latest_failed_scan_run=_read_dashboard_mapper_value(
                    lambda: app_context.repositories.scan_runs.latest_by_target(
                        target.id,
                        status=ScanStatus.FAILED,
                    ),
                    tables=("scan_runs",),
                    violations=violations,
                ),
                notification_outbox_summary=_read_dashboard_mapper_value(
                    lambda: (
                        app_context.repositories.notification_outbox
                        .summarize_by_targets([target.id])
                        .get(target.id)
                    ),
                    tables=("notification_outbox",),
                    violations=violations,
                ),
                hit_record_total_count=app_context.repositories.match_history.count_by_target(
                    target.id,
                    notified_since=session_started_at,
                ),
            )
    except sqlite3.OperationalError as exc:
        _raise_dashboard_read_unavailable_if_locked(exc)
        raise
    except _DashboardInvariantMapperError as exc:
        raise DashboardReadUnavailable(str(exc)) from exc


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

        templates[group.id] = _read_dashboard_mapper_value(
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

    target_configs = app_context.repositories.configs.list_for_targets([target.id for target in targets])
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
    latest_scan_items: list[LatestScanItem] | tuple[LatestScanItem, ...],
    hit_record_preview_items: list[MatchHistoryEntry] | tuple[MatchHistoryEntry, ...],
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


def _read_application_context(db_path: Path) -> SqliteApplicationContext:
    """建立 Web UI read model 用 context，避免每輪 partial update 跑 schema init。"""

    return SqliteApplicationContext(db_path, initialize_schema_on_enter=False)


def _raise_dashboard_read_unavailable_if_locked(exc: sqlite3.OperationalError) -> None:
    """將 SQLite lock 轉成 route 可處理的 read model 暫不可用錯誤。"""

    if is_sqlite_lock_error(exc):
        raise DashboardReadUnavailable(str(exc)) from exc


def _is_missing_schema_error(exc: sqlite3.OperationalError) -> bool:
    """判斷 read path 是否遇到尚未初始化的空資料庫。"""

    return "no such table" in str(exc).lower()


def target_exists(db_path: Path, target_id: str) -> bool:
    """檢查 target 是否存在，供 API route 回傳明確 404。"""

    try:
        with _read_application_context(db_path) as app_context:
            violations = validate_database_invariants(
                app_context.repositories.targets.connection
            )
            if target_id in _inactive_invariant_target_ids(
                app_context.repositories.targets.connection,
                violations=violations,
            ):
                return False
            if _has_target_or_runtime_invariant_violation(target_id, violations):
                raise DashboardReadUnavailable("database invariant violation")
            return (
                _read_dashboard_mapper_value(
                    lambda: app_context.repositories.targets.get(target_id),
                    tables=("targets",),
                    violations=violations,
                )
                is not None
            )
    except sqlite3.OperationalError as exc:
        _raise_dashboard_read_unavailable_if_locked(exc)
        raise
    except _DashboardInvariantMapperError as exc:
        raise DashboardReadUnavailable(str(exc)) from exc


def _has_target_or_runtime_invariant_violation(
    target_id: str,
    violations: tuple[DatabaseInvariantViolation, ...],
) -> bool:
    """判斷 target hot path 是否有不可忽略的 target/runtime invariant violation。"""

    return any(
        violation.row_id == target_id
        and violation.table in {"targets", "target_runtime_state"}
        for violation in violations
    )


def list_hit_record_preview_rows(
    db_path: Path,
    target_id: str,
    *,
    limit: int = PYTHON_WEBUI_RUNTIME_DEFAULTS.hit_record_preview_limit,
    session_started_at: datetime | None = None,
) -> tuple[HitRecordPreviewRow, ...]:
    """讀取單一 target 的命中紀錄 preview rows。"""

    try:
        with _read_application_context(db_path) as app_context:
            violations = validate_database_invariants(
                app_context.repositories.targets.connection
            )
            return tuple(
                HitRecordPreviewRow(entry=entry)
                for entry in _read_dashboard_mapper_value(
                    lambda: app_context.repositories.match_history.list_by_target(
                        target_id,
                        limit=limit,
                        notified_since=session_started_at,
                    ),
                    tables=("match_history",),
                    violations=violations,
                )
            )
    except sqlite3.OperationalError as exc:
        _raise_dashboard_read_unavailable_if_locked(exc)
        raise
    except _DashboardInvariantMapperError as exc:
        raise DashboardReadUnavailable(str(exc)) from exc


def list_full_hit_record_rows(
    db_path: Path,
    target_id: str,
    *,
    limit: int = PYTHON_WEBUI_RUNTIME_DEFAULTS.hit_record_full_limit,
    offset: int = 0,
) -> tuple[FullHitRecordRow, ...]:
    """讀取單一 target 的完整命中紀錄 rows。"""

    bounded_offset = max(int(offset), 0)
    try:
        with _read_application_context(db_path) as app_context:
            violations = validate_database_invariants(
                app_context.repositories.targets.connection
            )
            entries = _read_dashboard_mapper_value(
                lambda: app_context.repositories.match_history.list_by_target(
                    target_id,
                    limit=limit,
                    offset=bounded_offset,
                ),
                tables=("match_history",),
                violations=violations,
            )
            notification_events = _read_dashboard_mapper_value(
                lambda: (
                    app_context.repositories.notification_events
                    .latest_sent_by_target_item_keys(
                        target_id,
                        [entry.item_key for entry in entries],
                    )
                ),
                tables=("notification_events",),
                violations=violations,
            )
    except sqlite3.OperationalError as exc:
        _raise_dashboard_read_unavailable_if_locked(exc)
        raise
    except _DashboardInvariantMapperError as exc:
        raise DashboardReadUnavailable(str(exc)) from exc
    return tuple(
        FullHitRecordRow(
            entry=entry,
            sequence_number=bounded_offset + index + 1,
            notification_event=notification_events.get(entry.item_key),
        )
        for index, entry in enumerate(entries)
    )


def count_hit_records(
    db_path: Path,
    target_id: str,
    *,
    session_started_at: datetime | None = None,
) -> int:
    """計算單一 target 的命中紀錄筆數。"""

    try:
        with _read_application_context(db_path) as app_context:
            _raise_if_hit_record_count_is_invariant_unsafe(
                app_context.repositories.match_history.connection,
                target_id=target_id,
                notified_since=session_started_at,
            )
            return app_context.repositories.match_history.count_by_target(
                target_id,
                notified_since=session_started_at,
            )
    except sqlite3.OperationalError as exc:
        _raise_dashboard_read_unavailable_if_locked(exc)
        raise


def _raise_if_hit_record_count_is_invariant_unsafe(
    connection: sqlite3.Connection,
    *,
    target_id: str,
    notified_since: datetime | None,
) -> None:
    """確認 count 查詢範圍內沒有會讓 hit-record mapper 失敗的 datetime。"""

    rows = connection.execute(
        """
        SELECT id, notified_at, created_at
        FROM match_history
        WHERE target_id = ?
        """,
        (target_id,),
    ).fetchall()
    since_text = encode_datetime(notified_since)
    for row in rows:
        if _hit_record_datetime_row_blocks_count(row, since_text=since_text):
            raise DashboardReadUnavailable("database invariant violation")


def _hit_record_datetime_row_blocks_count(
    row: sqlite3.Row,
    *,
    since_text: str,
) -> bool:
    """回傳單筆 match_history datetime 是否會讓對應 read model 不可讀。"""

    notified_at = str(row["notified_at"] or "")
    if since_text:
        if not notified_at:
            return False
        if not _is_valid_datetime_text(notified_at):
            return True
        if notified_at < since_text:
            return False
    elif notified_at and not _is_valid_datetime_text(notified_at):
        return True
    created_at = str(row["created_at"] or "")
    return not created_at or not _is_valid_datetime_text(created_at)


def _is_valid_datetime_text(value: str) -> bool:
    """檢查 SQLite datetime text 是否可被既有 mapper decode。"""

    try:
        decode_datetime(value)
    except ValueError:
        return False
    return True


def get_dashboard_revision(db_path: Path) -> DashboardRevision:
    """用 read-only connection 讀取首頁 revision，避免 SSE 觸發 schema init。"""

    if not db_path.exists():
        return DashboardRevision(revision="0", last_changed_at="")
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.OperationalError as exc:
        if is_sqlite_lock_error(exc):
            raise DashboardRevisionUnavailable(str(exc)) from exc
        return DashboardRevision(revision="0", last_changed_at="")
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        row = connection.execute(
            "SELECT revision, updated_at FROM dashboard_revision WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if is_sqlite_lock_error(exc):
            raise DashboardRevisionUnavailable(str(exc)) from exc
        if "no such table" not in message:
            raise
        return DashboardRevision(revision="0", last_changed_at="")
    finally:
        connection.close()
    if row is None:
        return DashboardRevision(revision="0", last_changed_at="")
    return DashboardRevision(
        revision=str(row["revision"]),
        last_changed_at=row["updated_at"],
    )
