"""Scheduler 啟動前安全檢查。

職責：在 resident scheduler 啟動 thread 前，先確認 scheduler 熱路徑需要
讀取的 DB/schema 與 active target runtime 資料可安全 decode。此檢查不是
每輪 tick 的 invariant 掃描，也不嘗試修復無法判斷語義的壞資料。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
from pathlib import Path
import sqlite3

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.refresh_policy import resolve_refresh_interval_seconds
from facebook_monitor.persistence.schema import SCHEMA_VERSION
from facebook_monitor.persistence.schema import read_supported_schema_version
from facebook_monitor.persistence.schema import validate_current_schema_shape
from facebook_monitor.persistence.sqlite_retry import is_sqlite_lock_error


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SchedulerStartPreflightResult:
    """保存 scheduler start preflight 結果。"""

    ok: bool
    message: str = ""

    @classmethod
    def passed(cls) -> SchedulerStartPreflightResult:
        """建立通過結果。"""

        return cls(ok=True)

    @classmethod
    def blocked(cls, message: str) -> SchedulerStartPreflightResult:
        """建立阻止 scheduler start 的結果。"""

        return cls(ok=False, message=message)


def run_scheduler_start_preflight(
    db_path: Path,
    *,
    default_interval_seconds: float,
) -> SchedulerStartPreflightResult:
    """檢查 scheduler 啟動後一定會使用的 DB/schema 與 active target 讀取路徑。"""

    try:
        with SqliteApplicationContext(db_path) as app:
            connection = app.repositories.targets.connection
            _validate_current_schema_if_needed(connection)
            violations = _scheduler_critical_contract_violations(connection)
            if violations:
                return SchedulerStartPreflightResult.blocked(
                    _format_preflight_failure("; ".join(violations[:5]))
                )
            _validate_scheduler_decode_path(
                app,
                default_interval_seconds=default_interval_seconds,
            )
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        if is_sqlite_lock_error(exc):
            logger.warning(
                "scheduler_start_preflight_deferred_database_locked "
                "db_path=%s exception_class=%s detail=%s",
                db_path,
                exc.__class__.__name__,
                detail,
            )
            return SchedulerStartPreflightResult.passed()
        logger.error(
            "scheduler_start_preflight_failed db_path=%s exception_class=%s detail=%s",
            db_path,
            exc.__class__.__name__,
            detail,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return SchedulerStartPreflightResult.blocked(_format_preflight_failure(detail))
    return SchedulerStartPreflightResult.passed()


def _validate_current_schema_if_needed(connection: sqlite3.Connection) -> None:
    """current-version DB 在啟動前仍需確認正式表與欄位存在。"""

    existing_version = read_supported_schema_version(connection)
    if existing_version == SCHEMA_VERSION:
        validate_current_schema_shape(connection)


def _scheduler_critical_contract_violations(
    connection: sqlite3.Connection,
) -> tuple[str, ...]:
    """回傳會讓 scheduler active target 讀取路徑無法安全 decode 的 row 問題。"""

    violations: list[str] = []
    violations.extend(
        _active_target_enum_violations(
            connection,
            field="target_kind",
            allowed_values=_enum_values(TargetKind),
        )
    )
    violations.extend(
        _active_target_enum_violations(
            connection,
            field="metadata_status",
            allowed_values=_enum_values(TargetMetadataStatus),
        )
    )
    violations.extend(
        _active_target_enum_violations(
            connection,
            field="worker_mode",
            allowed_values=_enum_values(WorkerMode),
        )
    )
    violations.extend(
        _active_runtime_enum_violations(
            connection,
            field="desired_state",
            allowed_values=_enum_values(TargetDesiredState),
        )
    )
    violations.extend(
        _active_runtime_enum_violations(
            connection,
            field="runtime_status",
            allowed_values=_enum_values(TargetRuntimeStatus),
        )
    )
    return tuple(violations)


def _active_target_enum_violations(
    connection: sqlite3.Connection,
    *,
    field: str,
    allowed_values: tuple[str, ...],
) -> tuple[str, ...]:
    placeholders = ",".join("?" for _ in allowed_values)
    rows = connection.execute(
        f"""
        SELECT id, {field}
        FROM targets
        WHERE enabled = 1
          AND paused = 0
          AND {field} NOT IN ({placeholders})
        ORDER BY created_at
        """,
        allowed_values,
    ).fetchall()
    return tuple(
        f"targets[{row['id']}].{field}: unexpected value {row[field]!r}"
        for row in rows
    )


def _active_runtime_enum_violations(
    connection: sqlite3.Connection,
    *,
    field: str,
    allowed_values: tuple[str, ...],
) -> tuple[str, ...]:
    placeholders = ",".join("?" for _ in allowed_values)
    rows = connection.execute(
        f"""
        SELECT runtime.target_id, runtime.{field}
        FROM target_runtime_state AS runtime
        JOIN targets AS target ON target.id = runtime.target_id
        WHERE target.enabled = 1
          AND target.paused = 0
          AND runtime.{field} NOT IN ({placeholders})
        ORDER BY target.created_at
        """,
        allowed_values,
    ).fetchall()
    return tuple(
        f"target_runtime_state[{row['target_id']}].{field}: "
        f"unexpected value {row[field]!r}"
        for row in rows
    )


def _validate_scheduler_decode_path(
    app: ApplicationContext,
    *,
    default_interval_seconds: float,
) -> None:
    """沿用 scheduler 會走到的 active target 讀取流程，捕捉 enum 以外的 decode 錯誤。"""

    for target in app.repositories.targets.list_enabled():
        if target.target_kind not in {TargetKind.POSTS, TargetKind.COMMENTS}:
            continue
        runtime_state = app.services.targets.ensure_runtime_state(target.id)
        if runtime_state.desired_state != TargetDesiredState.ACTIVE:
            continue
        if runtime_state.runtime_status == TargetRuntimeStatus.ERROR:
            continue
        config = app.services.targets.get_config_for_target(target)
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        resolve_refresh_interval_seconds(
            config=config,
            default_interval_seconds=default_interval_seconds,
            target_id=target.id,
            latest_finished_at=latest_scan.finished_at if latest_scan else None,
        )


def _enum_values(enum_type: type[StrEnum]) -> tuple[str, ...]:
    """回傳穩定排序的 enum values，供 SQL placeholder 使用。"""

    return tuple(sorted(item.value for item in enum_type))


def _format_preflight_failure(detail: str) -> str:
    """建立 scheduler state 可顯示的 preflight 失敗訊息。"""

    return f"背景掃描啟動前資料檢查失敗：{detail}"
