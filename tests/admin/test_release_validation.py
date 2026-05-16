"""Release validation script tests。"""

from __future__ import annotations

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
