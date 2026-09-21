"""SQLite migration chain。

職責：保存目前仍支援的明確版本鏈。既有 DB 欄位補齊必須進本模組的
版本鏈，不得另建 current-schema repair 平行路徑。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from datetime import timedelta
import sqlite3


Migration = Callable[[sqlite3.Connection], None]

_WARNING_SOURCE_KINDS = frozenset({"cover", "metadata", "scan", "sync_resolver"})
_WARNING_SOURCE_SQL = ", ".join(f"'{value}'" for value in sorted(_WARNING_SOURCE_KINDS))
_WARNING_OPERATION_KINDS = frozenset(
    {
        "posts_access",
        "comments_access",
        "group_metadata_access",
        "cover_metadata_access",
        "unknown",
    }
)
_WARNING_ACTION_KINDS = frozenset(
    {
        "group_feed_document",
        "group_document",
        "direct_document",
        "reload",
        "trusted_click",
        "unknown",
    }
)


TARGETS_V35_TO_V36_COLUMNS = (
    "id",
    "name",
    "target_kind",
    "group_id",
    "group_name",
    "group_cover_image_url",
    "parent_post_id",
    "scope_id",
    "canonical_url",
    "metadata_status",
    "metadata_error",
    "enabled",
    "paused",
    "worker_mode",
    "created_at",
    "updated_at",
)


TARGETS_V35_TO_V36_CREATE_SQL_TEMPLATE = """
CREATE TABLE {table_name} (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('posts', 'comments')),
    group_id TEXT NOT NULL,
    group_name TEXT NOT NULL,
    group_cover_image_url TEXT NOT NULL DEFAULT '',
    parent_post_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    metadata_status TEXT NOT NULL DEFAULT 'resolved'
        CHECK (metadata_status IN ('resolved', 'pending', 'failed')),
    metadata_error TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    paused INTEGER NOT NULL CHECK (paused IN (0, 1)),
    worker_mode TEXT NOT NULL CHECK (worker_mode IN ('headless', 'headed_compat')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


TARGETS_V36_ENUM_CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("target_kind", ("posts", "comments")),
    ("metadata_status", ("resolved", "pending", "failed")),
    ("worker_mode", ("headless", "headed_compat")),
)


TARGETS_V36_BOOLEAN_CHECKS = ("enabled", "paused")


def migrate_35_to_36(connection: sqlite3.Connection) -> None:
    """重建 targets table，導入核心 enum / boolean CHECK constraints。"""

    rebuild_targets_table_with_check_constraints(connection)


def migrate_36_to_37(connection: sqlite3.Connection) -> None:
    """移除不再是正式設定來源的舊全域通知設定表。"""

    connection.execute("DROP TABLE IF EXISTS global_notification_settings")


def migrate_37_to_38(connection: sqlite3.Connection) -> None:
    """將 match_history 記錄時間欄位改為符合產品語義的 recorded_at。"""

    if not table_exists(connection, "match_history"):
        return
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(match_history)").fetchall()
    }
    if "recorded_at" in columns:
        return
    if "notified_at" not in columns:
        return
    connection.execute("ALTER TABLE match_history RENAME COLUMN notified_at TO recorded_at")


def migrate_38_to_39(connection: sqlite3.Connection) -> None:
    """為 notification outbox processing claim 加入 runtime lease token。"""

    if not table_exists(connection, "notification_outbox"):
        return
    columns = {
        str(row["name"])
        for row in connection.execute(
            "PRAGMA table_info(notification_outbox)"
        ).fetchall()
    }
    if "processing_token" in columns:
        return
    connection.execute(
        """
        ALTER TABLE notification_outbox
        ADD COLUMN processing_token TEXT NOT NULL DEFAULT ''
        """
    )


