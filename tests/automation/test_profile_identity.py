"""Managed profile opaque identity tests。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC
from datetime import datetime
from pathlib import Path
import shutil

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.managed_profile_identity import (
    ManagedProfileIdentityError,
)
from facebook_monitor.application.managed_profile_identity import (
    ManagedProfileIdentityStatus,
)
from facebook_monitor.application.managed_profile_identity import (
    inspect_managed_profile_identity,
)
from facebook_monitor.application.managed_profile_identity import (
    resolve_managed_profile_identity,
)
from facebook_monitor.automation.profile_identity import PROFILE_ID_MARKER_FILENAME
from facebook_monitor.automation.profile_identity import (
    load_or_create_managed_profile_identity,
)


def test_same_profile_name_in_different_roots_gets_different_scope_keys(
    tmp_path: Path,
) -> None:
    """同名 profile 在不同 managed roots 不得碰撞。"""

    first_root = tmp_path / "first" / "profiles"
    second_root = tmp_path / "second" / "profiles"

    first = load_or_create_managed_profile_identity(
        profiles_root=first_root,
        profile_dir=first_root / "default",
    )
    second = load_or_create_managed_profile_identity(
        profiles_root=second_root,
        profile_dir=second_root / "default",
    )

    assert first.profile_scope_key != second.profile_scope_key
    assert first.marker_path.name == PROFILE_ID_MARKER_FILENAME
    assert first.marker_path.read_text(encoding="ascii").strip()


def test_moving_or_cloning_whole_profile_preserves_conservative_identity(
    tmp_path: Path,
) -> None:
    """整體搬移保留 identity；連 marker clone 時保守共用 scope。"""

    root = tmp_path / "profiles"
    original_dir = root / "original"
    original = load_or_create_managed_profile_identity(
        profiles_root=root,
        profile_dir=original_dir,
    )
    moved_dir = root / "moved"
    shutil.move(original_dir, moved_dir)
    moved = load_or_create_managed_profile_identity(
        profiles_root=root,
        profile_dir=moved_dir,
    )
    clone_dir = root / "clone"
    shutil.copytree(moved_dir, clone_dir)
    clone = load_or_create_managed_profile_identity(
        profiles_root=root,
        profile_dir=clone_dir,
    )

    assert moved.profile_scope_key == original.profile_scope_key
    assert clone.profile_scope_key == original.profile_scope_key


def test_invalid_marker_and_outside_profile_fail_closed(tmp_path: Path) -> None:
    """Marker 損毀或 profile 脫離 managed root 時不得退回 path hash。"""

    root = tmp_path / "profiles"
    profile = root / "broken"
    profile.mkdir(parents=True)
    (profile / PROFILE_ID_MARKER_FILENAME).write_text("not-a-uuid", encoding="ascii")

    with pytest.raises(ValueError, match="marker is invalid"):
        load_or_create_managed_profile_identity(
            profiles_root=root,
            profile_dir=profile,
        )
    with pytest.raises(ValueError, match="under profiles root"):
        load_or_create_managed_profile_identity(
            profiles_root=root,
            profile_dir=tmp_path / "outside",
        )


def test_concurrent_first_use_publishes_one_complete_marker(tmp_path: Path) -> None:
    """並發首次使用只能發布一個完整 UUID identity。"""

    root = tmp_path / "profiles"
    profile = root / "concurrent"

    with ThreadPoolExecutor(max_workers=8) as executor:
        identities = tuple(
            executor.map(
                lambda _: load_or_create_managed_profile_identity(
                    profiles_root=root,
                    profile_dir=profile,
                ),
                range(16),
            )
        )

    assert len({identity.profile_scope_key for identity in identities}) == 1
    assert not tuple(profile.glob(".*.tmp"))


def test_durable_binding_rejects_missing_corrupt_and_replaced_marker(
    tmp_path: Path,
) -> None:
    """Binding 建立後 marker 遺失、損毀或替換都不得產生新 runtime scope。"""

    db_path = tmp_path / "app.db"
    root = tmp_path / "profiles"
    profile = root / "default"
    identity = resolve_managed_profile_identity(
        db_path=db_path,
        profiles_root=root,
        profile_dir=profile,
    )
    with SqliteApplicationContext(db_path) as app:
        binding = app.repositories.managed_profile_identity.get()
        assert binding is not None
        assert binding.marker_uuid == identity.marker_uuid

    identity.marker_path.unlink()
    with pytest.raises(ManagedProfileIdentityError) as missing:
        resolve_managed_profile_identity(
            db_path=db_path,
            profiles_root=root,
            profile_dir=profile,
        )
    assert missing.value.status == ManagedProfileIdentityStatus.MISSING

    identity.marker_path.write_text("not-a-uuid", encoding="ascii")
    with pytest.raises(ManagedProfileIdentityError) as corrupt:
        resolve_managed_profile_identity(
            db_path=db_path,
            profiles_root=root,
            profile_dir=profile,
        )
    assert corrupt.value.status == ManagedProfileIdentityStatus.CORRUPT

    identity.marker_path.unlink()
    replacement = load_or_create_managed_profile_identity(
        profiles_root=root,
        profile_dir=profile,
    )
    assert replacement.profile_scope_key != identity.profile_scope_key
    with pytest.raises(ManagedProfileIdentityError) as mismatch:
        resolve_managed_profile_identity(
            db_path=db_path,
            profiles_root=root,
            profile_dir=profile,
        )
    assert mismatch.value.status == ManagedProfileIdentityStatus.MISMATCH


def test_legacy_marker_backfills_binding_but_missing_marker_with_evidence_blocks(
    tmp_path: Path,
) -> None:
    """合法 legacy marker 可 backfill；已有 scope truth 卻無 marker 時 fail closed。"""

    legacy_db = tmp_path / "legacy.db"
    legacy_root = tmp_path / "legacy" / "profiles"
    legacy_profile = legacy_root / "default"
    legacy = load_or_create_managed_profile_identity(
        profiles_root=legacy_root,
        profile_dir=legacy_profile,
    )
    with SqliteApplicationContext(legacy_db) as app:
        app.repositories.facebook_access_circuit.ensure_closed(
            legacy.profile_scope_key,
            updated_at=datetime.now(UTC),
        )
    resolved = resolve_managed_profile_identity(
        db_path=legacy_db,
        profiles_root=legacy_root,
        profile_dir=legacy_profile,
    )
    assert resolved.profile_scope_key == legacy.profile_scope_key

    lost_db = tmp_path / "lost.db"
    lost_root = tmp_path / "lost" / "profiles"
    lost_profile = lost_root / "default"
    with SqliteApplicationContext(lost_db) as app:
        app.repositories.facebook_access_circuit.ensure_closed(
            "old-private-scope",
            updated_at=datetime.now(UTC),
        )
    with pytest.raises(ManagedProfileIdentityError) as lost:
        resolve_managed_profile_identity(
            db_path=lost_db,
            profiles_root=lost_root,
            profile_dir=lost_profile,
        )
    assert lost.value.status == ManagedProfileIdentityStatus.MISSING
    assert not (lost_profile / PROFILE_ID_MARKER_FILENAME).exists()

    recovery_db = tmp_path / "recovery-lost.db"
    recovery_root = tmp_path / "recovery-lost" / "profiles"
    recovery_profile = recovery_root / "default"
    recovered_at = datetime.now(UTC)
    with SqliteApplicationContext(recovery_db) as app:
        app.repositories.facebook_session_recovery.record_stale_session(
            "old-recovery-scope",
            marker_session_id="11111111-1111-4111-8111-111111111111",
            stale_detected_at=recovered_at,
            earliest_probe_at=recovered_at,
        )
    with pytest.raises(ManagedProfileIdentityError) as recovery_lost:
        resolve_managed_profile_identity(
            db_path=recovery_db,
            profiles_root=recovery_root,
            profile_dir=recovery_profile,
        )
    assert recovery_lost.value.status == ManagedProfileIdentityStatus.MISSING
    assert not (recovery_profile / PROFILE_ID_MARKER_FILENAME).exists()


def test_legacy_marker_with_different_safety_scope_fails_closed(
    tmp_path: Path,
) -> None:
    """Binding 尚未建立時，現存 marker 不得覆蓋不同 scope 的 safety truth。"""

    db_path = tmp_path / "app.db"
    root = tmp_path / "profiles"
    profile = root / "default"
    original = load_or_create_managed_profile_identity(
        profiles_root=root,
        profile_dir=profile,
    )
    with SqliteApplicationContext(db_path) as app:
        app.repositories.facebook_access_circuit.ensure_closed(
            original.profile_scope_key,
            updated_at=datetime.now(UTC),
        )
    original.marker_path.unlink()
    replacement = load_or_create_managed_profile_identity(
        profiles_root=root,
        profile_dir=profile,
    )
    assert replacement.profile_scope_key != original.profile_scope_key

    inspection = inspect_managed_profile_identity(
        db_path=db_path,
        profiles_root=root,
        profile_dir=profile,
    )
    assert inspection.status == ManagedProfileIdentityStatus.MISMATCH
    assert inspection.storage_critical
    assert inspection.identity is None
    with pytest.raises(ManagedProfileIdentityError) as failed:
        resolve_managed_profile_identity(
            db_path=db_path,
            profiles_root=root,
            profile_dir=profile,
        )
    assert failed.value.status == ManagedProfileIdentityStatus.MISMATCH
    with SqliteApplicationContext(db_path) as app:
        assert app.repositories.managed_profile_identity.get() is None
        assert app.repositories.facebook_access_circuit.get(
            original.profile_scope_key
        ) is not None
        assert app.repositories.facebook_access_circuit.get(
            replacement.profile_scope_key
        ) is None


def test_legacy_marker_with_multiple_safety_scopes_fails_closed(
    tmp_path: Path,
) -> None:
    """即使其中一個 scope 符合 marker，多個 durable scopes 仍視為 storage mismatch。"""

    db_path = tmp_path / "app.db"
    root = tmp_path / "profiles"
    profile = root / "default"
    identity = load_or_create_managed_profile_identity(
        profiles_root=root,
        profile_dir=profile,
    )
    observed_at = datetime.now(UTC)
    with SqliteApplicationContext(db_path) as app:
        app.repositories.facebook_access_circuit.ensure_closed(
            identity.profile_scope_key,
            updated_at=observed_at,
        )
        app.repositories.facebook_automation_pacing.ensure(
            "different-private-scope",
            updated_at=observed_at,
        )
        assert set(
            app.repositories.managed_profile_identity.list_scoped_safety_evidence()
        ) == {"different-private-scope", identity.profile_scope_key}

    inspection = inspect_managed_profile_identity(
        db_path=db_path,
        profiles_root=root,
        profile_dir=profile,
    )
    assert inspection.status == ManagedProfileIdentityStatus.MISMATCH
    with pytest.raises(ManagedProfileIdentityError) as failed:
        resolve_managed_profile_identity(
            db_path=db_path,
            profiles_root=root,
            profile_dir=profile,
        )
    assert failed.value.status == ManagedProfileIdentityStatus.MISMATCH


def test_concurrent_resolver_first_use_publishes_one_binding(tmp_path: Path) -> None:
    """並發正式 resolver 只能建立一個 marker 與同 UUID durable binding。"""

    db_path = tmp_path / "app.db"
    root = tmp_path / "profiles"
    profile = root / "default"

    with ThreadPoolExecutor(max_workers=4) as executor:
        identities = tuple(
            executor.map(
                lambda _: resolve_managed_profile_identity(
                    db_path=db_path,
                    profiles_root=root,
                    profile_dir=profile,
                ),
                range(4),
            )
        )

    assert len({identity.marker_uuid for identity in identities}) == 1
    with SqliteApplicationContext(db_path) as app:
        binding = app.repositories.managed_profile_identity.get()
        assert binding is not None
        assert binding.marker_uuid == identities[0].marker_uuid
