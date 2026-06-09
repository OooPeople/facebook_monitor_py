"""Windows frozen app integration helpers。

職責：隔離 Windows GUI subsystem / system tray / uvicorn runner glue，
讓正式 launcher 保持產品啟動流程的 orchestration。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Any

import uvicorn


@dataclass(frozen=True)
class WindowsTrayDecision:
    """描述本次啟動是否啟用 Windows tray。"""

    enabled: bool
    warning: str | None = None


def ensure_standard_streams_for_gui_subsystem() -> None:
    """Windows GUI EXE 沒有 console 時，補上 devnull stream 供 logging 使用。"""

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def resolve_windows_tray_decision(cli_value: bool | None) -> WindowsTrayDecision:
    """依 CLI、平台與 frozen 狀態決定是否啟用 Windows tray。"""

    enabled = (
        cli_value
        if cli_value is not None
        else sys.platform == "win32" and bool(getattr(sys, "frozen", False))
    )
    if enabled and sys.platform != "win32":
        return WindowsTrayDecision(
            enabled=False,
            warning="--windows-tray is only supported on Windows; continuing without tray.",
        )
    return WindowsTrayDecision(enabled=enabled)


def find_windows_tray_icon(paths: object) -> Path | None:
    """尋找 PyInstaller data 或 source tree 內的 tray icon 檔案。"""

    app_base_dir = getattr(paths, "app_base_dir", None)
    project_root = getattr(paths, "project_root", None)
    candidates: list[Path] = []
    if isinstance(app_base_dir, Path):
        candidates.extend(
            [
                app_base_dir / "assets" / "facebook-monitor-tray.ico",
                app_base_dir / "_internal" / "assets" / "facebook-monitor-tray.ico",
                app_base_dir / "assets" / "facebook-monitor.ico",
                app_base_dir / "_internal" / "assets" / "facebook-monitor.ico",
            ]
        )
    if isinstance(project_root, Path):
        candidates.extend(
            [
                project_root / "packaging" / "assets" / "facebook-monitor-tray.ico",
                project_root / "packaging" / "assets" / "facebook-monitor.ico",
            ]
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def find_windows_notification_icon() -> Path | None:
    """尋找 desktop notification 可使用的 bundled / source icon。"""

    candidates: list[Path] = []
    executable_parent = Path(sys.executable).resolve().parent
    candidates.extend(_windows_icon_candidates(executable_parent))
    pyinstaller_base = getattr(sys, "_MEIPASS", "")
    if pyinstaller_base:
        candidates.extend(_windows_icon_candidates(Path(str(pyinstaller_base)).resolve()))
    source_root = Path(__file__).resolve().parents[3]
    candidates.extend(
        [
            source_root / "packaging" / "assets" / "facebook-monitor-tray.ico",
            source_root / "packaging" / "assets" / "facebook-monitor.ico",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _windows_icon_candidates(base_dir: Path) -> list[Path]:
    """回傳 Windows onedir 可能放置 icon asset 的路徑。"""

    return [
        base_dir / "assets" / "facebook-monitor-tray.ico",
        base_dir / "_internal" / "assets" / "facebook-monitor-tray.ico",
        base_dir / "assets" / "facebook-monitor.ico",
        base_dir / "_internal" / "assets" / "facebook-monitor.ico",
    ]


def run_uvicorn_with_windows_tray(
    app: Any,
    *,
    url: str,
    icon_path: Path | None,
    uvicorn_kwargs: dict[str, Any],
    configure_server: Callable[[uvicorn.Server], None] | None = None,
) -> None:
    """以可由 system tray 觸發關閉的方式執行 uvicorn server。"""

    from facebook_monitor.runtime.windows_tray import start_windows_tray_icon

    config = uvicorn.Config(app, **uvicorn_kwargs)
    server = uvicorn.Server(config)
    if configure_server is not None:
        configure_server(server)
    tray_icon = start_windows_tray_icon(
        url=url,
        icon_path=icon_path,
        on_exit=lambda: setattr(server, "should_exit", True),
    )
    try:
        try:
            server.run()
        except KeyboardInterrupt:
            return
    finally:
        tray_icon.stop()
