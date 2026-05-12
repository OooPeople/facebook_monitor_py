"""Build/runtime metadata helpers。

職責：集中啟動診斷與設定頁會用到的版本、Python 與 future packaging
metadata，讓未來打包流程能用環境變數注入 build 資訊。
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import platform
from pathlib import Path
import sys

from facebook_monitor.version import APP_NAME
from facebook_monitor.version import APP_VERSION


BUILD_DATE_ENV = "FACEBOOK_MONITOR_BUILD_DATE"
GIT_COMMIT_ENV = "FACEBOOK_MONITOR_GIT_COMMIT"
PACKAGING_MODE_ENV = "FACEBOOK_MONITOR_PACKAGING_MODE"


@dataclass(frozen=True)
class BuildMetadata:
    """描述目前執行檔與版本資訊。"""

    app_name: str
    app_version: str
    asset_version: str
    python_version: str
    executable: Path
    frozen: bool
    packaging_mode: str
    build_date: str
    git_commit: str


def collect_build_metadata(*, asset_version: str) -> BuildMetadata:
    """收集 diagnostics 可顯示的 build/runtime metadata。"""

    frozen = bool(getattr(sys, "frozen", False))
    packaging_mode = os.environ.get(PACKAGING_MODE_ENV) or (
        "frozen" if frozen else "source"
    )
    return BuildMetadata(
        app_name=APP_NAME,
        app_version=APP_VERSION,
        asset_version=asset_version,
        python_version=platform.python_version(),
        executable=Path(sys.executable).resolve(),
        frozen=frozen,
        packaging_mode=packaging_mode,
        build_date=os.environ.get(BUILD_DATE_ENV, "unknown"),
        git_commit=os.environ.get(GIT_COMMIT_ENV, "unknown"),
    )