def migrate_39_to_40(connection: sqlite3.Connection) -> None:
    """新增 profile 級 Facebook access circuit state 與 transition events。"""

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS facebook_access_circuit_state (
            profile_scope_key TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK (state IN ('closed', 'open', 'half_open')),
            episode_id TEXT NOT NULL DEFAULT '',
            generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
            reason_code TEXT NOT NULL DEFAULT '',
            source_kind TEXT NOT NULL DEFAULT ''
                CHECK (source_kind IN ('', 'scan', 'metadata', 'cover', 'sync_resolver', 'probe')),
            operation_kind TEXT NOT NULL DEFAULT ''
                CHECK (operation_kind IN ('', 'posts_access', 'comments_access', 'group_metadata_access', 'cover_metadata_access', 'unknown')),
            trigger_action_kind TEXT NOT NULL DEFAULT ''
                CHECK (trigger_action_kind IN ('', 'group_feed_document', 'group_document', 'direct_document', 'reload', 'trusted_click', 'unknown')),
            recovery_recipe_kind TEXT NOT NULL DEFAULT ''
                CHECK (recovery_recipe_kind IN ('', 'group_feed_document_guard_v1', 'comments_group_trusted_click_v1', 'group_document_guard_v1', 'group_cover_guard_v1')),
            trigger_target_id TEXT REFERENCES targets(id) ON DELETE SET NULL,
            opened_at TEXT NOT NULL DEFAULT '',
            last_detected_at TEXT NOT NULL DEFAULT '',
            cooldown_until TEXT NOT NULL DEFAULT '',
            detection_count INTEGER NOT NULL DEFAULT 0 CHECK (detection_count >= 0),
            reopen_count INTEGER NOT NULL DEFAULT 0 CHECK (reopen_count >= 0),
            half_open_token TEXT NOT NULL DEFAULT '',
            half_open_started_at TEXT NOT NULL DEFAULT '',
            half_open_lease_expires_at TEXT NOT NULL DEFAULT '',
            probe_request_id TEXT NOT NULL DEFAULT '',
            probe_requested_at TEXT NOT NULL DEFAULT '',
            requested_recipe_kind TEXT NOT NULL DEFAULT ''
                CHECK (requested_recipe_kind IN ('', 'group_feed_document_guard_v1', 'comments_group_trusted_click_v1', 'group_document_guard_v1', 'group_cover_guard_v1')),
            requested_target_id TEXT REFERENCES targets(id) ON DELETE SET NULL,
            last_probe_finished_at TEXT NOT NULL DEFAULT '',
            last_probe_result TEXT NOT NULL DEFAULT ''
                CHECK (last_probe_result IN ('', 'success', 'blocked', 'inconclusive', 'cancelled')),
            closed_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            CHECK (
                (state = 'half_open' AND half_open_token <> ''
                    AND half_open_started_at <> '' AND half_open_lease_expires_at <> ''
                    AND half_open_lease_expires_at > half_open_started_at)
                OR
                (state <> 'half_open' AND half_open_token = ''
                    AND half_open_started_at = '' AND half_open_lease_expires_at = '')
            ),
            CHECK (
                state = 'closed'
                OR (episode_id <> '' AND opened_at <> '' AND cooldown_until <> '')
            ),
            CHECK (
                (probe_request_id = '' AND probe_requested_at = ''
                    AND requested_recipe_kind = '' AND requested_target_id IS NULL)
                OR
                (state = 'open' AND probe_request_id <> ''
                    AND probe_requested_at <> '' AND probe_requested_at >= cooldown_until
                    AND requested_recipe_kind <> '')
            )
        );

        CREATE TABLE IF NOT EXISTS facebook_access_circuit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_scope_key TEXT NOT NULL
                REFERENCES facebook_access_circuit_state(profile_scope_key) ON DELETE CASCADE,
            episode_id TEXT NOT NULL,
            event_kind TEXT NOT NULL CHECK (event_kind IN (
                'opened', 'repeated_detection', 'probe_requested', 'half_open_acquired',
                'probe_succeeded', 'probe_blocked', 'probe_inconclusive',
                'probe_cancelled', 'lease_recovered', 'closed'
            )),
            from_state TEXT NOT NULL CHECK (from_state IN ('closed', 'open', 'half_open')),
            to_state TEXT NOT NULL CHECK (to_state IN ('closed', 'open', 'half_open')),
            reason_code TEXT NOT NULL DEFAULT '',
            source_kind TEXT NOT NULL DEFAULT ''
                CHECK (source_kind IN ('', 'scan', 'metadata', 'cover', 'sync_resolver', 'probe')),
            operation_kind TEXT NOT NULL DEFAULT ''
                CHECK (operation_kind IN ('', 'posts_access', 'comments_access', 'group_metadata_access', 'cover_metadata_access', 'unknown')),
            trigger_action_kind TEXT NOT NULL DEFAULT ''
                CHECK (trigger_action_kind IN ('', 'group_feed_document', 'group_document', 'direct_document', 'reload', 'trusted_click', 'unknown')),
            recovery_recipe_kind TEXT NOT NULL DEFAULT ''
                CHECK (recovery_recipe_kind IN ('', 'group_feed_document_guard_v1', 'comments_group_trusted_click_v1', 'group_document_guard_v1', 'group_cover_guard_v1')),
            target_id TEXT REFERENCES targets(id) ON DELETE SET NULL,
            policy_delay_seconds INTEGER NOT NULL DEFAULT 0 CHECK (policy_delay_seconds >= 0),
            occurred_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_facebook_access_events_profile_occurred
            ON facebook_access_circuit_events(profile_scope_key, occurred_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_facebook_access_events_episode
            ON facebook_access_circuit_events(episode_id, id);
        """
    )


def migrate_40_to_41(connection: sqlite3.Connection) -> None:
    """新增獨立的 profile-wide Facebook automation pacing state。"""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS facebook_automation_pacing_state (
            profile_scope_key TEXT PRIMARY KEY,
            lease_generation INTEGER NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
            active_operation_id TEXT NOT NULL DEFAULT '',
            active_work_kind TEXT NOT NULL DEFAULT '',
            owner_session_id TEXT NOT NULL DEFAULT '',
            active_lease_expires_at TEXT NOT NULL DEFAULT '',
            last_automation_started_at TEXT NOT NULL DEFAULT '',
            last_automation_finished_at TEXT NOT NULL DEFAULT '',
            next_automation_not_before TEXT NOT NULL DEFAULT '',
            last_outcome TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            CHECK (
                (active_operation_id = '' AND active_work_kind = ''
                    AND owner_session_id = '' AND active_lease_expires_at = '')
                OR
                (active_operation_id <> '' AND active_work_kind <> ''
                    AND owner_session_id <> '' AND active_lease_expires_at <> '')
            )
        )
        """
    )


