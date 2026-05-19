"""Admin smoke：用 frozen onedir build 驗證 updater 可替換 app files。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
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
from facebook_monitor.updates.platforms import UpdaterLayoutPolicy
from facebook_monitor.updates.platforms import detect_layout_policy
from facebook_monitor.updates.platforms import missing_required_paths

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
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint。"""

    args = parse_args()
    try:
        result = run_smoke(
            built_app=args.built_app.resolve(),
            smoke_root=args.smoke_root.resolve(),
            timeout_seconds=float(args.timeout_seconds),
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
) -> dict[str, object]:
    """執行 frozen updater smoke 並回傳結構化結果。"""

    _validate_smoke_root(smoke_root)
    layout_policy = _validate_built_app(built_app)
    if smoke_root.exists():
        shutil.rmtree(smoke_root)
    smoke_root.mkdir(parents=True)

    old_app = smoke_root / "installed-app"
    new_app = smoke_root / "new-app" / APP_DIR_NAME
    shutil.copytree(built_app, old_app)
    shutil.copytree(built_app, new_app)
    (old_app / "updater-smoke-marker.txt").write_text(
        "old-app-files",
        encoding="utf-8",
    )
    (new_app / "updater-smoke-marker.txt").write_text(
        "new-app-files",
        encoding="utf-8",
    )

    data_dir = old_app / "data"
    runtime_dir = data_dir / "runtime"
    logs_dir = data_dir / "logs"
    updates_dir = data_dir / "updates" / "0.2.0-smoke"
    profile_dir = data_dir / "profiles" / "automation_default"
    for directory in (runtime_dir, logs_dir, updates_dir, profile_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (data_dir / "app.db").write_text("smoke-user-db", encoding="utf-8")
    (profile_dir / "profile-marker.txt").write_text("smoke-profile", encoding="utf-8")

    zip_path = updates_dir / f"facebook-monitor-0.2.0-smoke-{layout_policy.platform_key}.zip"
    _write_app_zip(new_app, zip_path)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}\n",
        encoding="ascii",
    )
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "0.2.0-smoke",
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
            shutil.copytree(source, temp_updater_dir / dirname)
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
    ok = (
        process.returncode == 0
        and marker == "new-app-files"
        and db_text == "smoke-user-db"
        and profile_text == "smoke-profile"
        and "status=applied applied=true message=updated" in updater_log
        and not pending_path.exists()
        and not zip_path.exists()
        and not zip_path.with_name(zip_path.name + ".sha256").exists()
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
        "sha256_removed": not zip_path.with_name(zip_path.name + ".sha256").exists(),
        "updater_log_contains_applied": (
            "status=applied applied=true message=updated" in updater_log
        ),
        "old_app": str(old_app),
    }


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
            if path.is_file():
                archive.write(path, Path(APP_DIR_NAME) / path.relative_to(app_root))


def _hidden_creation_flags() -> int:
    """Windows 下隱藏 updater smoke process 視窗。"""

    if sys.platform != "win32":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


if __name__ == "__main__":
    raise SystemExit(main())
