"""啟動獨立 updater process。

職責：從 frozen app 目錄找到 `facebook-monitor-updater.exe`，複製到 temp
後以 detached process 執行，讓原 app 目錄可在主程式退出後被替換。
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.updates.handoff import PendingUpdate
from facebook_monitor.updates.handoff import pending_update_path
from facebook_monitor.updates.platforms import WINDOWS_APP_ENTRY
from facebook_monitor.updates.platforms import WINDOWS_UPDATER_ENTRY
from facebook_monitor.updates.platforms import detect_layout_policy
from facebook_monitor.updates.platforms import layout_policy_for_updater_path
from facebook_monitor.updates.platforms import supported_layout_policies


UPDATER_EXE_NAME = WINDOWS_UPDATER_ENTRY
APP_EXE_NAME = WINDOWS_APP_ENTRY
TEMP_UPDATER_MAX_AGE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class UpdaterLaunchResult:
    """獨立 updater process 啟動結果。"""

    launched: bool
    status: str
    message: str
    updater_path: Path | None = None
    pid: int | None = None


@dataclass(frozen=True)
class AppRestartResult:
    """新版 app 重啟結果。"""

    launched: bool
    status: str
    message: str
    pid: int | None = None


def launch_temp_updater(
    *,
    paths: RuntimePaths,
    wait_seconds: int = 300,
    restart: bool = True,
) -> UpdaterLaunchResult:
    """複製 updater 到 temp 並啟動，讓它等待主程式退出後套用更新。"""

    source = find_bundled_updater(paths.app_base_dir)
    if source is None:
        return UpdaterLaunchResult(
            launched=False,
            status="updater_missing",
            message="bundled updater not found",
        )
    pending_path = pending_update_path(paths.runtime_dir)
    if not pending_path.is_file():
        return UpdaterLaunchResult(
            launched=False,
            status="pending_update_missing",
            message=str(pending_path),
        )
    try:
        temp_updater = copy_updater_to_temp(source, paths.runtime_dir)
    except (OSError, ValueError) as exc:
        return UpdaterLaunchResult(
            launched=False,
            status="launch_failed",
            message=str(exc),
        )
    command = [
        str(temp_updater),
        "--pending-update",
        str(pending_path),
        "--data-dir",
        str(paths.data_dir),
        "--wait-seconds",
        str(wait_seconds),
    ]
    if restart:
        command.append("--restart")
    try:
        process = _popen_detached(command, cwd=temp_updater.parent)
    except OSError as exc:
        return UpdaterLaunchResult(
            launched=False,
            status="launch_failed",
            message=str(exc),
            updater_path=temp_updater,
        )
    return UpdaterLaunchResult(
        launched=True,
        status="launched",
        message="updater launched",
        updater_path=temp_updater,
        pid=process.pid,
    )


def find_bundled_updater(app_base_dir: Path) -> Path | None:
    """尋找 frozen onedir 旁的 updater。"""

    for policy in supported_layout_policies():
        candidate = policy.updater_entry(app_base_dir)
        if candidate.is_file():
            return candidate.resolve()
    return None


def copy_updater_to_temp(source: Path, runtime_dir: Path) -> Path:
    """複製 updater onedir runtime 到 temp，避免 updater 鎖住 app base dir。"""

    root = temp_updater_root()
    cleanup_old_temp_updaters(root)
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    runtime_hash = hashlib.sha256(str(runtime_dir.resolve()).encode("utf-8")).hexdigest()[:12]
    temp_dir = Path(
        tempfile.mkdtemp(
            prefix=f"{timestamp}-{runtime_hash}-",
            dir=str(root),
        )
    )
    layout_policy = layout_policy_for_updater_path(source)
    destination = temp_dir / layout_policy.updater_entry_name
    shutil.copy2(source, destination)
    for directory_name in layout_policy.temp_copy_dirs:
        source_dir = source.parent / directory_name
        if not source_dir.is_dir():
            raise ValueError(f"updater_runtime_dir_missing:{directory_name}")
        shutil.copytree(source_dir, temp_dir / directory_name)
    return destination


def temp_updater_root() -> Path:
    """回傳 temp updater runtime copy 的根目錄。"""

    return Path(tempfile.gettempdir()) / "facebook-monitor" / "updater"


def cleanup_old_temp_updaters(
    root: Path,
    *,
    max_age_seconds: int = TEMP_UPDATER_MAX_AGE_SECONDS,
) -> None:
    """清除過舊 temp updater runtime copy；清理失敗不影響本次更新。"""

    cutoff = time.time() - max_age_seconds
    if not root.exists():
        return
    with suppress(OSError):
        for child in root.iterdir():
            if not child.is_dir():
                continue
            with suppress(OSError):
                if child.stat().st_mtime < cutoff:
                    shutil.rmtree(child)


def launch_restarted_app(pending: PendingUpdate) -> AppRestartResult:
    """套用更新後啟動新版 app，並保留原 runtime path 覆寫。"""

    layout_policy = detect_layout_policy(pending.app_base_dir)
    executable = layout_policy.app_entry(pending.app_base_dir)
    if not executable.is_file():
        return AppRestartResult(
            launched=False,
            status="app_exe_missing",
            message=str(executable),
        )
    command = [
        str(executable),
        "--data-dir",
        str(pending.data_dir),
        "--db-path",
        str(pending.db_path),
        "--profile-dir",
        str(pending.profile_dir),
        "--logs-dir",
        str(pending.logs_dir),
    ]
    try:
        process = _popen_detached(command, cwd=pending.app_base_dir)
    except OSError as exc:
        return AppRestartResult(
            launched=False,
            status="restart_failed",
            message=str(exc),
        )
    return AppRestartResult(
        launched=True,
        status="launched",
        message="app launched",
        pid=process.pid,
    )


def _popen_detached(command: list[str], *, cwd: Path) -> subprocess.Popen[Any]:
    """以平台適合的 detached 方式啟動 process。"""

    if sys.platform != "win32":
        return subprocess.Popen(  # noqa: S603
            command,
            close_fds=True,
            cwd=str(cwd),
            start_new_session=True,
        )
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
        getattr(subprocess, "DETACHED_PROCESS", 0)
    )
    return subprocess.Popen(  # noqa: S603
        command,
        close_fds=True,
        creationflags=creationflags,
        cwd=str(cwd),
    )
