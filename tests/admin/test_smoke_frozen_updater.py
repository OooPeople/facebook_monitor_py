"""Frozen updater smoke script tests。"""

from __future__ import annotations

import hashlib
from pathlib import Path
import plistlib
import subprocess

from facebook_monitor.updates.handoff import load_pending_update
from facebook_monitor.updates.manifest import release_manifest_asset_name
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_INFO_PLIST
from facebook_monitor.versioning import parse_version
from scripts.admin import smoke_frozen_updater
from tests.helpers.macos_bundle import write_macos_app_bundle


def test_validate_smoke_root_rejects_repo_root() -> None:
    """smoke root 不可指向 repo root，避免 rmtree 刪掉整個 checkout。"""

    try:
        smoke_frozen_updater._validate_smoke_root(smoke_frozen_updater.ROOT)
    except ValueError:
        return
    raise AssertionError("expected repo root smoke directory to be rejected")


def test_validate_smoke_root_rejects_build_root() -> None:
    """smoke root 不可直接指向 build root，只能是其下的工作目錄。"""

    try:
        smoke_frozen_updater._validate_smoke_root(
            (smoke_frozen_updater.ROOT / "build").resolve()
        )
    except ValueError:
        return
    raise AssertionError("expected build root smoke directory to be rejected")


def test_validate_smoke_root_accepts_build_child() -> None:
    """預設 build/updater_smoke 這類子目錄可以作為 smoke workspace。"""

    smoke_frozen_updater._validate_smoke_root(
        (smoke_frozen_updater.ROOT / "build" / "updater_smoke").resolve()
    )


def test_validate_built_app_accepts_macos_onedir_layout(tmp_path: Path) -> None:
    """frozen updater smoke 可辨識 macOS Apple Silicon onedir。"""

    built_app = tmp_path / "dist" / "facebook-monitor"
    browser = built_app / "_internal" / "browser" / "Google Chrome for Testing.app"
    browser_exe = browser / "Contents" / "MacOS" / "Google Chrome for Testing"
    for relative in (
        "facebook-monitor",
        "facebook-monitor-updater",
        "_internal/python",
    ):
        path = built_app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    write_macos_app_bundle(built_app)
    browser_exe.parent.mkdir(parents=True, exist_ok=True)
    browser_exe.write_text("chromium", encoding="utf-8")

    policy = smoke_frozen_updater._validate_built_app(built_app)

    assert policy.platform_key == "macos-arm64"


def test_next_smoke_update_version_is_newer_than_current_app() -> None:
    """smoke 版本號必須自動大於目前 frozen app，避免 release smoke 需雙 build。"""

    stable_smoke = smoke_frozen_updater._next_smoke_update_version("0.4.1")
    rc_smoke = smoke_frozen_updater._next_smoke_update_version("0.4.1-rc2")

    assert parse_version(stable_smoke).sort_key() > parse_version("0.4.1").sort_key()
    assert parse_version(rc_smoke).sort_key() > parse_version("0.4.1-rc2").sort_key()
    assert rc_smoke == "0.4.1"


def test_patch_smoke_app_version_updates_macos_info_plist(tmp_path: Path) -> None:
    """macOS smoke artifact 內的 `.app` 版本需對齊臨時 smoke update version。"""

    built_app = tmp_path / "dist" / "facebook-monitor"
    for relative in (
        "facebook-monitor",
        "facebook-monitor-updater",
        "_internal/python",
    ):
        path = built_app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    browser_exe = (
        built_app
        / "_internal"
        / "browser"
        / "Google Chrome for Testing.app"
        / "Contents"
        / "MacOS"
        / "Google Chrome for Testing"
    )
    browser_exe.parent.mkdir(parents=True, exist_ok=True)
    browser_exe.write_text("chromium", encoding="utf-8")
    write_macos_app_bundle(built_app, version="0.4.1")
    policy = smoke_frozen_updater._validate_built_app(built_app)

    smoke_frozen_updater._patch_smoke_app_version(
        built_app,
        layout_policy=policy,
        version="0.4.2",
    )

    plist = plistlib.loads((built_app / MACOS_APP_BUNDLE_INFO_PLIST).read_bytes())
    assert plist["CFBundleShortVersionString"] == "0.4.2"
    assert plist["CFBundleVersion"] == "0.4.2"


def test_run_smoke_reports_timeout(tmp_path: Path, monkeypatch) -> None:
    """frozen updater process 卡住時，smoke 應回結構化 timeout 而不是無限等待。"""

    built_app = tmp_path / "dist" / "facebook-monitor"
    for relative in (
        "facebook-monitor.exe",
        "facebook-monitor-updater.exe",
        "_internal/python313.dll",
        "_internal/browser/chrome.exe",
        "_internal/assets/facebook-monitor.ico",
        "_internal/assets/facebook-monitor-tray.ico",
    ):
        path = built_app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=kwargs.get("args", args[0] if args else "updater"),
            timeout=1,
            output="out",
            stderr="err",
        )

    def fake_write_smoke_manifest(**kwargs):
        updates_dir = kwargs["updates_dir"]
        manifest_path = updates_dir / release_manifest_asset_name(kwargs["version"])
        signature_path = manifest_path.with_name(manifest_path.name + ".sig")
        manifest_path.write_text("{}", encoding="utf-8")
        signature_path.write_text("signature", encoding="ascii")
        manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        return manifest_path, signature_path, manifest_sha256, "release-ed25519-2026q2"

    monkeypatch.setattr(smoke_frozen_updater.subprocess, "run", fake_run)
    monkeypatch.setattr(
        smoke_frozen_updater,
        "_write_smoke_manifest",
        fake_write_smoke_manifest,
    )

    result = smoke_frozen_updater.run_smoke(
        built_app=built_app,
        smoke_root=(smoke_frozen_updater.ROOT / "build" / "timeout_test").resolve(),
        timeout_seconds=1,
    )

    assert not result["ok"]
    assert result["timed_out"]
    assert result["stdout"] == "out"
    assert result["stderr"] == "err"
    pending = load_pending_update(
        smoke_frozen_updater.ROOT
        / "build"
        / "timeout_test"
        / "installed-app"
        / "data"
        / "runtime"
        / "pending_update.json"
    )
    assert parse_version(pending.version).sort_key() > parse_version(
        smoke_frozen_updater.APP_VERSION
    ).sort_key()
    assert pending.zip_path.parent.parent.name == pending.version
    assert pending.zip_path.parent.name == "attempt-smoke"
    assert pending.manifest_path is not None
    assert pending.manifest_path.name == release_manifest_asset_name(pending.version)
