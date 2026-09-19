"""Managed profile identity continuity application service。

職責：組合 private marker 與 SQLite durable binding；正式 runtime 只能經由本模組
取得 profile scope，避免 marker 遺失或被替換後靜默切換 circuit/pacing identity。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
from pathlib import Path
import sqlite3

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.automation.profile_identity import ManagedProfileIdentity
from facebook_monitor.automation.profile_identity import (
    load_or_create_managed_profile_identity,
)
from facebook_monitor.automation.profile_identity import read_managed_profile_identity
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.repositories.managed_profile_identity import (
    ManagedProfileIdentityBinding,
)
from facebook_monitor.persistence.repositories.managed_profile_identity import (
    ManagedProfileIdentityRepository,
)
from facebook_monitor.persistence.sqlite_retry import run_sqlite_operation_with_retry


logger = logging.getLogger(__name__)


class ManagedProfileIdentityStatus(StrEnum):
    """不含 marker/path 內容的 continuity 判斷結果。"""

    READY = "ready"
    LEGACY_UNBOUND = "legacy_unbound"
    UNINITIALIZED = "uninitialized"
    MISSING = "missing"
    CORRUPT = "corrupt"
    MISMATCH = "mismatch"
    STORAGE_UNAVAILABLE = "storage_unavailable"


class ManagedProfileIdentityError(RuntimeError):
    """正式 runtime 無法安全取得 durable profile identity。"""

    def __init__(self, status: ManagedProfileIdentityStatus) -> None:
        self.status = status
        super().__init__(f"managed profile identity: {status.value}")


@dataclass(frozen=True)
class ManagedProfileIdentityInspection:
    """提供 Web/support 使用的 bounded read-only continuity 摘要。"""

    status: ManagedProfileIdentityStatus
    identity: ManagedProfileIdentity | None = None
    binding_present: bool = False

    @property
    def storage_critical(self) -> bool:
        """判斷是否必須在任何 Facebook I/O 前 fail closed。"""

        return self.status in {
            ManagedProfileIdentityStatus.MISSING,
            ManagedProfileIdentityStatus.CORRUPT,
            ManagedProfileIdentityStatus.MISMATCH,
            ManagedProfileIdentityStatus.STORAGE_UNAVAILABLE,
        }


def resolve_managed_profile_identity(
    *,
    db_path: Path,
    profiles_root: Path,
    profile_dir: Path,
) -> ManagedProfileIdentity:
    """初始化或驗證 durable binding，成功後才回傳 runtime identity。"""

    return run_sqlite_operation_with_retry(
        lambda: _resolve_managed_profile_identity_once(
            db_path=db_path,
            profiles_root=profiles_root,
            profile_dir=profile_dir,
        ),
        operation_name="managed_profile_identity.resolve",
        logger=logger,
    )


def _resolve_managed_profile_identity_once(
    *,
    db_path: Path,
    profiles_root: Path,
    profile_dir: Path,
) -> ManagedProfileIdentity:
    """執行一次 marker/binding transaction，SQLite lock 由 facade 整體重試。"""

    with SqliteApplicationContext(db_path) as app:
        repository = app.repositories.managed_profile_identity
        try:
            binding = repository.get()
        except (OSError, ValueError) as exc:
            raise ManagedProfileIdentityError(
                ManagedProfileIdentityStatus.STORAGE_UNAVAILABLE
            ) from exc
        identity = _read_marker_identity(
            profiles_root=profiles_root,
            profile_dir=profile_dir,
        )
        if binding is not None:
            return _require_matching_identity(identity, binding)
        evidence_scopes = repository.list_scoped_safety_evidence()
        if identity is None:
            if evidence_scopes:
                raise ManagedProfileIdentityError(ManagedProfileIdentityStatus.MISSING)
            try:
                identity = load_or_create_managed_profile_identity(
                    profiles_root=profiles_root,
                    profile_dir=profile_dir,
                )
            except ValueError as exc:
                raise ManagedProfileIdentityError(
                    ManagedProfileIdentityStatus.CORRUPT
                ) from exc
            except OSError as exc:
                raise ManagedProfileIdentityError(
                    ManagedProfileIdentityStatus.STORAGE_UNAVAILABLE
                ) from exc
        elif evidence_scopes and evidence_scopes != (identity.profile_scope_key,):
            raise ManagedProfileIdentityError(ManagedProfileIdentityStatus.MISMATCH)
        binding = repository.bind_if_absent(identity.marker_uuid, bound_at=utc_now())
        return _require_matching_identity(identity, binding)


def inspect_managed_profile_identity(
    *,
    db_path: Path,
    profiles_root: Path,
    profile_dir: Path,
) -> ManagedProfileIdentityInspection:
    """唯讀檢查 marker/binding，不建立 profile、marker 或 DB row。"""

    try:
        identity = read_managed_profile_identity(
            profiles_root=profiles_root,
            profile_dir=profile_dir,
        )
    except (UnicodeError, ValueError):
        return ManagedProfileIdentityInspection(ManagedProfileIdentityStatus.CORRUPT)
    except OSError:
        return ManagedProfileIdentityInspection(
            ManagedProfileIdentityStatus.STORAGE_UNAVAILABLE
        )

    binding: ManagedProfileIdentityBinding | None = None
    evidence_scopes: tuple[str, ...] = ()
    if db_path.is_file():
        try:
            binding, evidence_scopes = _read_binding_and_evidence(db_path)
        except (OSError, sqlite3.Error, ValueError):
            return ManagedProfileIdentityInspection(
                ManagedProfileIdentityStatus.STORAGE_UNAVAILABLE
            )
    if binding is None:
        if identity is not None:
            if evidence_scopes and evidence_scopes != (identity.profile_scope_key,):
                return ManagedProfileIdentityInspection(
                    ManagedProfileIdentityStatus.MISMATCH
                )
            return ManagedProfileIdentityInspection(
                ManagedProfileIdentityStatus.LEGACY_UNBOUND,
                identity=identity,
            )
        return ManagedProfileIdentityInspection(
            (
                ManagedProfileIdentityStatus.MISSING
                if evidence_scopes
                else ManagedProfileIdentityStatus.UNINITIALIZED
            )
        )
    if identity is None:
        return ManagedProfileIdentityInspection(
            ManagedProfileIdentityStatus.MISSING,
            binding_present=True,
        )
    if identity.marker_uuid != binding.marker_uuid:
        return ManagedProfileIdentityInspection(
            ManagedProfileIdentityStatus.MISMATCH,
            binding_present=True,
        )
    return ManagedProfileIdentityInspection(
        ManagedProfileIdentityStatus.READY,
        identity=identity,
        binding_present=True,
    )


def _read_marker_identity(
    *,
    profiles_root: Path,
    profile_dir: Path,
) -> ManagedProfileIdentity | None:
    """將 strict marker 錯誤轉成 runtime 可分類的 bounded error。"""

    try:
        return read_managed_profile_identity(
            profiles_root=profiles_root,
            profile_dir=profile_dir,
        )
    except (UnicodeError, ValueError) as exc:
        raise ManagedProfileIdentityError(ManagedProfileIdentityStatus.CORRUPT) from exc
    except OSError as exc:
        raise ManagedProfileIdentityError(
            ManagedProfileIdentityStatus.STORAGE_UNAVAILABLE
        ) from exc


def _require_matching_identity(
    identity: ManagedProfileIdentity | None,
    binding: ManagedProfileIdentityBinding,
) -> ManagedProfileIdentity:
    """要求 marker 必須存在且符合 durable binding。"""

    if identity is None:
        raise ManagedProfileIdentityError(ManagedProfileIdentityStatus.MISSING)
    if identity.marker_uuid != binding.marker_uuid:
        raise ManagedProfileIdentityError(ManagedProfileIdentityStatus.MISMATCH)
    return identity


def _read_binding_and_evidence(
    db_path: Path,
) -> tuple[ManagedProfileIdentityBinding | None, tuple[str, ...]]:
    """從唯讀 DB 讀取 binding 與 legacy scope evidence。"""

    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 500")
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        binding = None
        if "managed_profile_identity_binding" in tables:
            binding = ManagedProfileIdentityRepository(connection).get()
        evidence_tables = {
            "facebook_access_circuit_state",
            "facebook_automation_pacing_state",
            "facebook_session_recovery_state",
        }
        evidence_queries = [
            f"SELECT profile_scope_key FROM {table_name}"
            for table_name in sorted(evidence_tables & tables)
        ]
        evidence_scopes: tuple[str, ...] = ()
        if evidence_queries:
            rows = connection.execute(
                " UNION ".join(evidence_queries) + " ORDER BY profile_scope_key"
            ).fetchall()
            evidence_scopes = tuple(str(row["profile_scope_key"]) for row in rows)
        return binding, evidence_scopes
    finally:
        connection.close()


__all__ = [
    "ManagedProfileIdentityError",
    "ManagedProfileIdentityInspection",
    "ManagedProfileIdentityStatus",
    "inspect_managed_profile_identity",
    "resolve_managed_profile_identity",
]