def migrate_41_to_42(connection: sqlite3.Connection) -> None:
    """新增 managed profile identity 的 durable singleton binding。"""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS managed_profile_identity_binding (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            marker_uuid TEXT NOT NULL CHECK (length(marker_uuid) = 36),
            bound_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def migrate_42_to_43(connection: sqlite3.Connection) -> None:
    """新增獨立 stale normal-session durable recovery state。"""

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS facebook_session_recovery_state (
            profile_scope_key TEXT PRIMARY KEY,
            generation INTEGER NOT NULL DEFAULT 1 CHECK (generation >= 1),
            status TEXT NOT NULL CHECK (
                status IN ('hold', 'probe_pending', 'probing', 'recovered')
            ),
            marker_session_id TEXT NOT NULL CHECK (marker_session_id <> ''),
            stale_detected_at TEXT NOT NULL,
            earliest_probe_at TEXT NOT NULL,
            request_id TEXT NOT NULL DEFAULT '',
            request_requested_at TEXT NOT NULL DEFAULT '',
            requested_target_id TEXT REFERENCES targets(id) ON DELETE SET NULL,
            requested_operation_kind TEXT NOT NULL DEFAULT '' CHECK (
                requested_operation_kind IN (
                    '', 'posts_access', 'comments_access', 'group_metadata_access',
                    'cover_metadata_access', 'unknown'
                )
            ),
            requested_recipe_kind TEXT NOT NULL DEFAULT '' CHECK (
                requested_recipe_kind IN (
                    '', 'group_feed_document_guard_v1',
                    'comments_group_trusted_click_v1', 'group_document_guard_v1',
                    'group_cover_guard_v1'
                )
            ),
            probe_token TEXT NOT NULL DEFAULT '',
            probe_started_at TEXT NOT NULL DEFAULT '',
            probe_lease_expires_at TEXT NOT NULL DEFAULT '',
            last_probe_finished_at TEXT NOT NULL DEFAULT '',
            last_probe_result TEXT NOT NULL DEFAULT '' CHECK (
                last_probe_result IN (
                    '', 'success', 'blocked', 'inconclusive', 'cancelled'
                )
            ),
            recovered_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            CHECK (
                (status = 'hold'
                    AND request_id = '' AND request_requested_at = ''
                    AND requested_target_id IS NULL
                    AND requested_operation_kind = '' AND requested_recipe_kind = ''
                    AND probe_token = '' AND probe_started_at = ''
                    AND probe_lease_expires_at = '' AND recovered_at = '')
                OR
                (status = 'probe_pending'
                    AND request_id <> '' AND request_requested_at <> ''
                    AND requested_operation_kind <> '' AND requested_recipe_kind <> ''
                    AND probe_token = '' AND probe_started_at = ''
                    AND probe_lease_expires_at = '' AND recovered_at = '')
                OR
                (status = 'probing'
                    AND request_id <> '' AND request_requested_at <> ''
                    AND requested_operation_kind <> '' AND requested_recipe_kind <> ''
                    AND probe_token <> '' AND probe_started_at <> ''
                    AND probe_lease_expires_at > probe_started_at
                    AND recovered_at = '')
                OR
                (status = 'recovered'
                    AND request_id = '' AND request_requested_at = ''
                    AND requested_target_id IS NULL
                    AND requested_operation_kind = '' AND requested_recipe_kind = ''
                    AND probe_token = '' AND probe_started_at = ''
                    AND probe_lease_expires_at = '' AND recovered_at <> '')
            )
        );
        CREATE INDEX IF NOT EXISTS idx_facebook_session_recovery_status_lease
            ON facebook_session_recovery_state(status, probe_lease_expires_at);
        """
    )


def migrate_43_to_44(connection: sqlite3.Connection) -> None:
    """把舊 temporary-block 強制鎖轉為可逐項確認的風險警告。"""

    migrated_at = connection.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
    ).fetchone()[0]
    active_warning = connection.execute(
        """
        SELECT 1
        FROM facebook_access_circuit_state
        WHERE reason_code = 'facebook_temporary_block'
          AND state IN ('open', 'half_open')
        LIMIT 1
        """
    ).fetchone()
    if active_warning is None:
        return
    connection.execute(
        """
        UPDATE target_runtime_state
        SET desired_state = 'stopped',
            runtime_status = 'idle',
            scan_requested_at = '',
            last_enqueued_at = '',
            last_started_at = '',
            last_finished_at = '',
            last_heartbeat_at = '',
            last_error = '',
            last_skip_reason = '',
            enqueue_reason = '',
            active_worker_id = '',
            active_page_id = '',
            display_next_due_at = '',
            consecutive_failure_reason = '',
            consecutive_failure_count = 0,
            consecutive_scan_skip_reason = '',
            consecutive_scan_skip_count = 0,
            updated_at = ?
        WHERE target_id IN (
            SELECT id FROM targets WHERE enabled = 1 AND paused = 0
        )
        """,
        (migrated_at,),
    )
    connection.execute(
        """
        UPDATE targets
        SET paused = 1,
            updated_at = ?
        WHERE enabled = 1 AND paused = 0
        """,
        (migrated_at,),
    )
    connection.execute(
        """
        INSERT INTO facebook_access_circuit_events (
            profile_scope_key, episode_id, event_kind, from_state, to_state,
            reason_code, source_kind, operation_kind, trigger_action_kind,
            recovery_recipe_kind, target_id, policy_delay_seconds, occurred_at
        )
        SELECT profile_scope_key, episode_id, 'closed', state, 'closed',
               reason_code, source_kind, operation_kind, trigger_action_kind,
               '', trigger_target_id, 0, ?
        FROM facebook_access_circuit_state
        WHERE reason_code = 'facebook_temporary_block'
          AND state IN ('open', 'half_open')
        """,
        (migrated_at,),
    )
    connection.execute(
        """
        UPDATE facebook_access_circuit_state
        SET state = 'closed',
            generation = generation + 1,
            recovery_recipe_kind = '',
            cooldown_until = strftime(
                '%Y-%m-%dT%H:%M:%fZ',
                CASE
                    WHEN last_detected_at <> '' THEN last_detected_at
                    ELSE opened_at
                END,
                '+12 hours'
            ),
            reopen_count = 0,
            half_open_token = '',
            half_open_started_at = '',
            half_open_lease_expires_at = '',
            probe_request_id = '',
            probe_requested_at = '',
            requested_recipe_kind = '',
            requested_target_id = NULL,
            last_probe_finished_at = '',
            last_probe_result = '',
            closed_at = ?,
            updated_at = ?
        WHERE reason_code = 'facebook_temporary_block'
          AND state IN ('open', 'half_open')
        """,
        (migrated_at, migrated_at),
    )


def migrate_44_to_45(connection: sqlite3.Connection) -> None:
    """建立最小 warning truth，僅匯入可信 legacy temporary-block evidence。"""

    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS facebook_temporary_block_warning (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            generation INTEGER NOT NULL CHECK (generation >= 1),
            detected_at TEXT NOT NULL,
            warning_until TEXT NOT NULL,
            source_kind TEXT NOT NULL CHECK (
                source_kind IN ({_WARNING_SOURCE_SQL})
            ),
            operation_kind TEXT NOT NULL CHECK (
                operation_kind IN (
                    'posts_access', 'comments_access', 'group_metadata_access',
                    'cover_metadata_access', 'unknown'
                )
            ),
            action_kind TEXT NOT NULL CHECK (
                action_kind IN (
                    'group_feed_document', 'group_document', 'direct_document',
                    'reload', 'trusted_click', 'unknown'
                )
            ),
            updated_at TEXT NOT NULL,
            CHECK (warning_until > detected_at)
        )
        """
    )
    rows = connection.execute(
        """
        SELECT profile_scope_key, generation, last_detected_at, opened_at,
               source_kind, operation_kind, trigger_action_kind
        FROM facebook_access_circuit_state
        WHERE reason_code = 'facebook_temporary_block'
        """
    ).fetchall()
    candidates: list[tuple[datetime, int, str, sqlite3.Row]] = []
    for row in rows:
        if str(row["source_kind"] or "") not in _WARNING_SOURCE_KINDS:
            continue
        detected_at = _trusted_legacy_detection_time(row)
        if detected_at is None:
            continue
        try:
            generation = int(row["generation"])
        except (TypeError, ValueError):
            continue
        if generation < 0:
            continue
        candidates.append(
            (
                detected_at,
                generation,
                str(row["profile_scope_key"]),
                row,
            )
        )
    if not candidates:
        return
    detected_at, _, _, selected = max(
        candidates,
        key=lambda candidate: (candidate[0], candidate[1], candidate[2]),
    )
    generation = max(candidate[1] for candidate in candidates) + 1
    warning_until = detected_at + timedelta(hours=12)
    detected_text = detected_at.isoformat()
    warning_until_text = warning_until.isoformat()
    connection.execute(
        """
        INSERT INTO facebook_temporary_block_warning (
            id, generation, detected_at, warning_until,
            source_kind, operation_kind, action_kind, updated_at
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            generation = excluded.generation,
            detected_at = excluded.detected_at,
            warning_until = excluded.warning_until,
            source_kind = excluded.source_kind,
            operation_kind = excluded.operation_kind,
            action_kind = excluded.action_kind,
            updated_at = excluded.updated_at
        WHERE facebook_temporary_block_warning.generation <= excluded.generation
        """,
        (
            generation,
            detected_text,
            warning_until_text,
            _bounded_legacy_value(
                selected["source_kind"],
                allowed=_WARNING_SOURCE_KINDS,
                fallback="scan",
            ),
            _bounded_legacy_value(
                selected["operation_kind"],
                allowed=_WARNING_OPERATION_KINDS,
                fallback="unknown",
            ),
            _bounded_legacy_value(
                selected["trigger_action_kind"],
                allowed=_WARNING_ACTION_KINDS,
                fallback="unknown",
            ),
            detected_text,
        ),
    )


