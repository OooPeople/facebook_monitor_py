"""Web read model 專用的資料 invariant 範圍檢查。

本模組只檢查單次 Web read 實際會映射或彙總的資料列；管理工具與支援包仍使用
``persistence.validate_database_invariants`` 執行全庫 audit。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from datetime import timedelta

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.persistence.invariants import DatabaseInvariantViolation
from facebook_monitor.persistence.schema_contract import BOOLEAN_CONTRACTS
from facebook_monitor.persistence.schema_contract import DATETIME_CONTRACTS
from facebook_monitor.persistence.schema_contract import ENUM_CONTRACTS
from facebook_monitor.persistence.schema_contract import RANGE_CONTRACTS
from facebook_monitor.persistence.sqlite_codec import decode_datetime
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.webapp.read_model_invariants import inactive_runtime_invariant_row_ids
from facebook_monitor.webapp.read_model_invariants import inactive_target_invariant_row_ids


_SQLITE_IN_CLAUSE_CHUNK_SIZE = 400


def validate_dashboard_read_scope(
    connection: sqlite3.Connection,
    *,
    session_started_at: datetime | None,
) -> tuple[DatabaseInvariantViolation, ...]:
    """只驗證本次完整 dashboard 會讀取的 rows。"""

    target_ids = _select_ids(connection, "SELECT id AS row_id FROM targets")
    target_rows = {"targets": target_ids}
    target_violations = list(_validate_selected_rows(connection, target_rows))
    target_violations.extend(_duplicate_target_scope_violations(connection))
    skipped_target_ids = inactive_target_invariant_row_ids(
        connection,
        violations=tuple(target_violations),
    )
    candidate_target_ids = target_ids - skipped_target_ids

    runtime_ids = _existing_ids(
        connection,
        table="target_runtime_state",
        row_id_column="target_id",
        values=candidate_target_ids,
    )
    runtime_rows = {"target_runtime_state": runtime_ids}
    runtime_violations = _validate_selected_rows(connection, runtime_rows)
    skipped_runtime_ids = inactive_runtime_invariant_row_ids(
        connection,
        violations=runtime_violations,
    )
    loaded_target_ids = candidate_target_ids - skipped_runtime_ids

    selected_rows = _dashboard_related_row_ids(
        connection,
        target_ids=loaded_target_ids,
        session_started_at=session_started_at,
    )
    return _unique_violations(
        (
            *target_violations,
            *runtime_violations,
            *_validate_selected_rows(connection, selected_rows),
            *_outbox_summary_violations(connection, loaded_target_ids),
        )
    )


def validate_target_card_read_scope(
    connection: sqlite3.Connection,
    target_id: str,
    *,
    session_started_at: datetime | None,
) -> tuple[DatabaseInvariantViolation, ...]:
    """只驗證單張 target card 實際會讀取的 rows。"""

    selected_rows = _target_identity_row_ids(connection, target_id)
    selected_rows.update(
        _target_card_related_row_ids(
            connection,
            target_id=target_id,
            session_started_at=session_started_at,
        )
    )
    return _unique_violations(
        (
            *_validate_selected_rows(connection, selected_rows),
            *_outbox_summary_violations(connection, {target_id}),
        )
    )


def validate_target_identity_scope(
    connection: sqlite3.Connection,
    target_id: str,
) -> tuple[DatabaseInvariantViolation, ...]:
    """只驗證 hit-record route 查存在性時讀取的 target/runtime rows。"""

    return _validate_selected_rows(
        connection,
        _target_identity_row_ids(connection, target_id),
    )


def validate_hit_record_page_scope(
    connection: sqlite3.Connection,
    target_id: str,
    *,
    limit: int,
    offset: int = 0,
    recorded_since: datetime | None = None,
) -> tuple[DatabaseInvariantViolation, ...]:
    """依 repository 相同排序與分頁條件驗證單頁命中紀錄。"""

    history_ids = _select_hit_record_page_ids(
        connection,
        target_id=target_id,
        limit=limit,
        offset=offset,
        recorded_since=recorded_since,
    )
    return _validate_selected_rows(connection, {"match_history": history_ids})


def validate_notification_event_scope(
    connection: sqlite3.Connection,
    target_id: str,
    *,
    item_keys: Iterable[str],
) -> tuple[DatabaseInvariantViolation, ...]:
    """驗證 full hit-record page 實際查詢的成功通知事件 rows。"""

    unique_keys = tuple(dict.fromkeys(key for key in item_keys if key))
    if not unique_keys:
        return ()
    placeholders = ",".join("?" for _ in unique_keys)
    event_ids = _select_ids(
        connection,
        f"""
        SELECT id AS row_id
        FROM notification_events
        WHERE target_id = ?
          AND status = ?
          AND item_key IN ({placeholders})
        """,
        (target_id, NotificationStatus.SENT.value, *unique_keys),
    )
    return _validate_selected_rows(connection, {"notification_events": event_ids})


def _dashboard_related_row_ids(
    connection: sqlite3.Connection,
    *,
    target_ids: set[str],
    session_started_at: datetime | None,
) -> dict[str, set[str]]:
    """收集完整 dashboard 除 target/runtime 外的實際 read scope row ids。"""

    group_ids = _select_ids(connection, "SELECT id AS row_id FROM sidebar_groups")
    selected_rows = {
        "target_configs": _existing_ids(
            connection,
            table="target_configs",
            row_id_column="target_id",
            values=target_ids,
        ),
        "sidebar_groups": group_ids,
        "sidebar_target_placements": _select_ids(
            connection,
            "SELECT target_id AS row_id FROM sidebar_target_placements",
        ),
        "sidebar_group_config_templates": _existing_ids(
            connection,
            table="sidebar_group_config_templates",
            row_id_column="sidebar_group_id",
            values=group_ids,
        ),
        "scan_runs": _select_dashboard_scan_run_ids(connection, target_ids),
        "facebook_temporary_block_warning": _select_ids(
            connection,
            "SELECT id AS row_id FROM facebook_temporary_block_warning WHERE id = 1",
        ),
    }
    max_items_limit = _dashboard_max_items_limit(connection, target_ids)
    selected_rows["latest_scan_items"] = _select_dashboard_latest_item_ids(
        connection,
        target_ids=target_ids,
        limit_per_target=max_items_limit,
    )
    selected_rows["match_history"] = _select_dashboard_history_ids(
        connection,
        target_ids=target_ids,
        limit_per_target=5,
        recorded_since=session_started_at,
    )
    return selected_rows


def _target_card_related_row_ids(
    connection: sqlite3.Connection,
    *,
    target_id: str,
    session_started_at: datetime | None,
) -> dict[str, set[str]]:
    """收集單張 target card 除 target/runtime 外的實際 read scope row ids。"""

    max_items_limit = _target_max_items_limit(connection, target_id)
    return {
        "target_configs": _existing_ids(
            connection,
            table="target_configs",
            row_id_column="target_id",
            values={target_id},
        ),
        "scan_runs": _select_target_scan_run_ids(connection, target_id),
        "latest_scan_items": _select_ids(
            connection,
            """
            SELECT target_id || ':' || item_key AS row_id
            FROM latest_scan_items
            WHERE target_id = ?
            ORDER BY item_index
            LIMIT ?
            """,
            (target_id, max_items_limit),
        ),
        "match_history": _select_hit_record_page_ids(
            connection,
            target_id=target_id,
            limit=5,
            offset=0,
            recorded_since=session_started_at,
        ),
    }


def _target_identity_row_ids(
    connection: sqlite3.Connection,
    target_id: str,
) -> dict[str, set[str]]:
    """收集單一 target existence check 會讀取的 target/runtime ids。"""

    return {
        "targets": _existing_ids(
            connection,
            table="targets",
            row_id_column="id",
            values={target_id},
        ),
        "target_runtime_state": _existing_ids(
            connection,
            table="target_runtime_state",
            row_id_column="target_id",
            values={target_id},
        ),
    }


def _select_hit_record_page_ids(
    connection: sqlite3.Connection,
    *,
    target_id: str,
    limit: int,
    offset: int,
    recorded_since: datetime | None,
) -> set[str]:
    """以 MatchHistoryRepository.list_by_target 相同 SQL 邊界選出 page ids。"""

    bounded_limit = max(int(limit), 1)
    bounded_offset = max(int(offset), 0)
    recorded_since_filter = ""
    params: list[object] = [target_id]
    if recorded_since is not None:
        recorded_since_filter = "AND match_history.recorded_at >= ?"
        params.append(encode_datetime(recorded_since))
    params.extend([bounded_limit, bounded_offset])
    return _select_ids(
        connection,
        f"""
        SELECT match_history.id AS row_id
        FROM match_history
        LEFT JOIN latest_scan_items
          ON latest_scan_items.target_id = match_history.target_id
         AND latest_scan_items.item_key = match_history.item_key
        WHERE match_history.target_id = ?
          {recorded_since_filter}
        ORDER BY
            CASE WHEN latest_scan_items.item_index IS NULL THEN 1 ELSE 0 END,
            latest_scan_items.item_index ASC,
            match_history.recorded_at DESC,
            match_history.id DESC
        LIMIT ?
        OFFSET ?
        """,
        tuple(params),
    )


def _select_dashboard_history_ids(
    connection: sqlite3.Connection,
    *,
    target_ids: set[str],
    limit_per_target: int,
    recorded_since: datetime | None,
) -> set[str]:
    """以 dashboard 批次 repository 相同 window query 選出 preview ids。"""

    if not target_ids:
        return set()
    ordered_ids = tuple(sorted(target_ids))
    placeholders = ",".join("?" for _ in ordered_ids)
    recorded_since_filter = ""
    params: list[object] = [*ordered_ids]
    if recorded_since is not None:
        recorded_since_filter = "AND match_history.recorded_at >= ?"
        params.append(encode_datetime(recorded_since))
    params.append(max(int(limit_per_target), 1))
    return _select_ids(
        connection,
        f"""
        SELECT id AS row_id
        FROM (
            SELECT match_history.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY match_history.target_id
                       ORDER BY
                           CASE WHEN latest_scan_items.item_index IS NULL THEN 1 ELSE 0 END,
                           latest_scan_items.item_index ASC,
                           match_history.recorded_at DESC,
                           match_history.id DESC
                   ) AS row_number
            FROM match_history
            LEFT JOIN latest_scan_items
              ON latest_scan_items.target_id = match_history.target_id
             AND latest_scan_items.item_key = match_history.item_key
            WHERE match_history.target_id IN ({placeholders})
              {recorded_since_filter}
        )
        WHERE row_number <= ?
        """,
        tuple(params),
    )


def _select_dashboard_latest_item_ids(
    connection: sqlite3.Connection,
    *,
    target_ids: set[str],
    limit_per_target: int,
) -> set[str]:
    """選出 dashboard 批次查詢實際會映射的 latest item ids。"""

    if not target_ids:
        return set()
    ordered_ids = tuple(sorted(target_ids))
    placeholders = ",".join("?" for _ in ordered_ids)
    return _select_ids(
        connection,
        f"""
        SELECT target_id || ':' || item_key AS row_id
        FROM (
            SELECT target_id,
                   item_key,
                   ROW_NUMBER() OVER (
                       PARTITION BY target_id
                       ORDER BY item_index
                   ) AS row_number
            FROM latest_scan_items
            WHERE target_id IN ({placeholders})
        )
        WHERE row_number <= ?
        """,
        (*ordered_ids, max(int(limit_per_target), 1)),
    )


def _select_dashboard_scan_run_ids(
    connection: sqlite3.Connection,
    target_ids: set[str],
) -> set[str]:
    """選出每個 dashboard target 最新 overall 與 latest failed scan ids。"""

    if not target_ids:
        return set()
    ordered_ids = tuple(sorted(target_ids))
    placeholders = ",".join("?" for _ in ordered_ids)
    latest_ids = _select_ids(
        connection,
        f"""
        SELECT id AS row_id
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (PARTITION BY target_id ORDER BY id DESC) AS row_number
            FROM scan_runs
            WHERE target_id IN ({placeholders})
        )
        WHERE row_number = 1
        """,
        ordered_ids,
    )
    failed_ids = _select_ids(
        connection,
        f"""
        SELECT id AS row_id
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (PARTITION BY target_id ORDER BY id DESC) AS row_number
            FROM scan_runs
            WHERE target_id IN ({placeholders})
              AND status = ?
        )
        WHERE row_number = 1
        """,
        (*ordered_ids, ScanStatus.FAILED.value),
    )
    return latest_ids | failed_ids


def _select_target_scan_run_ids(
    connection: sqlite3.Connection,
    target_id: str,
) -> set[str]:
    """選出單張 card 最新 overall 與 latest failed scan ids。"""

    latest_ids = _select_ids(
        connection,
        """
        SELECT id AS row_id
        FROM scan_runs
        WHERE target_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (target_id,),
    )
    failed_ids = _select_ids(
        connection,
        """
        SELECT id AS row_id
        FROM scan_runs
        WHERE target_id = ? AND status = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (target_id, ScanStatus.FAILED.value),
    )
    return latest_ids | failed_ids


def _dashboard_max_items_limit(
    connection: sqlite3.Connection,
    target_ids: set[str],
) -> int:
    """回傳 dashboard 批次 latest-items repository 實際使用的上限。"""

    if not target_ids:
        return 1
    ordered_ids = tuple(sorted(target_ids))
    placeholders = ",".join("?" for _ in ordered_ids)
    rows = connection.execute(
        f"""
        SELECT max_items_per_scan
        FROM target_configs
        WHERE target_id IN ({placeholders})
        """,
        ordered_ids,
    ).fetchall()
    configured = [int(row["max_items_per_scan"]) for row in rows]
    if len(rows) < len(target_ids):
        configured.append(PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan)
    return max([1, *configured])


def _target_max_items_limit(connection: sqlite3.Connection, target_id: str) -> int:
    """回傳單張 card latest-items repository 實際使用的上限。"""

    row = connection.execute(
        "SELECT max_items_per_scan FROM target_configs WHERE target_id = ?",
        (target_id,),
    ).fetchone()
    value = (
        int(row["max_items_per_scan"])
        if row is not None
        else PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan
    )
    return value


def _outbox_summary_violations(
    connection: sqlite3.Connection,
    target_ids: set[str],
) -> tuple[DatabaseInvariantViolation, ...]:
    """只驗證 summarize_by_targets 實際聚合與 decode 的 outbox 欄位。"""

    if not target_ids:
        return ()
    ordered_ids = tuple(sorted(target_ids))
    target_placeholders = ",".join("?" for _ in ordered_ids)
    allowed_statuses = tuple(status.value for status in NotificationOutboxStatus)
    status_placeholders = ",".join("?" for _ in allowed_statuses)
    status_rows = connection.execute(
        f"""
        SELECT id, status
        FROM notification_outbox
        WHERE target_id IN ({target_placeholders})
          AND status NOT IN ({status_placeholders})
        """,
        (*ordered_ids, *allowed_statuses),
    ).fetchall()
    violations = [
        DatabaseInvariantViolation(
            table="notification_outbox",
            row_id=str(row["id"]),
            field="status",
            message=f"unexpected enum value {row['status']!r}",
        )
        for row in status_rows
    ]
    aggregate_rows = connection.execute(
        f"""
        SELECT
            target_id,
            MIN(
                CASE WHEN status IN (?, ?, ?) THEN updated_at ELSE NULL END
            ) AS oldest_pending_updated_at,
            MAX(attempts) AS max_attempts
        FROM notification_outbox
        WHERE target_id IN ({target_placeholders})
        GROUP BY target_id
        """,
        (
            NotificationOutboxStatus.PENDING.value,
            NotificationOutboxStatus.PROCESSING_PENDING.value,
            NotificationOutboxStatus.PROCESSING_FAILED.value,
            *ordered_ids,
        ),
    ).fetchall()
    for row in aggregate_rows:
        row_id = str(row["target_id"])
        oldest_value = row["oldest_pending_updated_at"]
        if oldest_value is not None:
            if not oldest_value:
                violations.append(
                    DatabaseInvariantViolation(
                        table="notification_outbox",
                        row_id=row_id,
                        field="updated_at",
                        message="datetime value is required",
                    )
                )
            else:
                try:
                    decode_datetime(str(oldest_value))
                except ValueError:
                    violations.append(
                        DatabaseInvariantViolation(
                            table="notification_outbox",
                            row_id=row_id,
                            field="updated_at",
                            message=f"invalid datetime value {oldest_value!r}",
                        )
                    )
        max_attempts = row["max_attempts"]
        if max_attempts is not None and int(max_attempts) < 0:
            violations.append(
                DatabaseInvariantViolation(
                    table="notification_outbox",
                    row_id=row_id,
                    field="attempts",
                    message="value is outside product range",
                )
            )
    return _unique_violations(violations)


def _existing_ids(
    connection: sqlite3.Connection,
    *,
    table: str,
    row_id_column: str,
    values: set[str],
) -> set[str]:
    """從指定安全 table/column 選出實際存在的 contract row ids。"""

    if not values:
        return set()
    ordered_values = tuple(sorted(values))
    placeholders = ",".join("?" for _ in ordered_values)
    return _select_ids(
        connection,
        f"""
        SELECT {row_id_column} AS row_id
        FROM {table}
        WHERE {row_id_column} IN ({placeholders})
        """,
        ordered_values,
    )


def _select_ids(
    connection: sqlite3.Connection,
    sql: str,
    params: tuple[object, ...] = (),
) -> set[str]:
    """執行 bounded selector 並回傳去重後的 row ids。"""

    return {str(row["row_id"]) for row in connection.execute(sql, params).fetchall()}


def _validate_selected_rows(
    connection: sqlite3.Connection,
    selected_rows: dict[str, set[str]],
) -> tuple[DatabaseInvariantViolation, ...]:
    """只對 caller 已選出的 row ids 套用既有 schema contracts。"""

    violations: list[DatabaseInvariantViolation] = []
    violations.extend(_enum_violations(connection, selected_rows))
    violations.extend(_boolean_violations(connection, selected_rows))
    violations.extend(_range_violations(connection, selected_rows))
    violations.extend(_datetime_violations(connection, selected_rows))
    violations.extend(_runtime_state_violations(connection, selected_rows))
    violations.extend(_temporary_block_warning_utc_violations(connection, selected_rows))
    return _unique_violations(violations)


def _enum_violations(
    connection: sqlite3.Connection,
    selected_rows: dict[str, set[str]],
) -> list[DatabaseInvariantViolation]:
    """檢查 scope 內 enum contracts。"""

    violations: list[DatabaseInvariantViolation] = []
    for contract in ENUM_CONTRACTS:
        allowed = tuple(sorted(contract.allowed_values))
        allowed_placeholders = ",".join("?" for _ in allowed)
        for chunk in _selected_chunks(selected_rows, contract.table):
            row_placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT {contract.row_id_expr} AS row_id, {contract.field}
                FROM {contract.table}
                WHERE ({contract.row_id_expr}) IN ({row_placeholders})
                  AND {contract.field} NOT IN ({allowed_placeholders})
                """,
                (*chunk, *allowed),
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


def _boolean_violations(
    connection: sqlite3.Connection,
    selected_rows: dict[str, set[str]],
) -> list[DatabaseInvariantViolation]:
    """檢查 scope 內 boolean contracts。"""

    violations: list[DatabaseInvariantViolation] = []
    for contract in BOOLEAN_CONTRACTS:
        for chunk in _selected_chunks(selected_rows, contract.table):
            placeholders = ",".join("?" for _ in chunk)
            for field in contract.fields:
                rows = connection.execute(
                    f"""
                    SELECT {contract.row_id_column} AS row_id, {field}
                    FROM {contract.table}
                    WHERE {contract.row_id_column} IN ({placeholders})
                      AND {field} NOT IN (0, 1)
                    """,
                    chunk,
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


def _range_violations(
    connection: sqlite3.Connection,
    selected_rows: dict[str, set[str]],
) -> list[DatabaseInvariantViolation]:
    """檢查 scope 內 range contracts。"""

    violations: list[DatabaseInvariantViolation] = []
    for contract in RANGE_CONTRACTS:
        for chunk in _selected_chunks(selected_rows, contract.table):
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT {contract.row_id_column} AS row_id
                FROM {contract.table}
                WHERE {contract.row_id_column} IN ({placeholders})
                  AND ({contract.where_clause})
                """,
                (*chunk, *contract.params),
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


def _datetime_violations(
    connection: sqlite3.Connection,
    selected_rows: dict[str, set[str]],
) -> list[DatabaseInvariantViolation]:
    """檢查 scope 內 datetime contracts。"""

    violations: list[DatabaseInvariantViolation] = []
    for contract in DATETIME_CONTRACTS:
        for chunk in _selected_chunks(selected_rows, contract.table):
            placeholders = ",".join("?" for _ in chunk)
            fields = ", ".join(contract.fields)
            rows = connection.execute(
                f"""
                SELECT {contract.row_id_column} AS row_id, {fields}
                FROM {contract.table}
                WHERE {contract.row_id_column} IN ({placeholders})
                """,
                chunk,
            ).fetchall()
            required_fields = set(contract.required_fields)
            for row in rows:
                for field in contract.fields:
                    value = row[field]
                    if not value:
                        if field in required_fields:
                            violations.append(
                                DatabaseInvariantViolation(
                                    table=contract.table,
                                    row_id=str(row["row_id"]),
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
                                row_id=str(row["row_id"]),
                                field=field,
                                message=f"invalid datetime value {value!r}",
                            )
                        )
    return violations


def _runtime_state_violations(
    connection: sqlite3.Connection,
    selected_rows: dict[str, set[str]],
) -> list[DatabaseInvariantViolation]:
    """檢查 scope 內 runtime ownership relational invariants。"""

    violations: list[DatabaseInvariantViolation] = []
    for chunk in _selected_chunks(selected_rows, "target_runtime_state"):
        placeholders = ",".join("?" for _ in chunk)
        running_rows = connection.execute(
            f"""
            SELECT target_id
            FROM target_runtime_state
            WHERE target_id IN ({placeholders})
              AND runtime_status = ?
              AND (active_worker_id = '' OR last_started_at = '' OR last_heartbeat_at = '')
            """,
            (*chunk, TargetRuntimeStatus.RUNNING.value),
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
        idle_rows = connection.execute(
            f"""
            SELECT target_id
            FROM target_runtime_state
            WHERE target_id IN ({placeholders})
              AND runtime_status != ?
              AND (active_worker_id != '' OR active_page_id != '')
            """,
            (*chunk, TargetRuntimeStatus.RUNNING.value),
        ).fetchall()
        violations.extend(
            DatabaseInvariantViolation(
                table="target_runtime_state",
                row_id=str(row["target_id"]),
                field="active_worker_id",
                message="non-running state must not keep active worker/page ownership",
            )
            for row in idle_rows
        )
    return violations


def _temporary_block_warning_utc_violations(
    connection: sqlite3.Connection,
    selected_rows: dict[str, set[str]],
) -> list[DatabaseInvariantViolation]:
    """檢查 scope 內 temporary-block warning timestamps 使用 UTC offset。"""

    violations: list[DatabaseInvariantViolation] = []
    for chunk in _selected_chunks(selected_rows, "facebook_temporary_block_warning"):
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            f"""
            SELECT id, detected_at, warning_until, updated_at
            FROM facebook_temporary_block_warning
            WHERE id IN ({placeholders})
            """,
            chunk,
        ).fetchall()
        for row in rows:
            for field in ("detected_at", "warning_until", "updated_at"):
                try:
                    parsed = decode_datetime(str(row[field] or ""))
                except ValueError:
                    continue
                if parsed is not None and parsed.utcoffset() != timedelta(0):
                    violations.append(
                        DatabaseInvariantViolation(
                            table="facebook_temporary_block_warning",
                            row_id=str(row["id"]),
                            field=field,
                            message="datetime value must use UTC offset",
                        )
                    )
    return violations


def _duplicate_target_scope_violations(
    connection: sqlite3.Connection,
) -> list[DatabaseInvariantViolation]:
    """Dashboard 會載入所有 targets，因此只在此檢查完整 duplicate scope。"""

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


def _selected_chunks(
    selected_rows: dict[str, set[str]],
    table: str,
) -> Iterable[tuple[str, ...]]:
    """將單表 row ids 切成安全大小的 deterministic SQL IN chunks。"""

    ordered = tuple(sorted(selected_rows.get(table, set())))
    for index in range(0, len(ordered), _SQLITE_IN_CLAUSE_CHUNK_SIZE):
        yield ordered[index : index + _SQLITE_IN_CLAUSE_CHUNK_SIZE]


def _unique_violations(
    violations: Iterable[DatabaseInvariantViolation],
) -> tuple[DatabaseInvariantViolation, ...]:
    """依原始 contract 順序去除重複 violation。"""

    return tuple(dict.fromkeys(violations))
