"""Web UI Jinja template environment assembly。

職責：集中 per-app template globals，讓 app factory 只負責傳入已解析資源路徑。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from facebook_monitor.core import input_limits
from facebook_monitor.webapp.assets import ASSET_VERSION
def build_templates(templates_dir: Path, *, csrf_token: str = "") -> Jinja2Templates:
    """建立 Jinja template environment，並注入 Web UI 共用 globals。"""

    template_environment = Jinja2Templates(directory=str(templates_dir))
    template_environment.env.globals["asset_version"] = ASSET_VERSION
    template_environment.env.globals["csrf_token"] = csrf_token
    template_environment.env.globals["input_limits"] = input_limits
    return template_environment


__all__ = ["build_templates"]