def _trusted_legacy_detection_time(row: sqlite3.Row) -> datetime | None:
    """解析 legacy detection time；無效或非 UTC evidence 不匯入。"""

    for field in ("last_detected_at", "opened_at"):
        raw_value = str(row[field] or "")
        if not raw_value:
            continue
        try:
            parsed = datetime.fromisoformat(raw_value)
        except ValueError:
            continue
        if parsed.utcoffset() == timedelta(0):
            return parsed
    return None


def _bounded_legacy_value(
    value: object,
    *,
    allowed: frozenset[str],
    fallback: str,
) -> str:
    """只把 legacy bounded enum 投影到新 warning，異常值降為固定 fallback。"""

    normalized = str(value or "")
    return normalized if normalized in allowed else fallback


def migrate_45_to_46(connection: sqlite3.Connection) -> None:
    """移除已退役的 circuit、pacing、identity 與 recovery 相容資料表。"""

    if not connection.in_transaction:
        connection.execute("BEGIN")
    connection.execute("DROP TABLE IF EXISTS facebook_access_circuit_events")
    connection.execute("DROP TABLE IF EXISTS facebook_session_recovery_state")
    connection.execute("DROP TABLE IF EXISTS facebook_automation_pacing_state")
    connection.execute("DROP TABLE IF EXISTS managed_profile_identity_binding")
    connection.execute("DROP TABLE IF EXISTS facebook_access_circuit_state")


