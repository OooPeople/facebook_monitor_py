"""Web UI start script CLI tests。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_webui_start_help_imports_successfully() -> None:
    """`webui.py --help` 可成功載入啟動腳本，避免 stale import regression。"""

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "scripts/start/webui.py", "--help"],
        capture_output=True,
        check=False,
        cwd=root,
        text=True,
    )

    assert result.returncode == 0
    assert "--db-path" in result.stdout
    assert "--profile-dir" in result.stdout
    assert "--graceful-shutdown-timeout-seconds" in result.stdout
    assert "--auto-scan-mode" not in result.stdout
