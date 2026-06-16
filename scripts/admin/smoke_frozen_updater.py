"""Admin smoke：用 frozen onedir build 驗證 updater 可替換 app files。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.core.defaults import PYTHON_UPDATER_RUNTIME_DEFAULTS
from facebook_monitor.updates.artifacts import release_sha256_asset_name
from facebook_monitor.updates.artifacts import update_artifact_policy_for_key
from facebook_monitor.updates.checksum import calculate_sha256
from facebook_monitor.updates.checksum import render_sha256_sidecar
from facebook_monitor.updates.download import VERIFIED_DOWNLOAD_SET_MARKER_NAME
from facebook_monitor.updates.download import VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION
from facebook_monitor.updates.manifest import release_manifest_asset_name
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_INFO_PLIST
from facebook_monitor.updates.platforms import UpdaterLayoutPolicy
from facebook_monitor.updates.platforms import detect_layout_policy
from facebook_monitor.updates.platforms import missing_required_paths
from facebook_monitor.updates.release_check import DEFAULT_UPDATE_REPOSITORY
from facebook_monitor.updates.validation import has_posix_executable_bit
from facebook_monitor.version import APP_VERSION
from facebook_monitor.versioning import parse_version
from scripts.admin._release_build import DEFAULT_KEY_ID
from scripts.admin._release_build import DEFAULT_PRIVATE_KEY_FILE
from scripts.admin.create_release_manifest import create_release_manifest
from scripts.admin.sign_release_manifest import PRIVATE_KEY_ENV
from scripts.admin.sign_release_manifest import sign_release_manifest

APP_DIR_NAME = "facebook-monitor"


def parse_args() -> argparse.Namespace:
    """解析 CLI 參數。"""

    parser = argparse.ArgumentParser(
        description="Smoke test frozen facebook-monitor-updater."
    )
    parser.add_argument(
        "--built-app",
        type=Path,
        default=ROOT / "dist" / APP_DIR_NAME,
        help="Path to the frozen onedir app folder.",
    )
    parser.add_argument(
        "--smoke-root",
        type=Path,
        default=ROOT / "build" / "updater_smoke",
        help="Temporary smoke workspace. It will be deleted before running.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=PYTHON_UPDATER_RUNTIME_DEFAULTS.timeout_seconds,
        help="Maximum seconds to wait for the frozen updater process.",
    )
    parser.add_argument(
        "--private-key-file",
        type=Path,
        default=None,
        help="Ed25519 private key file. Defaults to repo-external local signing path when present.",
    )
    parser.add_argument(
        "--private-key-b64",
        default="",
        help="Base64 raw Ed25519 private key used to sign the smoke manifest.",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint。"""

    args = parse_args()
    try:
        result = run_smoke(
            built_app=args.built_app.resolve(),
            smoke_root=args.smoke_root.resolve(),
            timeout_seconds=float(args.timeout_seconds),
            private_key_file=args.private_key_file,
            private_key_b64=str(args.private_key_b64),
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


def run_smoke(
    *,
    built_app: Path,
    smoke_root: Path,
    timeout_seconds: float = PYTHON_UPDATER_RUNTIME_DEFAULTS.timeout_seconds,
    private_key_file: Path | None = None,
    private_key_b64: str = "",
) -> dict[str, object]:
    """執行 frozen updater smoke 並回傳結構化結果。"""

    _validate_smoke_root(smoke_root)
    layout_policy = _validate_built_app(built_app)
    if smoke_root.exists():
        shutil.rmtree(smoke_root)
    smoke_root.mkdir(parents=True)

    old_app = smoke_root / "installed-app"
    new_app = smoke_root / "new-app" / APP_DIR_NAME
    shutil.copytree(built_app, old_app, symlinks=True)
    shutil.copytree(built_app, new_app, symlinks=True)
    (old_app / "updater-smoke-marker.txt").write_text(
        "old-app-files",
        encoding="utf-8",
    )
    (new_app / "updater-smoke-marker.txt").write_text(
        "new-app-files",
        encoding="utf-8",
    )

    smoke_update_version = _next_smoke_update_version(APP_VERSION)
    _patch_smoke_app_version(
        new_app,
        layout_policy=layout_policy,
        version=smoke_update_version,
    )
    data_dir = old_app / "data"
    runtime_dir = data_dir / "runtime"
    logs_dir = data_dir / "logs"
    updates_dir = data_dir / "updates" / smoke_update_version
    artifact_set_dir = updates_dir / "attempt-smoke"
    profile_dir = data_dir / "profiles" / "automation_default"
    for directory in (runtime_dir, logs_dir, artifact_set_dir, profile_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (data_dir / "app.db").write_text("smoke-user-db", encoding="utf-8")
    (profile_dir / "profile-marker.txt").write_text("smoke-profile", encoding="utf-8")

    artifact_policy = update_artifact_policy_for_key(layout_policy.platform_key)
    zip_path = artifact_set_dir / artifact_policy.asset_name(smoke_update_version)
    _write_app_zip(new_app, zip_path)
    digest = calculate_sha256(zip_path)
    sha256_path = zip_path.with_name(release_sha256_asset_name(zip_path.name))
    sha256_path.write_text(
        render_sha256_sidecar(digest, zip_path.name),
        encoding="ascii",
    )
    manifest_path, manifest_signature_path, manifest_sha256, manifest_key_id = (
        _write_smoke_manifest(
            updates_dir=artifact_set_dir,
            zip_path=zip_path,
            version=smoke_update_version,
            platform_key=artifact_policy.platform_key,
            private_key_file=private_key_file,
            private_key_b64=private_key_b64,
        )
    )
    _write_smoke_verified_download_marker(
        artifact_set_dir=artifact_set_dir,
        zip_path=zip_path,
        sha256_path=sha256_path,
        manifest_path=manifest_path,
        manifest_signature_path=manifest_signature_path,
        manifest_sha256=manifest_sha256,
        manifest_key_id=manifest_key_id,
        digest=digest,
    )
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": smoke_update_version,
                "repository": DEFAULT_UPDATE_REPOSITORY,
                "asset_name": zip_path.name,
                "zip_path": str(zip_path),
                "expected_sha256": digest,
                "actual_sha256": digest,
                "app_base_dir": str(old_app),
                "data_dir": str(data_dir),
                "db_path": str(data_dir / "app.db"),
                "profile_dir": str(profile_dir),
                "logs_dir": str(logs_dir),
                "runtime_dir": str(runtime_dir),
                "created_at": "2026-05-17T00:00:00+00:00",
                "manifest_path": str(manifest_path),
                "manifest_signature_path": str(manifest_signature_path),
                "manifest_sha256": manifest_sha256,
                "manifest_key_id": manifest_key_id,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp_updater_dir = smoke_root / "temp-updater"
    temp_updater_dir.mkdir()
    updater_entry = layout_policy.updater_entry(old_app)
    temp_updater = temp_updater_dir / layout_policy.updater_entry_name
    shutil.copy2(updater_entry, temp_updater)
    for dirname in layout_policy.temp_copy_dirs:
        source = old_app / dirname
        if source.exists():
            shutil.copytree(source, temp_updater_dir / dirname, symlinks=True)
    command = [
        str(temp_updater),
        "--data-dir",
        str(data_dir),
        "--pending-update",
        str(pending_path),
        "--wait-seconds",
        "0",
    ]
    try:
        process = subprocess.run(
            command,
            cwd=temp_updater_dir,
            check=False,
            creationflags=_hidden_creation_flags(),
            capture_output=True,
            text=True,
            timeout=max(1.0, timeout_seconds),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "timeout_seconds": timeout_seconds,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "old_app": str(old_app),
        }

    marker = (old_app / "updater-smoke-marker.txt").read_text(encoding="utf-8").strip()
    db_text = (data_dir / "app.db").read_text(encoding="utf-8").strip()
    profile_text = (profile_dir / "profile-marker.txt").read_text(
        encoding="utf-8"
    ).strip()
    log_path = logs_dir / "updater.log"
    updater_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    executable_checks = _post_update_executable_checks(old_app, layout_policy)
    ok = (
        process.returncode == 0
        and marker == "new-app-files"
        and db_text == "smoke-user-db"
        and profile_text == "smoke-profile"
        and "status=applied applied=true message=updated" in updater_log
        and not pending_path.exists()
        and not zip_path.exists()
        and not zip_path.with_name(release_sha256_asset_name(zip_path.name)).exists()
        and not manifest_path.exists()
        and not manifest_signature_path.exists()
        and all(executable_checks.values())
    )
    return {
        "ok": ok,
        "timed_out": False,
        "exit_code": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
        "marker": marker,
        "data_preserved": db_text == "smoke-user-db",
        "profile_preserved": profile_text == "smoke-profile",
        "pending_removed": not pending_path.exists(),
        "zip_removed": not zip_path.exists(),
        "sha256_removed": not zip_path.with_name(
            release_sha256_asset_name(zip_path.name)
        ).exists(),
        "manifest_removed": not manifest_path.exists(),
        "manifest_signature_removed": not manifest_signature_path.exists(),
        "updater_log_contains_applied": (
            "status=applied applied=true message=updated" in updater_log
        ),
        "executable_checks": executable_checks,
        "old_app": str(old_app),
        "smoke_update_version": smoke_update_version,
    }


def _next_smoke_update_version(current_version: str) -> str:
    """產生只供 smoke 使用、且必定比目前 app 新的版本號。"""

    parsed = parse_version(current_version)
    parts = list(parsed.release)
    while len(parts) < 3:
        parts.append(0)
    if parsed.prerelease_label:
        return ".".join(str(part) for part in parts)
    parts[-1] += 1
    return ".".join(str(part) for part in parts)


def _patch_smoke_app_version(
    app_root: Path,
    *,
    layout_policy: UpdaterLayoutPolicy,
    version: str,
) -> None:
    """讓 smoke update artifact 的 macOS bundle version 與 pending version 對齊。"""

    if layout_policy.platform_key != "macos-arm64":
        return
    plist_path = app_root / MACOS_APP_BUNDLE_INFO_PLIST
    value = plistlib.loads(plist_path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("smoke_macos_info_plist_invalid")
    value["CFBundleShortVersionString"] = version
    value["CFBundleVersion"] = version
    plist_path.write_bytes(plistlib.dumps(value, sort_keys=True))


def _write_smoke_manifest(
    *,
    updates_dir: Path,
    zip_path: Path,
    version: str,
    platform_key: str,
    private_key_file: Path | None,
    private_key_b64: str,
) -> tuple[Path, Path, str, str]:
    """建立 updater smoke 使用的 signed manifest 與 detached signature。"""

    manifest_path = updates_dir / release_manifest_asset_name(version)
    create_release_manifest(
        version=version,
        repository=DEFAULT_UPDATE_REPOSITORY,
        key_id=DEFAULT_KEY_ID,
        asset_specs=[f"{platform_key}={zip_path}"],
        output=manifest_path,
        force=True,
    )
    resolved_private_key_file = private_key_file
    if (
        resolved_private_key_file is None
        and not private_key_b64.strip()
        and not os.environ.get(PRIVATE_KEY_ENV, "").strip()
        and DEFAULT_PRIVATE_KEY_FILE.is_file()
    ):
        resolved_private_key_file = DEFAULT_PRIVATE_KEY_FILE
    signature_path = sign_release_manifest(
        manifest_path=manifest_path,
        private_key_b64=private_key_b64,
        private_key_file=resolved_private_key_file,
        force=True,
    )
    return manifest_path, signature_path, calculate_sha256(manifest_path), DEFAULT_KEY_ID


def _write_smoke_verified_download_marker(
    *,
    artifact_set_dir: Path,
    zip_path: Path,
    sha256_path: Path,
    manifest_path: Path,
    manifest_signature_path: Path,
    manifest_sha256: str,
    manifest_key_id: str,
    digest: str,
) -> None:
    """寫出 strict updater handoff 需要的 verified download set marker。"""

    marker_path = artifact_set_dir / VERIFIED_DOWNLOAD_SET_MARKER_NAME
    marker_path.write_text(
        json.dumps(
            {
                "schema_version": VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION,
                "asset_name": zip_path.name,
                "asset_sha256": digest,
                "asset_size": zip_path.stat().st_size,
                "sha256_name": sha256_path.name,
                "sha256_sha256": calculate_sha256(sha256_path),
                "manifest_name": manifest_path.name,
                "manifest_sha256": manifest_sha256,
                "manifest_key_id": manifest_key_id,
                "manifest_signature_name": manifest_signature_path.name,
                "manifest_signature_sha256": calculate_sha256(manifest_signature_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _validate_smoke_root(smoke_root: Path) -> None:
    """避免清理目錄逃出 repo。"""

    allowed_root = (ROOT / "build").resolve()
    if smoke_root == allowed_root or not smoke_root.is_relative_to(allowed_root):
        raise ValueError(f"smoke root escaped repo: {smoke_root}")


def _validate_built_app(built_app: Path) -> UpdaterLayoutPolicy:
    """確認 frozen onedir build 有 updater smoke 必要檔案。"""

    layout_policy = detect_layout_policy(built_app)
    missing = missing_required_paths(
        built_app,
        required_paths=layout_policy.required_staging_files,
        any_groups=layout_policy.required_staging_any_groups,
    )
    if missing:
        raise ValueError(f"missing built file: {missing[0]}")
    return layout_policy


def _write_app_zip(app_root: Path, zip_path: Path) -> None:
    """建立 updater 測試用 portable zip。"""

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in app_root.rglob("*"):
            if path.is_symlink():
                info = zipfile.ZipInfo((Path(APP_DIR_NAME) / path.relative_to(app_root)).as_posix())
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, path.readlink().as_posix())
            elif path.is_file():
                archive.write(path, Path(APP_DIR_NAME) / path.relative_to(app_root))


def _post_update_executable_checks(
    app_root: Path,
    layout_policy: UpdaterLayoutPolicy,
) -> dict[str, bool]:
    """macOS smoke 套用後確認必要 executable bit 仍存在。"""

    if layout_policy.platform_key != "macos-arm64":
        return {}
    checks: dict[str, bool] = {}
    executable_paths = [layout_policy.app_entry_name, layout_policy.updater_entry_name]
    if layout_policy.restart_entry_name:
        executable_paths.append(layout_policy.restart_entry_name)
    for relative_path in executable_paths:
        checks[relative_path] = has_posix_executable_bit(app_root / relative_path)
    for group in layout_policy.required_staging_any_groups:
        for relative_path in group:
            path = app_root / relative_path
            if path.is_file():
                checks[relative_path] = has_posix_executable_bit(path)
                break
    return checks


def _hidden_creation_flags() -> int:
    """Windows 下隱藏 updater smoke process 視窗。"""

    if sys.platform != "win32":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


if __name__ == "__main__":
    raise SystemExit(main())