MIGRATIONS: dict[int, Migration] = {
    35: migrate_35_to_36,
    36: migrate_36_to_37,
    37: migrate_37_to_38,
    38: migrate_38_to_39,
    39: migrate_39_to_40,
    40: migrate_40_to_41,
    41: migrate_41_to_42,
    42: migrate_42_to_43,
    43: migrate_43_to_44,
    44: migrate_44_to_45,
    45: migrate_45_to_46,
}


def run_known_migrations(
    connection: sqlite3.Connection,
    *,
    from_version: int,
    to_version: int,
) -> None:
    """依版本鏈執行已知 migrations，成功後才更新 schema_metadata。"""

    current_version = from_version
    while current_version < to_version:
        migration = MIGRATIONS.get(current_version)
        if migration is None:
            raise RuntimeError(
                f"Missing SQLite migration {current_version} -> {current_version + 1}"
            )
        migration(connection)
        current_version += 1
        connection.execute(
            """
            INSERT OR REPLACE INTO schema_metadata (key, value)
            VALUES ('version', ?)
            """,
            (str(current_version),),
        )


def rebuild_targets_table_with_check_constraints(connection: sqlite3.Connection) -> None:
    """以 parent-table-safe rebuild 將 targets 核心語義升成 DB CHECK。"""

    if not table_exists(connection, "targets"):
        return
    violations = _targets_v36_check_violations(connection)
    if violations:
        raise RuntimeError(
            "SQLite targets table contains values incompatible with v36 CHECK "
            "constraints: "
            + "; ".join(violations)
        )

    temp_table = "__targets_v36_checked"
    old_foreign_keys_enabled = _foreign_keys_enabled(connection)
    if connection.in_transaction:
        connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    if _foreign_keys_enabled(connection):
        raise RuntimeError("SQLite failed to disable foreign keys for targets rebuild")
    try:
        connection.execute(f"DROP TABLE IF EXISTS {temp_table}")
        connection.execute(
            TARGETS_V35_TO_V36_CREATE_SQL_TEMPLATE.format(table_name=temp_table)
        )
        columns_sql = ", ".join(TARGETS_V35_TO_V36_COLUMNS)
        old_count = _table_row_count(connection, "targets")
        connection.execute(
            f"""
            INSERT INTO {temp_table} ({columns_sql})
            SELECT {columns_sql}
            FROM targets
            """
        )
        new_count = _table_row_count(connection, temp_table)
        if new_count != old_count:
            raise RuntimeError(
                "SQLite targets rebuild copied an unexpected row count: "
                f"old={old_count}, new={new_count}"
            )
        connection.execute("DROP TABLE targets")
        connection.execute(f"ALTER TABLE {temp_table} RENAME TO targets")
        _raise_for_foreign_key_check_failures(connection)
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.execute(
            f"PRAGMA foreign_keys = {'ON' if old_foreign_keys_enabled else 'OFF'}"
        )


