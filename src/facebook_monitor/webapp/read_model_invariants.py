"""Web read model invariant guards and mapper downgrade helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import TypeVar

from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.persistence.invariants import DatabaseInvariantViolation
from facebook_monitor.persistence.row_mappers import target_from_row
from facebook_monitor.persistence.schema_contract import DATETIME_CONTRACTS
from facebook_monitor.persistence.schema_contract import ENUM_CONTRACTS

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


class ReadModelInvariantMapperError(ValueError):
    """表示 Web read model mapper failure 已被 DB invariant 定位。"""


def read_mapper_value(
    operation: Callable[[], _ReadValue],
    *,
    tables: tuple[str, ...],
    violations: tuple[DatabaseInvariantViolation, ...],
) -> _ReadValue:
    """讀取會跑 row mapper 的 repository；只讓已知 invariant 壞資料降級。"""

    try:
        return operation()
    except ValueError as exc:
        if is_invariant_backed_mapper_error(
            exc,
            violations=violations,
            tables=tables,
        ):
            raise ReadModelInvariantMapperError(str(exc)) from exc
        raise


def is_invariant_backed_mapper_error(
    exc: ValueError,
    *,
    violations: tuple[DatabaseInvariantViolation, ...],
    tables: tuple[str, ...],
) -> bool:
    """判斷 mapper 錯誤是否可由已知 invariant violation 安全降級承接。"""

    message = str(exc)
    datetime_violations = _datetime_violations_for_tables(violations, tables=tables)
    if datetime_violations and _matches_datetime_mapper_error(
        message,
        violations=datetime_violations,
    ):
        return True
    return _matches_enum_mapper_error(
        message,
        type_names=_enum_type_names_for_tables(violations, tables=tables),
    )


def inactive_invariant_target_ids(
    connection: sqlite3.Connection,
    *,
    violations: tuple[DatabaseInvariantViolation, ...],
) -> set[str]:
    """回傳 Web read model 可安全略過的 inactive/paused 壞 target ids。"""

    return inactive_target_invariant_row_ids(
        connection,
        violations=violations,
    ) | inactive_runtime_invariant_row_ids(
        connection,
        violations=violations,
    )


def inactive_target_invariant_row_ids(
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


def inactive_runtime_invariant_row_ids(
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


def list_targets_excluding_ids(
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


def has_target_or_runtime_invariant_violation(
    target_id: str,
    violations: tuple[DatabaseInvariantViolation, ...],
) -> bool:
    """判斷 target hot path 是否有不可忽略的 target/runtime invariant violation。"""

    return any(
        violation.row_id == target_id
        and violation.table in {"targets", "target_runtime_state"}
        for violation in violations
    )


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


def _datetime_violations_for_tables(
    violations: tuple[DatabaseInvariantViolation, ...],
    *,
    tables: tuple[str, ...],
) -> tuple[DatabaseInvariantViolation, ...]:
    """篩出候選 table 中已被 schema contract 定位的 datetime violations。"""

    candidate_tables = set(tables)
    contract_fields = _datetime_contract_fields()
    return tuple(
        violation
        for violation in violations
        if violation.table in candidate_tables
        and (violation.table, violation.field) in contract_fields
    )


def _enum_type_names_for_tables(
    violations: tuple[DatabaseInvariantViolation, ...],
    *,
    tables: tuple[str, ...],
) -> set[str]:
    """回傳候選 table 中已違反 enum contract 的 domain enum 名稱。"""

    candidate_tables = set(tables)
    contract_fields = _enum_contract_fields()
    return {
        type_name
        for violation in violations
        if violation.table in candidate_tables
        and (violation.table, violation.field) in contract_fields
        for type_name in (
            _ENUM_ERROR_TYPE_BY_TABLE_FIELD.get((violation.table, violation.field)),
        )
        if type_name
    }


def _matches_enum_mapper_error(message: str, *, type_names: set[str]) -> bool:
    """判斷 ValueError 是否符合 enum mapper 的固定錯誤格式。"""

    return any(f"is not a valid {type_name}" in message for type_name in type_names)


def _enum_contract_fields() -> set[tuple[str, str]]:
    """回傳 schema contract 覆蓋的 enum 欄位集合。"""

    return {(contract.table, contract.field) for contract in ENUM_CONTRACTS}


def _datetime_contract_fields() -> set[tuple[str, str]]:
    """回傳 schema contract 覆蓋的 datetime 欄位集合。"""

    return {
        (contract.table, field)
        for contract in DATETIME_CONTRACTS
        for field in contract.fields
    }


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
