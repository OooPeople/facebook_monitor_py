"""Frozen updater smoke script tests。"""

from __future__ import annotations

from pathlib import Path
import subprocess

from scripts.admin import smoke_frozen_updater


def test_validate_smoke_root_rejects_repo_root() -> None:
    """smoke root 不可指向 repo root，避免 rmtree 刪掉整個 checkout。"""

    try:
        smoke_frozen_updater._validate_smoke_root(smoke_frozen_updater.ROOT)
    except ValueError:
        return
    raise AssertionError("expected repo root smoke directory to be rejected")


def test_validate_smoke_root_rejects_build_root() -> None:
    """smoke root 不可直接指向 build root，只能是其下的工作目錄。"""

    try:
        smoke_frozen_updater._validate_smoke_root(
            (smoke_frozen_updater.ROOT / "build").resolve()
        )
    except ValueError:
        return
    raise AssertionError("expected build root smoke directory to be rejected")


def test_validate_smoke_root_accepts_build_child() -> None:
    """預設 build/updater_smoke 這類子目錄可以作為 smoke workspace。"""

    smoke_frozen_updater._validate_smoke_root(
        (smoke_frozen_updater.ROOT / "build" / "updater_smoke").resolve()
    )


def test_run_smoke_reports_timeout(tmp_path: Path, monkeypatch) -> None:
    """frozen updater process 卡住時，smoke 應回結構化 timeout 而不是無限等待。"""

    built_app = tmp_path / "dist" / "facebook-monitor"
    for relative in (
        "facebook-monitor.exe",
        "facebook-monitor-updater.exe",
        "_internal/python313.dll",
        "_internal/browser/chrome.exe",
        "_internal/assets/facebook-monitor.ico",
        "_internal/assets/facebook-monitor-tray.ico",
    ):
        path = built_app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=kwargs.get("args", args[0] if args else "updater"),
            timeout=1,
            output="out",
            stderr="err",
        )

    monkeypatch.setattr(smoke_frozen_updater.subprocess, "run", fake_run)

    result = smoke_frozen_updater.run_smoke(
        built_app=built_app,
        smoke_root=(smoke_frozen_updater.ROOT / "build" / "timeout_test").resolve(),
        timeout_seconds=1,
    )

    assert not result["ok"]
    assert result["timed_out"]
    assert result["stdout"] == "out"
    assert result["stderr"] == "err"
