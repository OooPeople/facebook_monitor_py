"""Application version metadata。

職責：提供 runtime 可用的 app 名稱與版本；版本唯一來源是
`pyproject.toml` 的 `[project].version`。
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
import os
from pathlib import Path
import sys
import tomllib

APP_NAME = "Facebook Monitor"
APP_VERSION_ENV = "FACEBOOK_MONITOR_APP_VERSION"
PROJECT_DISTRIBUTION_NAME = "facebook-monitor-py"


def _read_frozen_build_version() -> str | None:
    """讀取 PyInstaller runtime hook 注入的 frozen app version。"""

    if not bool(getattr(sys, "frozen", False)):
        return None
    value = os.environ.get(APP_VERSION_ENV, "").strip()
    if not value:
        return None
    return value


def _read_pyproject_version() -> str | None:
    """從 source tree 的 pyproject.toml 讀取專案版本。"""

    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject_path.is_file():
        return None
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    version = project.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("pyproject.toml missing [project].version")
    return version.strip()


def _resolve_app_version() -> str:
    """解析 app version；source tree 用 pyproject，installed/frozen 用 metadata。"""

    frozen_build_version = _read_frozen_build_version()
    if frozen_build_version is not None:
        return frozen_build_version
    pyproject_version = _read_pyproject_version()
    if pyproject_version is not None:
        return pyproject_version
    try:
        return package_version(PROJECT_DISTRIBUTION_NAME)
    except PackageNotFoundError as exc:
        raise RuntimeError("cannot resolve facebook monitor app version") from exc


APP_VERSION = _resolve_app_version()
