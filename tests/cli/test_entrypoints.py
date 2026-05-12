"""Package entrypoint CLI tests。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_python_module_webui_help_imports_successfully() -> None:
    """`python -m facebook_monitor --help` 可載入正式 Web UI launcher。"""

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "facebook_monitor", "--help"],
        capture_output=True,
        check=False,
        cwd=root,
        text=True,
    )

    assert result.returncode == 0
    assert "--data-dir" in result.stdout
    assert "--profile-dir" in result.stdout
    assert "--auto-port" in result.stdout
    assert "--graceful-shutdown-timeout-seconds" in result.stdout
    assert "--auto-scan-mode" not in result.stdout


def test_profile_setup_module_help_imports_successfully() -> None:
    """`profile_setup` module help 可載入正式 profile setup entrypoint。"""

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from facebook_monitor.profile_setup import main; raise SystemExit(main(['--help']))",
        ],
        capture_output=True,
        check=False,
        cwd=root,
        text=True,
    )

    assert result.returncode == 0
    assert "--data-dir" in result.stdout
    assert "--profile-dir" in result.stdout
    assert "--start-url" in result.stdout