def _targets_v36_check_violations(connection: sqlite3.Connection) -> tuple[str, ...]:
    """回傳 targets v36 CHECK preflight 發現的違規欄位摘要。"""

    violations: list[str] = []
    for field, allowed_values in TARGETS_V36_ENUM_CHECKS:
        placeholders = ", ".join("?" for _ in allowed_values)
        rows = connection.execute(
            f"""
            SELECT id
            FROM targets
            WHERE {field} NOT IN ({placeholders})
               OR {field} IS NULL
            ORDER BY id
            LIMIT 5
            """,
            allowed_values,
        ).fetchall()
        if rows:
            violations.append(_target_violation_summary(field, rows))
    for field in TARGETS_V36_BOOLEAN_CHECKS:
        rows = connection.execute(
            f"""
            SELECT id
            FROM targets
            WHERE {field} NOT IN (0, 1)
               OR {field} IS NULL
            ORDER BY id
            LIMIT 5
            """
        ).fetchall()
        if rows:
            violations.append(_target_violation_summary(field, rows))
    return tuple(violations)


def _target_violation_summary(field: str, rows: list[sqlite3.Row]) -> str:
    """將 target CHECK 違規列成短摘要，避免 migration 只回模糊 IntegrityError。"""

    row_ids = ", ".join(str(_row_first_value(row)) for row in rows)
    return f"targets.{field} invalid row id(s): {row_ids}"


