"""獨立 updater 套用流程測試。"""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import shutil

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from facebook_monitor.updates.apply import apply_pending_update


from tests.updates.apply_test_helpers import make_app_root
from tests.updates.apply_test_helpers import make_update_zip
from tests.updates.apply_test_helpers import write_signed_manifest_for_pending
from tests.updates.apply_test_helpers import pending_update

TEST_KEY_ID = "test-key"
TEST_PRIVATE_KEY = Ed25519PrivateKey.generate()
TEST_REPOSITORY = "OooPeople/facebook_monitor_py"
TEST_VERSION = "0.1.0"


def test_apply_pending_update_rejects_hash_changed_after_handoff(tmp_path: Path) -> None:
    """handoff 後 zip 被替換時，updater 會重算 SHA256 並拒絕套用。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    zip_path.write_bytes(b"changed")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "pending_zip_sha256_mismatch"
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"


def test_apply_pending_update_rejects_manifest_changed_after_handoff(
    tmp_path: Path,
) -> None:
    """handoff 後 manifest 被替換時，updater 會重算 SHA256 並拒絕套用。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    manifest_path = zip_path.with_name("facebook-monitor-0.1.0-manifest.json")
    manifest_path.write_text("original", encoding="utf-8")
    manifest_digest = hashlib.sha256(b"original").hexdigest()
    manifest_path.write_text("changed", encoding="utf-8")
    signature_path = manifest_path.with_suffix(manifest_path.suffix + ".sig")
    signature_path.write_text("sig", encoding="utf-8")

    pending = replace(
        pending_update(tmp_path, zip_path=zip_path, digest=digest),
        manifest_path=manifest_path,
        manifest_signature_path=signature_path,
        manifest_sha256=manifest_digest,
        manifest_key_id="test-key",
    )
    result = apply_pending_update(pending)

    assert result.status == "failed"
    assert result.message == "pending_manifest_sha256_mismatch"
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"


def test_apply_pending_update_rejects_self_consistent_manifest_without_valid_signature(
    tmp_path: Path,
) -> None:
    """zip、pending 與 manifest 被一起改寫但 signature 不符時不可套用。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    original_digest = make_update_zip(zip_path, exe_text="new")
    pending = pending_update(tmp_path, zip_path=zip_path, digest=original_digest)
    assert pending.manifest_signature_path is not None
    original_signature = pending.manifest_signature_path.read_bytes()
    shutil.rmtree(zip_path.parent / "new")
    tampered_digest = make_update_zip(zip_path, exe_text="evil")
    _, manifest_path, signature_path, manifest_digest = write_signed_manifest_for_pending(
        tmp_path=tmp_path,
        zip_path=zip_path,
        digest=tampered_digest,
    )
    signature_path.write_bytes(original_signature)
    tampered_pending = replace(
        pending,
        expected_sha256=tampered_digest,
        actual_sha256=tampered_digest,
        manifest_path=manifest_path,
        manifest_signature_path=signature_path,
        manifest_sha256=manifest_digest,
    )

    result = apply_pending_update(tampered_pending)

    assert result.status == "failed"
    assert result.message == "manifest_signature_invalid"
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"
