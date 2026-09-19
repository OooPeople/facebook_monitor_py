"""Facebook access manual recovery Web use case。

職責：列出同 operation 的 active canary，並提出一次 persistent probe
request；不 claim half-open，不建立 browser。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from pathlib import Path

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.managed_profile_identity import (
    inspect_managed_profile_identity,
)
from facebook_monitor.core.facebook_access import FacebookProbeRequestOutcome
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryRequestOutcome,
)
from facebook_monitor.core.facebook_session_recovery import FacebookSessionRecoveryStatus


@dataclass(frozen=True)
class FacebookAccessRecoveryRequestOutcome:
    """Web route 可直接呈現的 bounded recovery request 結果。"""

    ok: bool
    message: str
    feedback: str = ""
    wake_scheduler: bool = False


@dataclass(frozen=True)
class FacebookAccessRecoveryCandidate:
    """Dashboard 可顯示、但仍由 application service 重驗的 canary。"""

    label: str
    request_value: str
    is_trigger_target: bool = False


def list_facebook_access_recovery_candidates(
    app_context: ApplicationContext,
    *,
    profile_dir: Path,
) -> tuple[FacebookAccessRecoveryCandidate, ...]:
    """列出當前 episode 同 operation 的 active canary，不洩漏 profile identity。"""

    identity = _inspect_recovery_identity(app_context, profile_dir=profile_dir)
    if identity is None:
        return ()
    service = app_context.services.facebook_access_circuit
    state = service.get(identity.profile_scope_key)
    if state is not None and state.operation_kind is not None:
        circuit_candidates = tuple(
            FacebookAccessRecoveryCandidate(
                label=target.name,
                request_value=_candidate_handle(
                    identity.profile_scope_key,
                    state.operation_kind,
                    target.id,
                ),
                is_trigger_target=target.id == state.trigger_target_id,
            )
            for target in service.list_probe_target_candidates(
                identity.profile_scope_key
            )
        )
        if circuit_candidates:
            return circuit_candidates
    session_service = app_context.services.facebook_session_recovery
    session_state = session_service.get(identity.profile_scope_key)
    if (
        session_state is None
        or session_state.status != FacebookSessionRecoveryStatus.HOLD
    ):
        return ()
    operation_labels = {
        FacebookProductOperationKind.POSTS_ACCESS: "貼文存取",
        FacebookProductOperationKind.GROUP_METADATA_ACCESS: "社團資訊",
        FacebookProductOperationKind.COVER_METADATA_ACCESS: "社團封面",
    }
    return tuple(
        FacebookAccessRecoveryCandidate(
            label=f"{target.name} · {operation_labels[operation_kind]}",
            request_value=_candidate_handle(
                identity.profile_scope_key,
                operation_kind,
                target.id,
            ),
        )
        for operation_kind in operation_labels
        for target in session_service.list_probe_target_candidates(
            identity.profile_scope_key,
            operation_kind=operation_kind,
        )
    )


def request_facebook_access_recovery_check(
    app_context: ApplicationContext,
    *,
    profile_dir: Path,
    requested_candidate: str | None = None,
) -> FacebookAccessRecoveryRequestOutcome:
    """以 trigger 或使用者顯式選定的替代 canary 登記恢復檢查。"""

    identity = _inspect_recovery_identity(app_context, profile_dir=profile_dir)
    if identity is None:
        return _unavailable("受管 profile identity 狀態異常，不能提出恢復檢查。")
    service = app_context.services.facebook_access_circuit
    state = service.get(identity.profile_scope_key)
    if state is None or state.operation_kind is None:
        return _unavailable("目前沒有可恢復的 Facebook 安全暫停狀態。")
    selected_target_id = state.trigger_target_id
    submitted_candidate = str(requested_candidate or "").strip()
    if submitted_candidate:
        selected_target_id = ""
        for target in service.list_probe_target_candidates(identity.profile_scope_key):
            expected = _candidate_handle(
                identity.profile_scope_key,
                state.operation_kind,
                target.id,
            )
            if hmac.compare_digest(submitted_candidate, expected):
                selected_target_id = target.id
                break
        if not selected_target_id:
            return _unavailable(
                "選擇的 target 已停用、暫停、不存在或不符合本次操作，"
                "請重新選擇安全檢查對象。"
            )
    result = service.request_probe(
        identity.profile_scope_key,
        target_id=selected_target_id,
    )
    if result.outcome == FacebookProbeRequestOutcome.REQUESTED:
        return FacebookAccessRecoveryRequestOutcome(
            ok=True,
            message="已排程一次安全恢復檢查。",
            feedback="facebook_access_recovery_requested",
            wake_scheduler=True,
        )
    if result.outcome == FacebookProbeRequestOutcome.ALREADY_PENDING:
        return FacebookAccessRecoveryRequestOutcome(
            ok=True,
            message="安全恢復檢查已在排程中。",
            feedback="facebook_access_recovery_pending",
            wake_scheduler=True,
        )
    if result.outcome == FacebookProbeRequestOutcome.COOLDOWN_ACTIVE:
        return _unavailable("冷卻時間尚未結束，現在不能執行恢復檢查。")
    if result.outcome == FacebookProbeRequestOutcome.RECIPE_UNAVAILABLE:
        if result.state.operation_kind == FacebookProductOperationKind.COMMENTS_ACCESS:
            return _unavailable(
                "留言監視尚未有核准的安全恢復流程，目前不能執行恢復檢查。"
            )
        return _unavailable("目前沒有適用的安全恢復流程。")
    if result.outcome == FacebookProbeRequestOutcome.TARGET_UNAVAILABLE:
        return _unavailable(
            "選擇的 target 已停用、暫停、不存在或不符合本次操作，"
            "請重新選擇安全檢查對象。"
        )
    return _unavailable("目前狀態不能提出新的恢復檢查。")


def request_facebook_session_recovery_check(
    app_context: ApplicationContext,
    *,
    profile_dir: Path,
    requested_candidate: str,
) -> FacebookAccessRecoveryRequestOutcome:
    """驗證使用者選定的 operation/target 後只持久化 healthcheck request。"""

    identity = _inspect_recovery_identity(app_context, profile_dir=profile_dir)
    if identity is None:
        return _unavailable("受管 profile identity 狀態異常，不能提出恢復檢查。")
    submitted_candidate = str(requested_candidate or "").strip()
    if not submitted_candidate:
        return _unavailable("請選擇一個安全檢查對象。")
    operation_kind: FacebookProductOperationKind | None = None
    target_id = ""
    session_service = app_context.services.facebook_session_recovery
    for candidate_operation in (
        FacebookProductOperationKind.POSTS_ACCESS,
        FacebookProductOperationKind.GROUP_METADATA_ACCESS,
        FacebookProductOperationKind.COVER_METADATA_ACCESS,
    ):
        for target in session_service.list_probe_target_candidates(
            identity.profile_scope_key,
            operation_kind=candidate_operation,
        ):
            expected = _candidate_handle(
                identity.profile_scope_key,
                candidate_operation,
                target.id,
            )
            if hmac.compare_digest(submitted_candidate, expected):
                operation_kind = candidate_operation
                target_id = target.id
                break
        if operation_kind is not None:
            break
    if operation_kind is None or not target_id:
        return _unavailable("選擇的 target 已停用、暫停或不符合本次操作。")
    result = app_context.services.facebook_session_recovery.request_probe(
        identity.profile_scope_key,
        target_id=target_id,
        operation_kind=operation_kind,
    )
    if result.outcome == FacebookSessionRecoveryRequestOutcome.REQUESTED:
        return FacebookAccessRecoveryRequestOutcome(
            ok=True,
            message="已排程一次非預期中斷健康檢查。",
            feedback="facebook_session_recovery_requested",
            wake_scheduler=True,
        )
    if result.outcome == FacebookSessionRecoveryRequestOutcome.ALREADY_PENDING:
        return FacebookAccessRecoveryRequestOutcome(
            ok=True,
            message="非預期中斷健康檢查已在排程中。",
            feedback="facebook_session_recovery_pending",
            wake_scheduler=True,
        )
    if result.outcome == FacebookSessionRecoveryRequestOutcome.QUIET_PERIOD_ACTIVE:
        return _unavailable("安靜期尚未結束，現在不能執行健康檢查。")
    if result.outcome == FacebookSessionRecoveryRequestOutcome.RECIPE_UNAVAILABLE:
        return _unavailable("目前沒有適用的非預期中斷健康檢查流程。")
    if result.outcome == FacebookSessionRecoveryRequestOutcome.TARGET_UNAVAILABLE:
        return _unavailable("選擇的 target 已停用、暫停或不符合本次操作。")
    return _unavailable("目前狀態不能提出非預期中斷健康檢查。")


def _unavailable(message: str) -> FacebookAccessRecoveryRequestOutcome:
    """建立不會觸發 scheduler 的保守結果。"""

    return FacebookAccessRecoveryRequestOutcome(ok=False, message=message)


def _candidate_handle(
    profile_scope_key: str,
    operation_kind: FacebookProductOperationKind,
    target_id: str,
) -> str:
    """產生只供單次 Web 選擇解析的 opaque bounded handle。"""

    digest = hashlib.sha256(
        (
            "facebook-recovery-candidate-v1:"
            f"{profile_scope_key}:{operation_kind.value}:{target_id}"
        ).encode("utf-8")
    ).hexdigest()
    return f"candidate-{digest[:32]}"


def _inspect_recovery_identity(
    app_context: ApplicationContext,
    *,
    profile_dir: Path,
):
    """集中 recovery read/write 共用的 managed identity continuity preflight。"""

    if app_context.db_path is None:
        return None
    inspection = inspect_managed_profile_identity(
        db_path=app_context.db_path,
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    if inspection.storage_critical:
        return None
    return inspection.identity


__all__ = [
    "FacebookAccessRecoveryCandidate",
    "FacebookAccessRecoveryRequestOutcome",
    "list_facebook_access_recovery_candidates",
    "request_facebook_access_recovery_check",
    "request_facebook_session_recovery_check",
]
