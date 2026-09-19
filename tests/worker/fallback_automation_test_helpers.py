"""Fallback managed-profile circuit 測試共用 helper。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.automation.profile_identity import (
    load_or_create_managed_profile_identity,
)
from facebook_monitor.core.facebook_access import FacebookAccessBlockSignal
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookWorkSourceKind
from facebook_monitor.core.models import utc_now


def seed_open_or_half_open_profile_circuit(
    *,
    db_path: Path,
    profile_dir: Path,
    target_id: str,
    half_open: bool,
) -> str:
    """建立指定 managed profile 的 open/half-open circuit 狀態。"""

    identity = load_or_create_managed_profile_identity(
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    opened_at = utc_now() - timedelta(hours=13)
    with SqliteApplicationContext(db_path) as app:
        service = app.services.facebook_access_circuit
        admission = service.admit_normal(
            identity.profile_scope_key,
            process_safety_epoch=0,
            operation_id="fallback-test-admission",
            admitted_at=opened_at,
        )
        assert admission.token is not None
        opened = service.trip(
            FacebookAccessBlockSignal(
                admission_token=admission.token,
                source_kind=FacebookWorkSourceKind.SCAN,
                operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
                trigger_action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
                source_owner_token="fallback-test-owner",
                target_id=target_id,
            ),
            source_owner_is_valid=True,
            detected_at=opened_at,
        )
        if half_open:
            requested = service.request_probe(
                identity.profile_scope_key,
                target_id=target_id,
                requested_at=utc_now(),
            )
            claimed = service.claim_half_open(
                identity.profile_scope_key,
                request_id=requested.state.probe_request_id,
                started_at=utc_now(),
            )
            assert claimed.state is not None
            assert claimed.state.status.value == "half_open"
        else:
            assert opened.state.status.value == "open"
    return identity.profile_scope_key
