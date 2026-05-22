"""Release validation script tests。"""

from __future__ import annotations

import argparse

from scripts.admin import release_validation


def test_validation_steps_skip_git_diff_outside_git_checkout() -> None:
    """非 Git checkout 不應把 git diff --check 放入驗證步驟。"""

    steps = release_validation.validation_steps(
        skip_sync=True,
        git_checkout=False,
    )

    assert "git diff --check" not in [step.label for step in steps]


def test_validation_steps_keep_git_diff_in_git_checkout() -> None:
    """Git checkout 內仍必須執行 git diff --check。"""

    steps = release_validation.validation_steps(
        skip_sync=True,
        git_checkout=True,
    )

    assert "git diff --check" in [step.label for step in steps]


def test_validation_steps_include_pip_audit_only_when_requested() -> None:
    """pip-audit 是 release validation 的 opt-in dependency audit。"""

    default_steps = release_validation.validation_steps(
        skip_sync=True,
        git_checkout=True,
    )
    audit_steps = release_validation.validation_steps(
        skip_sync=True,
        git_checkout=True,
        include_audit=True,
    )

    assert "pip-audit" not in [step.label for step in default_steps]
    assert "pip-audit" in [step.label for step in audit_steps]


def test_validation_steps_use_project_mypy_config() -> None:
    """release validation 的 mypy 指令必須與 CI 一樣讀取專案設定。"""

    steps = release_validation.validation_steps(
        skip_sync=True,
        git_checkout=True,
    )

    mypy_step = next(step for step in steps if step.label == "mypy")
    assert mypy_step.command == ["uv", "run", "mypy"]


def test_validation_steps_include_artifact_validation_when_requested() -> None:
    """artifact validation 只在明確要求時加入，並可傳遞 signer subject。"""

    default_steps = release_validation.validation_steps(
        skip_sync=True,
        git_checkout=False,
    )
    artifact_steps = release_validation.validation_steps(
        skip_sync=True,
        git_checkout=False,
        include_artifacts=True,
        expected_signer_subject="Example Publisher",
    )

    assert "release artifacts" not in [step.label for step in default_steps]
    artifact_step = next(step for step in artifact_steps if step.label == "release artifacts")
    assert "scripts/admin/release_artifact_validation.py" in artifact_step.command
    assert "--platform" in artifact_step.command
    assert "windows" in artifact_step.command
    assert "--require-manifest" in artifact_step.command
    assert "--expected-signer-subject" in artifact_step.command
    assert "Example Publisher" in artifact_step.command


def test_validation_steps_pass_expected_tag_to_artifact_validation() -> None:
    """expected tag 由 release validation 傳給 artifact validation。"""

    artifact_steps = release_validation.validation_steps(
        skip_sync=True,
        git_checkout=False,
        include_artifacts=True,
        expected_tag="v0.1.0",
    )

    artifact_step = next(step for step in artifact_steps if step.label == "release artifacts")
    assert "--expected-tag" in artifact_step.command
    assert "v0.1.0" in artifact_step.command


def test_validation_steps_pass_artifact_platform_to_artifact_validation() -> None:
    """release validation 可明確驗證 macOS artifact。"""

    artifact_steps = release_validation.validation_steps(
        skip_sync=True,
        git_checkout=False,
        include_artifacts=True,
        artifact_platform="macos-arm64",
    )

    artifact_step = next(step for step in artifact_steps if step.label == "release artifacts")
    assert "--platform" in artifact_step.command
    assert "macos-arm64" in artifact_step.command


def test_validate_cli_args_rejects_signer_without_artifacts() -> None:
    """signer subject 沒有 artifact validation 時不可被靜默忽略。"""

    error = release_validation.validate_cli_args(
        argparse.Namespace(
            include_artifacts=False,
            artifact_platform="windows",
            expected_signer_subject="Example Publisher",
            expected_tag="",
        )
    )

    assert error == "--expected-signer-subject requires --include-artifacts"


def test_validate_cli_args_rejects_tag_without_artifacts() -> None:
    """expected tag 沒有 artifact validation 時不可被靜默忽略。"""

    error = release_validation.validate_cli_args(
        argparse.Namespace(
            include_artifacts=False,
            artifact_platform="windows",
            expected_signer_subject="",
            expected_tag="v0.1.0",
        )
    )

    assert error == "--expected-tag requires --include-artifacts"


def test_validate_cli_args_rejects_platform_without_artifacts() -> None:
    """artifact platform 沒有 artifact validation 時不可被靜默忽略。"""

    error = release_validation.validate_cli_args(
        argparse.Namespace(
            include_artifacts=False,
            artifact_platform="macos-arm64",
            expected_signer_subject="",
            expected_tag="",
        )
    )

    assert error == "--artifact-platform requires --include-artifacts"


def test_validate_cli_args_rejects_macos_signer_subject() -> None:
    """Authenticode signer 只適用 Windows artifact。"""

    error = release_validation.validate_cli_args(
        argparse.Namespace(
            include_artifacts=True,
            artifact_platform="macos-arm64",
            expected_signer_subject="Example Publisher",
            expected_tag="",
        )
    )

    assert error == "--expected-signer-subject is only supported for Windows artifacts"
