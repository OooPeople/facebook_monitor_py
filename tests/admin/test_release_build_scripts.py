"""Release build orchestration scripts 測試。"""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.admin import build_macos_release
from scripts.admin import build_windows_release


def _windows_args(**overrides: object) -> argparse.Namespace:
    """建立 Windows build_steps 測試參數。"""

    values: dict[str, object] = {
        "force": True,
        "key_id": "test-key",
        "private_key_file": Path("release.private-key.b64"),
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
        "key_id": "test-key",
        "private_key_file": Path("release.private-key.b64"),
        "expected_tag": "v9.9.9",
        "skip_pyinstaller_install": False,
        "skip_playwright_install": False,
        "skip_release_validation": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_windows_release_build_steps_cover_full_artifact_flow() -> None:
    """Windows release builder 應串起打包、manifest、簽章與驗證。"""

    steps = build_windows_release.build_steps(_windows_args(), version="9.9.9")
    labels = [step.label for step in steps]

    assert labels == [
        "install pyinstaller",
        "install playwright chromium",
        "pyinstaller windows onedir",
        "create windows release zip",
        "create signed manifest payload",
        "sign manifest",
        "validate windows artifact",
        "full release validation",
    ]
    manifest_step = next(step for step in steps if step.label == "create signed manifest payload")
    assert "windows=dist/facebook-monitor-9.9.9-windows-portable.zip" in manifest_step.command
    assert "--key-id" in manifest_step.command
    assert "test-key" in manifest_step.command
    sign_step = next(step for step in steps if step.label == "sign manifest")
    assert "--private-key-file" in sign_step.command
    artifact_step = next(step for step in steps if step.label == "validate windows artifact")
    assert "--require-manifest" in artifact_step.command
    assert "--expected-tag" in artifact_step.command
    full_step = next(step for step in steps if step.label == "full release validation")
    assert "--include-artifacts" in full_step.command
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
    assert "install playwright chromium" not in labels
    assert "full release validation" not in labels
    assert "validate windows artifact" in labels


def test_windows_release_build_steps_pass_signer_subject() -> None:
    """Windows signer subject 應傳給 artifact 與 full validation。"""

    steps = build_windows_release.build_steps(
        _windows_args(expected_signer_subject="Example Publisher"),
        version="9.9.9",
    )

    artifact_step = next(step for step in steps if step.label == "validate windows artifact")
    full_step = next(step for step in steps if step.label == "full release validation")
    assert "--expected-signer-subject" in artifact_step.command
    assert "Example Publisher" in artifact_step.command
    assert "--expected-signer-subject" in full_step.command
    assert "Example Publisher" in full_step.command


def test_macos_release_build_steps_cover_full_artifact_flow() -> None:
    """macOS release builder 應串起打包、manifest、簽章與驗證。"""

    steps = build_macos_release.build_steps(_macos_args(), version="9.9.9")
    labels = [step.label for step in steps]

    assert labels == [
        "install pyinstaller",
        "install playwright chromium",
        "pyinstaller macos onedir",
        "create macos release zip",
        "create signed manifest payload",
        "sign manifest",
        "validate macos artifact",
        "full release validation",
    ]
    manifest_step = next(step for step in steps if step.label == "create signed manifest payload")
    assert "macos-arm64=dist/facebook-monitor-9.9.9-macos-arm64-onedir.zip" in (
        manifest_step.command
    )
    artifact_step = next(step for step in steps if step.label == "validate macos artifact")
    assert "--platform" in artifact_step.command
    assert "macos-arm64" in artifact_step.command
    assert "--require-manifest" in artifact_step.command
    full_step = next(step for step in steps if step.label == "full release validation")
    assert "--artifact-platform" in full_step.command
    assert "macos-arm64" in full_step.command


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
    assert "install playwright chromium" not in labels
    assert "full release validation" not in labels
    assert "validate macos artifact" in labels
