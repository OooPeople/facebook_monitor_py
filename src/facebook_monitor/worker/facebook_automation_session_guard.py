"""Facebook automation crash/restart durable session sentinel。

職責：在任何Facebook I/O之前建立privacy-safe ``normal_session`` marker，
在block incident持久化前原子切換為``trip_pending``，並於restart時fail closed。
本模組不建立browser，也不把raw path、URL、target或page evidence寫入marker。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from enum import StrEnum
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import tempfile
from threading import Lock
from uuid import UUID
from uuid import uuid4

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.facebook_access import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookSafetyHoldResult
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryReconcileOutcome,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryReconcileResult,
)
from facebook_monitor.core.facebook_session_recovery import FacebookSessionRecoveryStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.sqlite_retry import run_sqlite_operation_with_retry


logger = logging.getLogger(__name__)
_MARKER_VERSION = 1
_MAX_MARKER_BYTES = 4096
_SAFE_PROFILE_ALIAS = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_NORMAL_FIELDS = frozenset(
    {"version", "session_id", "profile_alias", "state", "started_at"}
)
_TRIP_FIELDS = frozenset(
    {
        *_NORMAL_FIELDS,
        "reason_code",
        "operation_kind",
        "trigger_action_kind",
    }
)


class FacebookAutomationSessionGuardState(StrEnum):
    """Durable marker的兩個stable states。"""

    NORMAL_SESSION = "normal_session"
    TRIP_PENDING = "trip_pending"


class FacebookAutomationSessionGuardErrorCode(StrEnum):
    """不含path/marker內容的storage failure分類。"""

    STORAGE_UNAVAILABLE = "storage_unavailable"
    MARKER_ALREADY_PRESENT = "marker_already_present"
    INVALID_MARKER = "invalid_marker"
    OWNER_MISMATCH = "owner_mismatch"
    STATE_MISMATCH = "state_mismatch"


class FacebookAutomationRestartGuardOutcome(StrEnum):
    """Restart reconcile的browser-free結果。"""

    NO_GUARD = "no_guard"
    UNCLEAN_SESSION_HOLD = "unclean_session_hold"
    PERSISTENCE_UNCERTAIN_RECONCILED = "persistence_uncertain_reconciled"
    STORAGE_CRITICAL = "storage_critical"


class FacebookAutomationSessionGuardError(RuntimeError):
    """Sentinel storage/validation失敗；訊息刻意不帶本機路徑與內容。"""

    def __init__(self, code: FacebookAutomationSessionGuardErrorCode) -> None:
        self.code = code
        super().__init__(f"facebook automation session guard: {code.value}")


@dataclass(frozen=True)
class FacebookAutomationSessionMarker:
    """已驗證且不含敏感頁面資訊的session marker。"""

    session_id: str
    profile_alias: str
    state: FacebookAutomationSessionGuardState
    started_at: datetime
    reason_code: str = ""
    operation_kind: FacebookProductOperationKind | None = None
    trigger_action_kind: FacebookActionKind | None = None


@dataclass(frozen=True)
class FacebookAutomationRestartGuardResult:
    """Restart gate結果；只有NO_GUARD允許之後進入Facebook I/O。"""

    outcome: FacebookAutomationRestartGuardOutcome
    browser_io_allowed: bool
    marker_state: FacebookAutomationSessionGuardState | None = None
    safety_hold: FacebookSafetyHoldResult | None = None
    session_recovery: FacebookSessionRecoveryReconcileResult | None = None
    reason_code: str = ""


class FacebookAutomationSessionGuardStore:
    """以private atomic file操作保存單一profile alias的sentinel。"""

    def __init__(self, directory: Path, *, profile_alias: str) -> None:
        normalized_alias = str(profile_alias).strip()
        if not _SAFE_PROFILE_ALIAS.fullmatch(normalized_alias):
            raise ValueError("profile alias must be a privacy-safe stable alias")
        self.directory = directory
        self.profile_alias = normalized_alias

    @property
    def marker_path(self) -> Path:
        """只回傳privacy-safe alias檔名；support bundle不得收集此目錄。"""

        return self.directory / f"{self.profile_alias}.json"

    def start_normal_session(
        self,
        *,
        started_at: datetime | None = None,
        session_id: str | None = None,
    ) -> FacebookAutomationSessionMarker:
        """Atomic create normal marker；任何既有或storage錯誤都fail closed。"""

        marker = FacebookAutomationSessionMarker(
            session_id=_canonical_uuid(session_id or str(uuid4())),
            profile_alias=self.profile_alias,
            state=FacebookAutomationSessionGuardState.NORMAL_SESSION,
            started_at=_require_utc(started_at or utc_now()),
        )
        self._ensure_private_directory()
        self._reject_unsafe_marker_path()
        temp_path = self._write_private_temp(_serialize_marker(marker))
        try:
            try:
                os.link(temp_path, self.marker_path)
            except FileExistsError as exc:
                raise FacebookAutomationSessionGuardError(
                    FacebookAutomationSessionGuardErrorCode.MARKER_ALREADY_PRESENT
                ) from exc
            except OSError as exc:
                raise FacebookAutomationSessionGuardError(
                    FacebookAutomationSessionGuardErrorCode.STORAGE_UNAVAILABLE
                ) from exc
            self._sync_directory()
        finally:
            _unlink_if_present(temp_path)
        return marker

    def mark_trip_pending(
        self,
        *,
        session_id: str,
        operation_kind: FacebookProductOperationKind,
        trigger_action_kind: FacebookActionKind,
        reason_code: str = FACEBOOK_TEMPORARY_BLOCK_REASON,
    ) -> FacebookAutomationSessionMarker:
        """以atomic replace切換trip_pending；失敗時保留原marker。"""

        current = self.read()
        if current is None:
            raise FacebookAutomationSessionGuardError(
                FacebookAutomationSessionGuardErrorCode.INVALID_MARKER
            )
        if current.session_id != _canonical_uuid(session_id):
            raise FacebookAutomationSessionGuardError(
                FacebookAutomationSessionGuardErrorCode.OWNER_MISMATCH
            )
        if current.state != FacebookAutomationSessionGuardState.NORMAL_SESSION:
            raise FacebookAutomationSessionGuardError(
                FacebookAutomationSessionGuardErrorCode.STATE_MISMATCH
            )
        if reason_code != FACEBOOK_TEMPORARY_BLOCK_REASON:
            raise ValueError("trip pending reason must be the stable temporary-block reason")
        pending = FacebookAutomationSessionMarker(
            session_id=current.session_id,
            profile_alias=current.profile_alias,
            state=FacebookAutomationSessionGuardState.TRIP_PENDING,
            started_at=current.started_at,
            reason_code=reason_code,
            operation_kind=operation_kind,
            trigger_action_kind=trigger_action_kind,
        )
        temp_path = self._write_private_temp(_serialize_marker(pending))
        try:
            try:
                os.replace(temp_path, self.marker_path)
            except OSError as exc:
                raise FacebookAutomationSessionGuardError(
                    FacebookAutomationSessionGuardErrorCode.STORAGE_UNAVAILABLE
                ) from exc
            self._sync_directory()
        finally:
            _unlink_if_present(temp_path)
        return pending

    def read(self) -> FacebookAutomationSessionMarker | None:
        """Strict-read marker；corrupt、extra fields與unsafe filesystem皆fail closed。"""

        self._ensure_private_directory()
        return self._read_existing_marker()

    def inspect_existing(self) -> FacebookAutomationSessionMarker | None:
        """唯讀檢查既有 marker；目錄不存在時不建立。"""

        if not self.directory.exists() and not self.directory.is_symlink():
            return None
        if _is_symlink_or_junction(self.directory) or not self.directory.is_dir():
            raise FacebookAutomationSessionGuardError(
                FacebookAutomationSessionGuardErrorCode.STORAGE_UNAVAILABLE
            )
        return self._read_existing_marker()

    def _read_existing_marker(self) -> FacebookAutomationSessionMarker | None:
        """讀取已確認存在或可安全檢查的 sentinel directory。"""

        path = self.marker_path
        if not path.exists() and not path.is_symlink():
            return None
        self._reject_unsafe_marker_path()
        try:
            metadata = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_MARKER_BYTES:
                raise ValueError
            raw = path.read_bytes()
            if len(raw) > _MAX_MARKER_BYTES:
                raise ValueError
            value = json.loads(raw.decode("utf-8"))
            return _parse_marker(value, expected_alias=self.profile_alias)
        except FacebookAutomationSessionGuardError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise FacebookAutomationSessionGuardError(
                FacebookAutomationSessionGuardErrorCode.INVALID_MARKER
            ) from exc

    def clear_clean_session(self, *, session_id: str) -> None:
        """只有matching normal owner可在乾淨shutdown後移除marker。"""

        self._clear_owned(
            session_id=session_id,
            required_state=FacebookAutomationSessionGuardState.NORMAL_SESSION,
        )

    def clear_persisted_trip(self, *, session_id: str) -> None:
        """只有durable DB incident/reconcile完成後可移除matching trip marker。"""

        self._clear_owned(
            session_id=session_id,
            required_state=FacebookAutomationSessionGuardState.TRIP_PENDING,
        )

    def _clear_owned(
        self,
        *,
        session_id: str,
        required_state: FacebookAutomationSessionGuardState,
    ) -> None:
        marker = self.read()
        if marker is None:
            raise FacebookAutomationSessionGuardError(
                FacebookAutomationSessionGuardErrorCode.INVALID_MARKER
            )
        if marker.session_id != _canonical_uuid(session_id):
            raise FacebookAutomationSessionGuardError(
                FacebookAutomationSessionGuardErrorCode.OWNER_MISMATCH
            )
        if marker.state != required_state:
            raise FacebookAutomationSessionGuardError(
                FacebookAutomationSessionGuardErrorCode.STATE_MISMATCH
            )
        try:
            self.marker_path.unlink()
            self._sync_directory()
        except OSError as exc:
            raise FacebookAutomationSessionGuardError(
                FacebookAutomationSessionGuardErrorCode.STORAGE_UNAVAILABLE
            ) from exc

    def _ensure_private_directory(self) -> None:
        try:
            if self.directory.exists() or self.directory.is_symlink():
                if _is_symlink_or_junction(self.directory) or not self.directory.is_dir():
                    raise OSError
            else:
                self.directory.mkdir(parents=True, mode=0o700)
            if os.name != "nt":
                self.directory.chmod(0o700)
        except OSError as exc:
            raise FacebookAutomationSessionGuardError(
                FacebookAutomationSessionGuardErrorCode.STORAGE_UNAVAILABLE
            ) from exc

    def _reject_unsafe_marker_path(self) -> None:
        path = self.marker_path
        if (path.exists() or path.is_symlink()) and _is_symlink_or_junction(path):
            raise FacebookAutomationSessionGuardError(
                FacebookAutomationSessionGuardErrorCode.INVALID_MARKER
            )

    def _write_private_temp(self, payload: bytes) -> Path:
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=".session-guard-",
                suffix=".tmp",
                dir=self.directory,
            )
            temp_path = Path(raw_path)
            try:
                if os.name != "nt":
                    os.chmod(temp_path, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    descriptor = -1
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                return temp_path
            except BaseException:
                if descriptor >= 0:
                    os.close(descriptor)
                _unlink_if_present(temp_path)
                raise
        except FacebookAutomationSessionGuardError:
            raise
        except OSError as exc:
            raise FacebookAutomationSessionGuardError(
                FacebookAutomationSessionGuardErrorCode.STORAGE_UNAVAILABLE
            ) from exc

    def _sync_directory(self) -> None:
        if os.name == "nt":
            return
        try:
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise FacebookAutomationSessionGuardError(
                FacebookAutomationSessionGuardErrorCode.STORAGE_UNAVAILABLE
            ) from exc


class FacebookAutomationSessionGuardRuntime:
    """串接單次resident browser context與durable marker的process owner。"""

    def __init__(self, store: FacebookAutomationSessionGuardStore) -> None:
        self.store = store
        self._lock = Lock()
        self._session_id = ""
        self._active = False
        self._cleanup_forbidden = False
        self._trip_pending = False
        self._incident_committed = False

    def start_before_browser_io(
        self,
        *,
        started_at: datetime | None = None,
        session_id: str | None = None,
    ) -> str:
        """在browser入口前以指定pacing owner建立marker；失敗就不得launch。"""

        with self._lock:
            if self._active:
                raise FacebookAutomationSessionGuardError(
                    FacebookAutomationSessionGuardErrorCode.STATE_MISMATCH
                )
            marker = self.store.start_normal_session(
                started_at=started_at,
                session_id=session_id,
            )
            self._session_id = marker.session_id
            self._active = True
            self._cleanup_forbidden = False
            self._trip_pending = False
            self._incident_committed = False
            return marker.session_id

    def mark_trip_pending(
        self,
        *,
        operation_kind: FacebookProductOperationKind,
        trigger_action_kind: FacebookActionKind,
    ) -> None:
        """Trip latch關閉後、incident transaction前原子升級marker。"""

        with self._lock:
            if not self._active or not self._session_id:
                raise FacebookAutomationSessionGuardError(
                    FacebookAutomationSessionGuardErrorCode.STATE_MISMATCH
                )
            # 先禁止normal cleanup；即使replace失敗也必須保留原marker。
            self._cleanup_forbidden = True
            self.store.mark_trip_pending(
                session_id=self._session_id,
                operation_kind=operation_kind,
                trigger_action_kind=trigger_action_kind,
            )
            self._trip_pending = True

    def note_incident_committed(self) -> None:
        """記錄SQLite incident已durable；仍須等browser context關閉才可清marker。"""

        with self._lock:
            if not self._active or not self._trip_pending:
                raise FacebookAutomationSessionGuardError(
                    FacebookAutomationSessionGuardErrorCode.STATE_MISMATCH
                )
            self._incident_committed = True

    def finish_after_browser_context_closed(self) -> bool:
        """依clean/durable-trip條件清marker；不確定情況一律保留。"""

        with self._lock:
            if not self._active:
                return False
            if self._trip_pending:
                if not self._incident_committed:
                    return False
                self.store.clear_persisted_trip(session_id=self._session_id)
            else:
                if self._cleanup_forbidden:
                    return False
                self.store.clear_clean_session(session_id=self._session_id)
            self._session_id = ""
            self._active = False
            self._cleanup_forbidden = False
            self._trip_pending = False
            self._incident_committed = False
            return True


def derive_facebook_automation_profile_alias(profile_scope_key: str) -> str:
    """由opaque managed identity派生不含profile path/name的穩定marker alias。"""

    normalized_scope = str(profile_scope_key).strip()
    if not normalized_scope:
        raise ValueError("profile scope key is required")
    digest = hashlib.sha256(
        f"facebook-session-guard-alias-v1:{normalized_scope}".encode("utf-8")
    ).hexdigest()
    return f"profile-{digest[:32]}"


def reconcile_facebook_automation_restart_guard(
    *,
    db_path: Path,
    store: FacebookAutomationSessionGuardStore,
    profile_scope_key: str,
    reconciled_at: datetime | None = None,
) -> FacebookAutomationRestartGuardResult:
    """Browser-free restart reconcile；normal與trip marker維持不同語義。"""

    try:
        marker = store.read()
    except FacebookAutomationSessionGuardError as exc:
        return _storage_critical(exc.code.value)
    if marker is None:
        return FacebookAutomationRestartGuardResult(
            outcome=FacebookAutomationRestartGuardOutcome.NO_GUARD,
            browser_io_allowed=True,
        )
    if marker.state == FacebookAutomationSessionGuardState.NORMAL_SESSION:
        now = reconciled_at or utc_now()

        def reconcile_normal_session() -> FacebookSessionRecoveryReconcileResult:
            """以單一 writer transaction 記錄 hold 並回收舊 pacing owner。"""

            with SqliteApplicationContext(
                db_path,
                initialize_schema_on_enter=False,
            ) as app:
                connection = app.repositories.facebook_session_recovery.connection
                if connection.in_transaction:
                    connection.commit()
                connection.execute("BEGIN IMMEDIATE")
                return app.services.facebook_session_recovery.reconcile_stale_session(
                    profile_scope_key,
                    marker_session_id=marker.session_id,
                    reconciled_at=now,
                )

        try:
            recovery = run_sqlite_operation_with_retry(
                reconcile_normal_session,
                operation_name="reconcile_facebook_automation_normal_session",
                logger=logger,
            )
        except Exception:
            logger.exception("facebook_automation_normal_session_reconcile_failed")
            return _storage_critical("database_reconcile_failed", marker.state)
        if recovery.outcome == (
            FacebookSessionRecoveryReconcileOutcome.PACING_OWNER_MISMATCH
        ):
            return FacebookAutomationRestartGuardResult(
                outcome=FacebookAutomationRestartGuardOutcome.STORAGE_CRITICAL,
                browser_io_allowed=False,
                marker_state=marker.state,
                session_recovery=recovery,
                reason_code="pacing_owner_mismatch",
            )
        if recovery.state.status == FacebookSessionRecoveryStatus.RECOVERED:
            try:
                store.clear_clean_session(session_id=marker.session_id)
            except FacebookAutomationSessionGuardError as exc:
                return FacebookAutomationRestartGuardResult(
                    outcome=FacebookAutomationRestartGuardOutcome.STORAGE_CRITICAL,
                    browser_io_allowed=False,
                    marker_state=marker.state,
                    session_recovery=recovery,
                    reason_code=exc.code.value,
                )
            return FacebookAutomationRestartGuardResult(
                outcome=FacebookAutomationRestartGuardOutcome.NO_GUARD,
                browser_io_allowed=True,
                session_recovery=recovery,
            )
        return FacebookAutomationRestartGuardResult(
            outcome=FacebookAutomationRestartGuardOutcome.UNCLEAN_SESSION_HOLD,
            browser_io_allowed=False,
            marker_state=marker.state,
            session_recovery=recovery,
            reason_code="facebook_automation_unclean_session",
        )
    if marker.operation_kind is None or marker.trigger_action_kind is None:
        return _storage_critical("invalid_trip_pending_marker")
    operation_kind = marker.operation_kind
    trigger_action_kind = marker.trigger_action_kind

    now = reconciled_at or utc_now()

    def operation() -> FacebookSafetyHoldResult:
        with SqliteApplicationContext(db_path, initialize_schema_on_enter=False) as app:
            connection = app.repositories.facebook_access_circuit.connection
            if connection.in_transaction:
                connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            return app.services.facebook_access_circuit.reconcile_persistence_uncertain(
                profile_scope_key,
                operation_kind=operation_kind,
                trigger_action_kind=trigger_action_kind,
                reconciled_at=now,
            )

    try:
        safety_hold = run_sqlite_operation_with_retry(
            operation,
            operation_name="reconcile_facebook_automation_session_guard",
            logger=logger,
        )
    except Exception:
        logger.exception("facebook_automation_session_guard_reconcile_failed")
        return _storage_critical("database_reconcile_failed", marker.state)
    try:
        store.clear_persisted_trip(session_id=marker.session_id)
    except FacebookAutomationSessionGuardError as exc:
        return FacebookAutomationRestartGuardResult(
            outcome=FacebookAutomationRestartGuardOutcome.STORAGE_CRITICAL,
            browser_io_allowed=False,
            marker_state=marker.state,
            safety_hold=safety_hold,
            reason_code=exc.code.value,
        )
    return FacebookAutomationRestartGuardResult(
        outcome=(
            FacebookAutomationRestartGuardOutcome.PERSISTENCE_UNCERTAIN_RECONCILED
        ),
        browser_io_allowed=False,
        marker_state=marker.state,
        safety_hold=safety_hold,
        reason_code="facebook_access_persistence_uncertain",
    )


def _storage_critical(
    reason_code: str,
    marker_state: FacebookAutomationSessionGuardState | None = None,
) -> FacebookAutomationRestartGuardResult:
    return FacebookAutomationRestartGuardResult(
        outcome=FacebookAutomationRestartGuardOutcome.STORAGE_CRITICAL,
        browser_io_allowed=False,
        marker_state=marker_state,
        reason_code=reason_code,
    )


def _serialize_marker(marker: FacebookAutomationSessionMarker) -> bytes:
    value: dict[str, object] = {
        "version": _MARKER_VERSION,
        "session_id": marker.session_id,
        "profile_alias": marker.profile_alias,
        "state": marker.state.value,
        "started_at": _encode_utc(marker.started_at),
    }
    if marker.state == FacebookAutomationSessionGuardState.TRIP_PENDING:
        value.update(
            {
                "reason_code": marker.reason_code,
                "operation_kind": (
                    marker.operation_kind.value if marker.operation_kind else ""
                ),
                "trigger_action_kind": (
                    marker.trigger_action_kind.value if marker.trigger_action_kind else ""
                ),
            }
        )
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _parse_marker(
    value: object,
    *,
    expected_alias: str,
) -> FacebookAutomationSessionMarker:
    if not isinstance(value, dict):
        raise ValueError
    if value.get("version") != _MARKER_VERSION:
        raise ValueError
    state = FacebookAutomationSessionGuardState(str(value.get("state", "")))
    expected_fields = (
        _NORMAL_FIELDS
        if state == FacebookAutomationSessionGuardState.NORMAL_SESSION
        else _TRIP_FIELDS
    )
    if frozenset(value) != expected_fields:
        raise ValueError
    profile_alias = str(value["profile_alias"])
    if profile_alias != expected_alias:
        raise ValueError
    marker = FacebookAutomationSessionMarker(
        session_id=_canonical_uuid(str(value["session_id"])),
        profile_alias=profile_alias,
        state=state,
        started_at=_decode_utc(str(value["started_at"])),
        reason_code=str(value.get("reason_code", "")),
        operation_kind=(
            FacebookProductOperationKind(str(value["operation_kind"]))
            if state == FacebookAutomationSessionGuardState.TRIP_PENDING
            else None
        ),
        trigger_action_kind=(
            FacebookActionKind(str(value["trigger_action_kind"]))
            if state == FacebookAutomationSessionGuardState.TRIP_PENDING
            else None
        ),
    )
    if (
        marker.state == FacebookAutomationSessionGuardState.TRIP_PENDING
        and marker.reason_code != FACEBOOK_TEMPORARY_BLOCK_REASON
    ):
        raise ValueError
    return marker


def _canonical_uuid(value: str) -> str:
    parsed = UUID(str(value).strip())
    if str(parsed) != str(value).strip().lower():
        raise ValueError("session id must be a canonical UUID")
    return str(parsed)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("session guard timestamp must be UTC")
    return value


def _encode_utc(value: datetime) -> str:
    return _require_utc(value).isoformat().replace("+00:00", "Z")


def _decode_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _require_utc(parsed)


def _is_symlink_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _unlink_if_present(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


__all__ = [
    "FacebookAutomationRestartGuardOutcome",
    "FacebookAutomationRestartGuardResult",
    "FacebookAutomationSessionGuardError",
    "FacebookAutomationSessionGuardErrorCode",
    "FacebookAutomationSessionGuardState",
    "FacebookAutomationSessionGuardStore",
    "FacebookAutomationSessionGuardRuntime",
    "FacebookAutomationSessionMarker",
    "derive_facebook_automation_profile_alias",
    "reconcile_facebook_automation_restart_guard",
]