def _row_first_value(row: sqlite3.Row) -> object:
    """讀取 sqlite Row / tuple 的第一個欄位值。"""

    try:
        return row[0]
    except (IndexError, TypeError):
        return ""


def _table_row_count(connection: sqlite3.Connection, table_name: str) -> int:
    """回傳 migration table row count。"""

    row = connection.execute(f"SELECT COUNT(1) FROM {table_name}").fetchone()
    return int(row[0] if row is not None else 0)


def _foreign_keys_enabled(connection: sqlite3.Connection) -> bool:
    """回傳目前 SQLite foreign_keys pragma 是否啟用。"""

    row = connection.execute("PRAGMA foreign_keys").fetchone()
    return bool(row[0] if row is not None else 0)


def _raise_for_foreign_key_check_failures(connection: sqlite3.Connection) -> None:
    """確認 parent-table rebuild 沒留下 foreign key violation。"""

    rows = connection.execute("PRAGMA foreign_key_check").fetchmany(5)
    if rows:
        details = ", ".join(
            f"{row[0]} rowid={row[1]} parent={row[2]} fk={row[3]}" for row in rows
        )
        raise RuntimeError(
            f"SQLite foreign_key_check failed after targets rebuild: {details}"
        )


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    """回傳 SQLite table 是否存在。"""

    row = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


__all__ = [
    "MIGRATIONS",
    "Migration",
    "TARGETS_V35_TO_V36_COLUMNS",
    "migrate_35_to_36",
    "migrate_36_to_37",
    "migrate_37_to_38",
    "migrate_38_to_39",
    "migrate_39_to_40",
    "migrate_41_to_42",
    "migrate_42_to_43",
    "migrate_43_to_44",
    "migrate_44_to_45",
    "migrate_45_to_46",
    "rebuild_targets_table_with_check_constraints",
    "run_known_migrations",
    "table_exists",
]
