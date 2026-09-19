"""SQLite 產品資料 invariant 檢查。

職責：提供 read-only schema contract 檢查，先把 enum、boolean、range 與
runtime 狀態不變式集中成可測工具。真正 CHECK constraint / table rebuild
需另走 migration，不在本模組直接修改資料。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.persistence.schema_contract import BOOLEAN_CONTRACTS
from facebook_monitor.persistence.schema_contract import DATETIME_CONTRACTS
from facebook_monitor.persistence.schema_contract import ENUM_CONTRACTS
from facebook_monitor.persistence.schema_contract import RANGE_CONTRACTS
from facebook_monitor.persistence.sqlite_codec import decode_datetime


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
    violations.extend(_datetime_violations(connection))
    violations.extend(_runtime_state_violations(connection))
    violations.extend(_facebook_access_circuit_state_violations(connection))
    violations.extend(_facebook_access_utc_datetime_violations(connection))
    violations.extend(_managed_profile_identity_binding_violations(connection))
    violations.extend(_facebook_session_recovery_state_violations(connection))
    violations.extend(_duplicate_target_scope_violations(connection))
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


def _datetime_violations(connection: sqlite3.Connection) -> list[DatabaseInvariantViolation]:
    violations: list[DatabaseInvariantViolation] = []
    for contract in DATETIME_CONTRACTS:
        fields = ", ".join(contract.fields)
        rows = connection.execute(
            f"SELECT {contract.row_id_column} AS row_id, {fields} FROM {contract.table}"
        ).fetchall()
        required_fields = set(contract.required_fields)
        for row in rows:
            row_id = str(row["row_id"])
            for field in contract.fields:
                value = row[field]
                if not value:
                    if field in required_fields:
                        violations.append(
                            DatabaseInvariantViolation(
                                table=contract.table,
                                row_id=row_id,
                                field=field,
                                message="datetime value is required",
                            )
                        )
                    continue
                try:
                    decode_datetime(str(value))
                except ValueError:
                    violations.append(
                        DatabaseInvariantViolation(
                            table=contract.table,
                            row_id=row_id,
                            field=field,
                            message=f"invalid datetime value {value!r}",
                        )
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


def _duplicate_target_scope_violations(
    connection: sqlite3.Connection,
) -> list[DatabaseInvariantViolation]:
    """回報 target kind/scope 重複，不在 invariant checker 內修資料。"""

    rows = connection.execute(
        """
        SELECT target_kind, scope_id, GROUP_CONCAT(id, ',') AS target_ids, COUNT(*) AS count
        FROM targets
        GROUP BY target_kind, scope_id
        HAVING count > 1
        ORDER BY target_kind, scope_id
        """
    ).fetchall()
    return [
        DatabaseInvariantViolation(
            table="targets",
            row_id=str(row["target_ids"]),
            field="scope_id",
            message=(
                "duplicate target scope "
                f"{row['target_kind']}:{row['scope_id']} count={row['count']}"
            ),
        )
        for row in rows
    ]


def _facebook_access_circuit_state_violations(
    connection: sqlite3.Connection,
) -> list[DatabaseInvariantViolation]:
    """檢查 circuit cross-field state，不把 opaque profile key 輸出到 diagnostics。"""

    rows = connection.execute(
        """
        SELECT state, episode_id, opened_at, cooldown_until,
               half_open_token, half_open_started_at, half_open_lease_expires_at,
               probe_request_id, probe_requested_at, requested_recipe_kind,
               requested_target_id
        FROM facebook_access_circuit_state
        """
    ).fetchall()
    violations: list[DatabaseInvariantViolation] = []
    for row in rows:
        state = str(row["state"])
        has_half_open_owner = bool(
            row["half_open_token"]
            and row["half_open_started_at"]
            and row["half_open_lease_expires_at"]
        )
        if (state == "half_open") != has_half_open_owner:
            violations.append(
                DatabaseInvariantViolation(
                    table="facebook_access_circuit_state",
                    row_id="profile",
                    field="half_open_token",
                    message="half-open state and lease ownership fields must agree",
                )
            )
        elif state == "half_open" and str(row["half_open_lease_expires_at"]) <= str(
            row["half_open_started_at"]
        ):
            violations.append(
                DatabaseInvariantViolation(
                    table="facebook_access_circuit_state",
                    row_id="profile",
                    field="half_open_lease_expires_at",
                    message="half-open lease expiry must follow its start time",
                )
            )
        if state in {"open", "half_open"} and not (
            row["episode_id"] and row["opened_at"] and row["cooldown_until"]
        ):
            violations.append(
                DatabaseInvariantViolation(
                    table="facebook_access_circuit_state",
                    row_id="profile",
                    field="episode_id",
                    message="open/half-open state requires episode and cooldown fields",
                )
            )
        request_fields = (
            row["probe_request_id"],
            row["probe_requested_at"],
            row["requested_recipe_kind"],
        )
        has_request = all(request_fields)
        has_any_request_field = any(request_fields) or row["requested_target_id"] is not None
        if has_any_request_field and not (state == "open" and has_request):
            violations.append(
                DatabaseInvariantViolation(
                    table="facebook_access_circuit_state",
                    row_id="profile",
                    field="probe_request_id",
                    message="pending probe fields require a complete open-state request",
                )
            )
        elif has_request and str(row["probe_requested_at"]) < str(row["cooldown_until"]):
            violations.append(
                DatabaseInvariantViolation(
                    table="facebook_access_circuit_state",
                    row_id="profile",
                    field="probe_requested_at",
                    message="probe request must not precede the circuit cooldown",
                )
            )
    return violations


def _facebook_access_utc_datetime_violations(
    connection: sqlite3.Connection,
) -> list[DatabaseInvariantViolation]:
    """Circuit timestamps 必須帶 UTC offset，避免 lexical CAS 比較不可靠。"""

    contracts = (
        (
            "facebook_access_circuit_state",
            "'profile'",
            (
                "opened_at",
                "last_detected_at",
                "cooldown_until",
                "half_open_started_at",
                "half_open_lease_expires_at",
                "probe_requested_at",
                "last_probe_finished_at",
                "closed_at",
                "updated_at",
            ),
        ),
        ("facebook_access_circuit_events", "id", ("occurred_at",)),
        (
            "facebook_session_recovery_state",
            "'profile'",
            (
                "stale_detected_at",
                "earliest_probe_at",
                "request_requested_at",
                "probe_started_at",
                "probe_lease_expires_at",
                "last_probe_finished_at",
                "recovered_at",
                "updated_at",
            ),
        ),
    )
    violations: list[DatabaseInvariantViolation] = []
    for table, row_id_expr, fields in contracts:
        selected_fields = ", ".join(fields)
        rows = connection.execute(
            f"SELECT {row_id_expr} AS row_id, {selected_fields} FROM {table}"
        ).fetchall()
        for row in rows:
            for field in fields:
                raw_value = str(row[field] or "")
                if not raw_value:
                    continue
                try:
                    parsed = decode_datetime(raw_value)
                except ValueError:
                    continue
                if parsed is not None and parsed.utcoffset() != timedelta(0):
                    violations.append(
                        DatabaseInvariantViolation(
                            table=table,
                            row_id=str(row["row_id"]),
                            field=field,
                            message="datetime value must use UTC offset",
                        )
                    )
    return violations


def _managed_profile_identity_binding_violations(
    connection: sqlite3.Connection,
) -> list[DatabaseInvariantViolation]:
    """檢查 singleton binding 的 UUID 與固定 row identity。"""

    rows = connection.execute(
        "SELECT id, marker_uuid FROM managed_profile_identity_binding"
    ).fetchall()
    violations: list[DatabaseInvariantViolation] = []
    for row in rows:
        row_id = str(row["id"])
        if int(row["id"]) != 1:
            violations.append(
                DatabaseInvariantViolation(
                    table="managed_profile_identity_binding",
                    row_id=row_id,
                    field="id",
                    message="managed profile identity binding must use singleton id 1",
                )
            )
        try:
            UUID(str(row["marker_uuid"]))
        except (TypeError, ValueError):
            violations.append(
                DatabaseInvariantViolation(
                    table="managed_profile_identity_binding",
                    row_id=row_id,
                    field="marker_uuid",
                    message="marker UUID is invalid",
                )
            )
    return violations


def _facebook_session_recovery_state_violations(
    connection: sqlite3.Connection,
) -> list[DatabaseInvariantViolation]:
    """檢查 stale-session recovery 的 request/probe/recovered cross fields。"""

    rows = connection.execute(
        """
        SELECT status, request_id, request_requested_at, requested_target_id,
               requested_operation_kind, requested_recipe_kind, probe_token,
               probe_started_at, probe_lease_expires_at, recovered_at
        FROM facebook_session_recovery_state
        """
    ).fetchall()
    violations: list[DatabaseInvariantViolation] = []
    for row in rows:
        status = str(row["status"])
        has_request = bool(
            row["request_id"]
            and row["request_requested_at"]
            and row["requested_operation_kind"]
            and row["requested_recipe_kind"]
        )
        has_probe = bool(
            row["probe_token"]
            and row["probe_started_at"]
            and row["probe_lease_expires_at"]
        )
        if (status in {"probe_pending", "probing"}) != has_request:
            violations.append(
                DatabaseInvariantViolation(
                    table="facebook_session_recovery_state",
                    row_id="profile",
                    field="request_id",
                    message="pending/probing state and request fields must agree",
                )
            )
        if (status == "probing") != has_probe:
            violations.append(
                DatabaseInvariantViolation(
                    table="facebook_session_recovery_state",
                    row_id="profile",
                    field="probe_token",
                    message="probing state and lease ownership fields must agree",
                )
            )
        elif status == "probing" and str(row["probe_lease_expires_at"]) <= str(
            row["probe_started_at"]
        ):
            violations.append(
                DatabaseInvariantViolation(
                    table="facebook_session_recovery_state",
                    row_id="profile",
                    field="probe_lease_expires_at",
                    message="probe lease expiry must follow its start time",
                )
            )
        if (status == "recovered") != bool(row["recovered_at"]):
            violations.append(
                DatabaseInvariantViolation(
                    table="facebook_session_recovery_state",
                    row_id="profile",
                    field="recovered_at",
                    message="recovered state and recovered_at must agree",
                )
            )
        if status in {"probe_pending", "probing"} and row["requested_target_id"] is None:
            violations.append(
                DatabaseInvariantViolation(
                    table="facebook_session_recovery_state",
                    row_id="profile",
                    field="requested_target_id",
                    message="active request target is unavailable",
                )
            )
    return violations
