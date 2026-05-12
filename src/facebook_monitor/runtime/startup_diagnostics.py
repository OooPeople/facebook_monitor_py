"""Startup diagnostics helpers。

職責：建立啟動時 `logs/startup.log` 使用的完整診斷摘要，
讓一般使用者回報問題時能快速確認 data/profile/log/browser 等路徑。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from facebook_monitor.runtime.build_metadata import collect_build_metadata
from facebook_monitor.runtime.logging_setup import LOG_BACKUP_COUNT
from facebook_monitor.runtime.logging_setup import LOG_MAX_BYTES
from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.webapp.assets import ASSET_VERSION


STARTUP_LOG_FILE_NAME = "startup.log"
STARTUP_LOG_MAX_BYTES = LOG_MAX_BYTES
STARTUP_LOG_BACKUP_COUNT = LOG_BACKUP_COUNT


@dataclass(frozen=True)
class StartupDiagnostics:
    """保存 launcher 啟動診斷摘要。"""

    lines: tuple[str, ...]

    def text(self) -> str:
        """回傳可寫入 log 或 console 的文字。"""

        return "\n".join(self.lines)


def build_startup_diagnostics(
    *,
    paths: RuntimePaths,
    host: str,
    port: int,
    url: str,
    open_browser: bool,
    scheduler_interval_seconds: float,
    reset_runtime_data_on_startup: bool,
    access_log: bool,
    auto_port: bool = False,
    resource_lock_paths: tuple[Path, ...] = (),
    reset_targets_on_startup: bool = True,
    resume_active_targets_on_startup: bool = False,
) -> StartupDiagnostics:
    """建立啟動診斷摘要。"""

    mode = "frozen" if paths.project_root is None else "development"
    metadata = collect_build_metadata(asset_version=ASSET_VERSION)
    return StartupDiagnostics(
        lines=(
            metadata.app_name,
            f"Version: {metadata.app_version}",
            f"Asset version: {metadata.asset_version}",
            f"Python version: {metadata.python_version}",
            f"Executable: {metadata.executable}",
            f"Frozen: {str(metadata.frozen).lower()}",
            f"Packaging mode: {metadata.packaging_mode}",
            f"Build date: {metadata.build_date}",
            f"Git commit: {metadata.git_commit}",
            f"Mode: {mode}",
            f"URL: {url}",
            f"Host: {host}",
            f"Port: {port}",
            f"Auto port: {str(auto_port).lower()}",
            f"Data dir: {paths.data_dir}",
            f"DB path: {paths.db_path}",
            f"Profile dir: {paths.profile_dir}",
            f"Logs dir: {paths.logs_dir}",
            f"Runtime dir: {paths.runtime_dir}",
            "Resource lock paths: "
            + (
                ", ".join(str(lock_path) for lock_path in resource_lock_paths)
                if resource_lock_paths
                else "(not acquired)"
            ),
            f"Templates dir: {paths.templates_dir}",
            f"Static dir: {paths.static_dir}",
            "Browser mode: playwright_chromium",
            "Auto-start scheduler: true",
            f"Scheduler interval seconds: {scheduler_interval_seconds}",
            f"Reset targets on startup: {str(reset_targets_on_startup).lower()}",
            f"Resume active targets on startup: {str(resume_active_targets_on_startup).lower()}",
            f"Reset runtime data on startup: {str(reset_runtime_data_on_startup).lower()}",
            f"Access log: {str(access_log).lower()}",
            f"Open browser: {str(open_browser).lower()}",
        )
    )


def append_startup_log(logs_dir: Path, diagnostics: StartupDiagnostics) -> Path:
    """將啟動診斷追加寫入會自動輪替的 `startup.log`。"""

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / STARTUP_LOG_FILE_NAME
    timestamp = datetime.now(timezone.utc).isoformat()
    message = f"[{timestamp}]\n{diagnostics.text()}\n"
    handler = RotatingFileHandler(
        log_path,
        maxBytes=STARTUP_LOG_MAX_BYTES,
        backupCount=STARTUP_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    try:
        handler.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord(
            name=__name__,
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        handler.handle(record)
    finally:
        handler.close()
    return log_path


def print_diagnostics(lines: Iterable[str]) -> None:
    """逐行輸出完整啟動診斷，供 verbose startup 模式使用。"""

    for line in lines:
        print(line)
