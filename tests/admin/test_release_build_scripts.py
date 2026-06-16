"""Release build orchestration scripts 測試。"""

from __future__ import annotations

import argparse

from scripts.admin import build_macos_release
from scripts.admin import build_windows_release
from scripts.admin._release_build import PYINSTALLER_REQUIREMENT


def _windows_args(**overrides: object) -> argparse.Namespace:
    """建立 Windows build_steps 測試參數。"""

    values: dict[str, object] = {
        "force": True,
        "expected_tag": "v9.9.9",
        "expected_signer_subject": "",
        "skip_pyinstaller_install": False,
        "skip_playwright_install": False,
        "skip_release_validation": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _macos_args(**overrides: object) -> argparse.Namespace:
    """建立 macOS build_steps 測試參數。"""

    values: dict[str, object] = {
        "force": True,
        "expected_tag": "v9.9.9",
        "skip_pyinstaller_install": False,
        "skip_playwright_install": False,
        "skip_release_validation": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_windows_release_build_steps_cover_full_artifact_flow() -> None:
    """Windows release builder 應串起打包、zip、sha 與平台驗證。"""

    steps = build_windows_release.build_steps(_windows_args(), version="9.9.9")
    labels = [step.label for step in steps]

    assert labels == [
        "install pyinstaller",
        "verify pyinstaller version",
        "install playwright chromium",
        "pyinstaller windows onedir",
        "create windows release zip",
        "validate windows artifact",
        "pre-finalize release validation",
    ]
    artifact_step = next(step for step in steps if step.label == "validate windows artifact")
    assert "--require-manifest" not in artifact_step.command
    assert "--expected-tag" in artifact_step.command
    install_step = next(step for step in steps if step.label == "install pyinstaller")
    assert PYINSTALLER_REQUIREMENT in install_step.command
    verify_step = next(step for step in steps if step.label == "verify pyinstaller version")
    assert "PyInstaller" in verify_step.command[-1]
    full_step = next(
        step for step in steps if step.label == "pre-finalize release validation"
    )
    assert "--include-artifacts" in full_step.command
    assert "--skip-artifact-manifest" in full_step.command
    assert "windows" in full_step.command


def test_windows_release_build_steps_can_skip_install_and_full_validation() -> None:
    """Windows release builder 支援快速重建 artifact。"""

    steps = build_windows_release.build_steps(
        _windows_args(
            skip_pyinstaller_install=True,
            skip_playwright_install=True,
            skip_release_validation=True,
        ),
        version="9.9.9",
    )
    labels = [step.label for step in steps]

    assert "install pyinstaller" not in labels
    assert "verify pyinstaller version" in labels
    assert "install playwright chromium" not in labels
    assert "pre-finalize release validation" not in labels
    assert "validate windows artifact" in labels


def test_windows_release_build_steps_pass_signer_subject() -> None:
    """Windows signer subject 應傳給 artifact 與 pre-finalize validation。"""

    steps = build_windows_release.build_steps(
        _windows_args(expected_signer_subject="Example Publisher"),
        version="9.9.9",
    )

    artifact_step = next(step for step in steps if step.label == "validate windows artifact")
    full_step = next(
        step for step in steps if step.label == "pre-finalize release validation"
    )
    assert "--expected-signer-subject" in artifact_step.command
    assert "Example Publisher" in artifact_step.command
    assert "--expected-signer-subject" in full_step.command
    assert "Example Publisher" in full_step.command


def test_macos_release_build_steps_cover_full_artifact_flow() -> None:
    """macOS release builder 應串起打包、zip、sha 與平台驗證。"""

    steps = build_macos_release.build_steps(_macos_args(), version="9.9.9")
    labels = [step.label for step in steps]

    assert labels == [
        "install pyinstaller",
        "verify pyinstaller version",
        "install playwright chromium",
        "pyinstaller macos onedir",
        "create macos release zip",
        "validate macos artifact",
        "pre-finalize release validation",
    ]
    artifact_step = next(step for step in steps if step.label == "validate macos artifact")
    assert "--platform" in artifact_step.command
    assert "macos-arm64" in artifact_step.command
    assert "--require-manifest" not in artifact_step.command
    install_step = next(step for step in steps if step.label == "install pyinstaller")
    assert PYINSTALLER_REQUIREMENT in install_step.command
    verify_step = next(step for step in steps if step.label == "verify pyinstaller version")
    assert "PyInstaller" in verify_step.command[-1]
    full_step = next(
        step for step in steps if step.label == "pre-finalize release validation"
    )
    assert "--artifact-platform" in full_step.command
    assert "macos-arm64" in full_step.command
    assert "--skip-artifact-manifest" in full_step.command


def test_macos_release_build_steps_can_skip_install_and_full_validation() -> None:
    """macOS release builder 支援快速重建 artifact。"""

    steps = build_macos_release.build_steps(
        _macos_args(
            skip_pyinstaller_install=True,
            skip_playwright_install=True,
            skip_release_validation=True,
        ),
        version="9.9.9",
    )
    labels = [step.label for step in steps]

    assert "install pyinstaller" not in labels
    assert "verify pyinstaller version" in labels
    assert "install playwright chromium" not in labels
    assert "pre-finalize release validation" not in labels
    assert "validate macos artifact" in labels
