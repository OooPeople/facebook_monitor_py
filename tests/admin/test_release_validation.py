"""Release validation script tests。"""

from __future__ import annotations

import argparse
from types import SimpleNamespace

from scripts.admin import check_static_js_syntax
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


def test_node_version_reports_static_js_runtime(monkeypatch) -> None:
    """release validation 環境資訊需列出 static JS syntax check 使用的 Node。"""

    def fake_check_output(command, **_kwargs):
        assert command == ["node", "--version"]
        return "v24.14.1\n"

    monkeypatch.setattr(release_validation.subprocess, "check_output", fake_check_output)

    assert release_validation.node_version() == "v24.14.1"


def test_validation_steps_use_ci_coverage_gate() -> None:
    """release validation 的 pytest 指令需覆蓋 CI 文件化的 coverage gate。"""

    steps = release_validation.validation_steps(
        skip_sync=True,
        git_checkout=True,
    )

    pytest_step = next(step for step in steps if step.label == "pytest")
    assert pytest_step.command == [
        "uv",
        "run",
        "pytest",
        "-q",
        "--cov=facebook_monitor",
        "--cov-report=term-missing",
        "--cov-fail-under=80",
    ]


def test_validation_steps_include_static_js_syntax_check() -> None:
    """release validation 必須覆蓋 Web static JS 語法檢查。"""

    steps = release_validation.validation_steps(
        skip_sync=True,
        git_checkout=True,
    )

    js_step = next(step for step in steps if step.label == "static js syntax")
    assert js_step.command == [
        "uv",
        "run",
        "python",
        "scripts/admin/check_static_js_syntax.py",
    ]


def test_static_js_syntax_check_reports_missing_node(monkeypatch, capsys) -> None:
    """缺 node 時 JS syntax checker 應以清楚錯誤 fail。"""

    monkeypatch.setattr(check_static_js_syntax.shutil, "which", lambda _name: None)

    assert check_static_js_syntax.main() == 1
    assert "node executable not found" in capsys.readouterr().err


def test_static_js_syntax_check_returns_first_node_failure(monkeypatch, tmp_path) -> None:
    """JS syntax checker 應回傳 node --check 的第一個失敗狀態。"""

    first = tmp_path / "first.js"
    second = tmp_path / "second.js"
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(check_static_js_syntax.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(
        check_static_js_syntax,
        "iter_static_js_files",
        lambda: [first, second],
    )
    monkeypatch.setattr(check_static_js_syntax.subprocess, "run", fake_run)

    assert check_static_js_syntax.main() == 7
    assert calls == [["node", "--check", str(first)]]


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


def test_validation_steps_can_skip_artifact_manifest_for_platform_build() -> None:
    """平台 build 階段可驗 artifact 但不要求 finalized signed manifest。"""

    artifact_steps = release_validation.validation_steps(
        skip_sync=True,
        git_checkout=False,
        include_artifacts=True,
        require_artifact_manifest=False,
    )

    artifact_step = next(step for step in artifact_steps if step.label == "release artifacts")
    assert "--require-manifest" not in artifact_step.command


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
            skip_artifact_manifest=False,
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
            skip_artifact_manifest=False,
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
            skip_artifact_manifest=False,
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
            skip_artifact_manifest=False,
        )
    )

    assert error == "--expected-signer-subject is only supported for Windows artifacts"


def test_validate_cli_args_rejects_skip_manifest_without_artifacts() -> None:
    """skip manifest 只對 artifact validation 階段有意義。"""

    error = release_validation.validate_cli_args(
        argparse.Namespace(
            include_artifacts=False,
            artifact_platform="windows",
            expected_signer_subject="",
            expected_tag="",
            skip_artifact_manifest=True,
        )
    )

    assert error == "--skip-artifact-manifest requires --include-artifacts"
