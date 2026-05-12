"""Runtime path resolver。

職責：集中本機 app 的可寫資料路徑與 bundled resource 路徑解析，
避免 Web UI、登入工具與未來打包入口各自推導 DB/profile/logs 位置。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys


DEFAULT_PROFILE_NAME = "automation_default"
DEFAULT_HOME_DATA_DIR_NAME = "facebook_monitor_data"


@dataclass(frozen=True)
class RuntimePaths:
    """描述本次程式啟動會使用的所有主要路徑。"""

    project_root: Path | None
    app_base_dir: Path
    data_dir: Path
    db_path: Path
    profiles_dir: Path
    profile_dir: Path
    logs_dir: Path
    runtime_dir: Path
    exports_dir: Path
    templates_dir: Path
    static_dir: Path

    def ensure_writable_dirs(self) -> None:
        """建立啟動時需要存在的可寫目錄。"""

        for directory in (
            self.data_dir,
            self.db_path.parent,
            self.profiles_dir,
            self.profile_dir,
            self.logs_dir,
            self.runtime_dir,
            self.exports_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def add_runtime_path_arguments(parser: argparse.ArgumentParser) -> None:
    """替 CLI parser 加入正式入口共用的 runtime path 參數。"""

    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Writable data directory. Defaults to ~/facebook_monitor_data.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="SQLite DB path. Overrides --data-dir/app.db when provided.",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=None,
        help=(
            "Playwright persistent profile directory under <data-dir>/profiles. "
            "Overrides --profile-name."
        ),
    )
    parser.add_argument(
        "--unsafe-profile-dir",
        type=Path,
        default=None,
        help=(
            "Debug/test only: external Playwright profile directory. "
            "Common browser profiles are rejected."
        ),
    )
    parser.add_argument(
        "--profile-name",
        default=DEFAULT_PROFILE_NAME,
        help="Profile folder name under <data-dir>/profiles.",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=None,
        help="Runtime logs directory. Defaults to <data-dir>/logs.",
    )
    parser.add_argument(
        "--portable",
        action="store_true",
        help="Resolve writable data beside the app base directory.",
    )


def resolve_runtime_paths_from_args(args: argparse.Namespace) -> RuntimePaths:
    """從 CLI namespace 解析 runtime paths。"""

    profile_dir = getattr(args, "profile_dir", None)
    unsafe_profile_dir = getattr(args, "unsafe_profile_dir", None)
    if profile_dir is not None and unsafe_profile_dir is not None:
        raise ValueError("--profile-dir and --unsafe-profile-dir cannot be used together")
    return resolve_runtime_paths(
        data_dir=getattr(args, "data_dir", None),
        db_path=getattr(args, "db_path", None),
        profile_dir=unsafe_profile_dir or profile_dir,
        profile_name=getattr(args, "profile_name", DEFAULT_PROFILE_NAME),
        logs_dir=getattr(args, "logs_dir", None),
        portable=bool(getattr(args, "portable", False)),
        allow_external_profile_dir=unsafe_profile_dir is not None,
    )


def resolve_runtime_paths(
    *,
    data_dir: Path | str | None = None,
    db_path: Path | str | None = None,
    profile_dir: Path | str | None = None,
    profile_name: str = DEFAULT_PROFILE_NAME,
    logs_dir: Path | str | None = None,
    portable: bool = False,
    app_base_dir: Path | None = None,
    allow_external_profile_dir: bool = False,
) -> RuntimePaths:
    """依 CLI 覆寫、portable 模式與 source 模式推導完整 runtime paths。"""

    project_root = _find_project_root()
    resolved_app_base_dir = _resolve_app_base_dir(project_root, app_base_dir)
    explicit_data_dir = data_dir is not None
    resolved_data_dir = _resolve_path(
        data_dir,
        base_dir=resolved_app_base_dir,
        default=_default_data_dir(
            project_root=project_root,
            app_base_dir=resolved_app_base_dir,
            portable=portable,
        ),
    )
    resolved_db_path = _resolve_path(
        db_path,
        base_dir=resolved_app_base_dir,
        default=resolved_data_dir / "app.db",
    )
    profiles_dir = resolved_data_dir / "profiles"
    resolved_profile_dir = _resolve_path(
        profile_dir,
        base_dir=resolved_app_base_dir,
        default=profiles_dir / _normalize_profile_name(profile_name),
    )
    if profile_dir is not None:
        if allow_external_profile_dir:
            _reject_common_browser_profile_dir(resolved_profile_dir)
        elif not resolved_profile_dir.is_relative_to(profiles_dir):
            raise ValueError(
                "--profile-dir must stay under <data-dir>/profiles; "
                "use --unsafe-profile-dir for debug-only external profiles"
            )
    resolved_logs_dir = _resolve_path(
        logs_dir,
        base_dir=resolved_app_base_dir,
        default=_default_logs_dir(
            project_root=project_root,
            data_dir=resolved_data_dir,
            explicit_data_dir=explicit_data_dir,
            portable=portable,
        ),
    )
    package_dir = _resource_package_dir()
    return RuntimePaths(
        project_root=project_root,
        app_base_dir=resolved_app_base_dir,
        data_dir=resolved_data_dir,
        db_path=resolved_db_path,
        profiles_dir=profiles_dir,
        profile_dir=resolved_profile_dir,
        logs_dir=resolved_logs_dir,
        runtime_dir=resolved_data_dir / "runtime",
        exports_dir=resolved_data_dir / "exports",
        templates_dir=package_dir / "webapp" / "templates",
        static_dir=package_dir / "webapp" / "static",
    )


def default_runtime_paths() -> RuntimePaths:
    """回傳未帶 CLI 覆寫時的 source-mode 預設路徑。"""

    return resolve_runtime_paths()


def _resolve_app_base_dir(project_root: Path | None, app_base_dir: Path | None) -> Path:
    """取得 app base dir；future frozen mode 會落在 executable 所在目錄。"""

    if app_base_dir is not None:
        return app_base_dir.expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    if project_root is not None:
        return project_root
    return Path.cwd().resolve()


def _resource_package_dir() -> Path:
    """取得 package resource base，支援 source tree 與 future frozen bundle。"""

    frozen_base = getattr(sys, "_MEIPASS", None)
    if frozen_base:
        base = Path(str(frozen_base)).resolve()
        for candidate in (
            base / "facebook_monitor",
            base / "src" / "facebook_monitor",
            base,
        ):
            if (candidate / "webapp" / "templates").exists() and (
                candidate / "webapp" / "static"
            ).exists():
                return candidate
    return Path(__file__).resolve().parents[1]


def _default_data_dir(*, project_root: Path | None, app_base_dir: Path, portable: bool) -> Path:
    """取得未指定 data-dir 時的可寫資料根目錄。"""

    if portable or project_root is None:
        return app_base_dir / "data"
    return Path.home() / DEFAULT_HOME_DATA_DIR_NAME


def _default_logs_dir(
    *,
    project_root: Path | None,
    data_dir: Path,
    explicit_data_dir: bool,
    portable: bool,
) -> Path:
    """取得未指定 logs-dir 時的 logs 目錄。"""

    return data_dir / "logs"


def _find_project_root() -> Path | None:
    """從目前 package 位置往上尋找 source tree 專案根目錄。"""

    current = Path(__file__).resolve()
    for candidate in current.parents:
        if (candidate / "pyproject.toml").exists() and (candidate / "src").exists():
            return candidate
    return None


def _resolve_path(value: Path | str | None, *, base_dir: Path, default: Path) -> Path:
    """解析 CLI path；相對路徑以 app base dir 為基準。"""

    if value is None:
        return default.resolve()
    expanded = Path(value).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (base_dir / expanded).resolve()


def _normalize_profile_name(profile_name: str) -> str:
    """整理 profile folder name，避免空值造成不可預期路徑。"""

    normalized = profile_name.strip()
    if not normalized:
        raise ValueError("--profile-name must not be empty")
    if normalized in {".", ".."} or any(separator in normalized for separator in ("/", "\\")):
        raise ValueError("--profile-name must be a folder name, not a path")
    return normalized


def _reject_common_browser_profile_dir(profile_dir: Path) -> None:
    """避免 debug-only profile path 指到使用者日常瀏覽器 profile。"""

    lower_parts = tuple(part.casefold() for part in profile_dir.parts)
    lower_part_set = set(lower_parts)
    common_browser_profile = (
        {"google", "chrome"}.issubset(lower_part_set)
        or {"microsoft", "edge"}.issubset(lower_part_set)
        or "chromium" in lower_part_set
        or "user data" in lower_part_set
    )
    if common_browser_profile:
        raise ValueError(
            "--unsafe-profile-dir must not point to a common Chrome/Edge/Chromium profile"
        )
