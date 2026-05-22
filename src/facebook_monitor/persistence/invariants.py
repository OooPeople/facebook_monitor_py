"""SQLite 產品資料 invariant 檢查。

職責：提供 read-only schema contract 檢查，先把 enum、boolean、range 與
runtime 狀態不變式集中成可測工具。真正 CHECK constraint / table rebuild
需另走 migration，不在本模組直接修改資料。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.persistence.schema_contract import BOOLEAN_CONTRACTS
from facebook_monitor.persistence.schema_contract import ENUM_CONTRACTS
from facebook_monitor.persistence.schema_contract import RANGE_CONTRACTS


@dataclass(frozen=True)
class DatabaseInvariantViolation:
    """描述一筆資料 invariant 違反。"""

    table: str
    row_id: str
    field: str
    message: str

    def format(self) -> str:
        """回傳 CLI 可讀格式。"""

        return f"{self.table}[{self.row_id}].{self.field}: {self.message}"


def validate_database_invariants(
    connection: sqlite3.Connection,
) -> tuple[DatabaseInvariantViolation, ...]:
    """回傳目前 DB 內所有已知 invariant 違反。"""

    violations: list[DatabaseInvariantViolation] = []
    violations.extend(_enum_violations(connection))
    violations.extend(_boolean_violations(connection))
    violations.extend(_range_violations(connection))
    violations.extend(_runtime_state_violations(connection))
    return tuple(violations)


def _enum_violations(connection: sqlite3.Connection) -> list[DatabaseInvariantViolation]:
    violations: list[DatabaseInvariantViolation] = []
    for contract in ENUM_CONTRACTS:
        allowed = tuple(sorted(contract.allowed_values))
        allowed_placeholders = ",".join("?" for _ in allowed)
        rows = connection.execute(
            f"""
            SELECT {contract.row_id_expr} AS row_id, {contract.field}
            FROM {contract.table}
            WHERE {contract.field} NOT IN ({allowed_placeholders})
            """,
            allowed,
        ).fetchall()
        violations.extend(
            DatabaseInvariantViolation(
                table=contract.table,
                row_id=str(row["row_id"]),
                field=contract.field,
                message=f"unexpected enum value {row[contract.field]!r}",
            )
            for row in rows
        )
    return violations


def _boolean_violations(connection: sqlite3.Connection) -> list[DatabaseInvariantViolation]:
    violations: list[DatabaseInvariantViolation] = []
    for contract in BOOLEAN_CONTRACTS:
        for field in contract.fields:
            rows = connection.execute(
                f"""
                SELECT {contract.row_id_column} AS row_id, {field}
                FROM {contract.table}
                WHERE {field} NOT IN (0, 1)
                """
            ).fetchall()
            violations.extend(
                DatabaseInvariantViolation(
                    table=contract.table,
                    row_id=str(row["row_id"]),
                    field=field,
                    message=f"expected boolean 0/1, got {row[field]!r}",
                )
                for row in rows
            )
    return violations


def _range_violations(connection: sqlite3.Connection) -> list[DatabaseInvariantViolation]:
    violations: list[DatabaseInvariantViolation] = []
    for contract in RANGE_CONTRACTS:
        rows = connection.execute(
            (
                f"SELECT {contract.row_id_column} AS row_id "
                f"FROM {contract.table} WHERE {contract.where_clause}"
            ),
            contract.params,
        ).fetchall()
        violations.extend(
            DatabaseInvariantViolation(
                table=contract.table,
                row_id=str(row["row_id"]),
                field=contract.field,
                message="value is outside product range",
            )
            for row in rows
        )
    return violations


def _runtime_state_violations(
    connection: sqlite3.Connection,
) -> list[DatabaseInvariantViolation]:
    violations: list[DatabaseInvariantViolation] = []
    running_rows = connection.execute(
        """
        SELECT target_id
        FROM target_runtime_state
        WHERE runtime_status = ?
          AND (active_worker_id = '' OR last_started_at = '' OR last_heartbeat_at = '')
        """,
        (TargetRuntimeStatus.RUNNING.value,),
    ).fetchall()
    violations.extend(
        DatabaseInvariantViolation(
            table="target_runtime_state",
            row_id=str(row["target_id"]),
            field="runtime_status",
            message="running state requires active worker, started_at and heartbeat",
        )
        for row in running_rows
    )
    idle_worker_rows = connection.execute(
        """
        SELECT target_id
        FROM target_runtime_state
        WHERE runtime_status != ?
          AND (active_worker_id != '' OR active_page_id != '')
        """,
        (TargetRuntimeStatus.RUNNING.value,),
    ).fetchall()
    violations.extend(
        DatabaseInvariantViolation(
            table="target_runtime_state",
            row_id=str(row["target_id"]),
            field="active_worker_id",
            message="non-running state must not keep active worker/page ownership",
        )
        for row in idle_worker_rows
    )
    return violations
