"""Admin tool：執行 release 前可重現的本機驗證流程。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.updates.artifacts import UPDATE_ARTIFACT_POLICIES
from facebook_monitor.webapp.assets import ASSET_VERSION


ARTIFACT_PLATFORM_CHOICES = tuple(
    policy.platform_key for policy in UPDATE_ARTIFACT_POLICIES
)


@dataclass(frozen=True)
class ValidationStep:
    """保存單一 release validation command。"""

    label: str
    command: list[str]


def parse_args() -> argparse.Namespace:
    """解析 release validation CLI 參數。"""

    parser = argparse.ArgumentParser(
        description="Run reproducible release validation commands."
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="Skip uv sync --locked when the environment is already prepared.",
    )
    parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="Skip pip-audit when running offline or intentionally reproducing non-audit checks.",
    )
    parser.add_argument(
        "--include-artifacts",
        action="store_true",
        help="Also validate release zip, SHA256, and platform-specific artifact metadata.",
    )
    parser.add_argument(
        "--skip-artifact-manifest",
        action="store_true",
        help="Validate platform artifacts before the final signed manifest is created.",
    )
    parser.add_argument(
        "--artifact-platform",
        default="windows",
        choices=ARTIFACT_PLATFORM_CHOICES,
        help="Platform artifact to validate when --include-artifacts is set.",
    )
    parser.add_argument(
        "--expected-signer-subject",
        default="",
        help="Optional Authenticode signer subject substring expected for EXEs.",
    )
    parser.add_argument(
        "--expected-tag",
        default="",
        help="Optional GitHub tag expected for the app version, for example v0.2.0.",
    )
    return parser.parse_args()


def uv_command(*args: str) -> list[str]:
    """回傳 uv command；cache 由 run_step 固定在專案目錄。"""

    return ["uv", *args]


def git_commit() -> str:
    """讀取目前 commit hash；非 git checkout 時回傳 unknown。"""

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def node_version() -> str:
    """讀取 static JS syntax check 使用的 Node 版本。"""

    try:
        return subprocess.check_output(
            ["node", "--version"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.STDOUT,
            env=validation_env(),
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"unavailable ({exc})"


def is_git_checkout() -> bool:
    """回傳 ROOT 是否位於 Git checkout 內。"""

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            env=validation_env(),
        )
    except OSError:
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def print_environment() -> None:
    """列印 release validation 可追溯環境資訊。"""

    print("Release validation environment")
    print("==============================")
    print(f"Root: {ROOT}")
    print(f"OS: {platform.platform()}")
    print(f"Python: {platform.python_version()} ({sys.executable})")
    print(f"Commit: {git_commit()}")
    print(f"Asset version: {ASSET_VERSION}")
    try:
        uv_version = subprocess.check_output(
            uv_command("--version"),
            cwd=ROOT,
            text=True,
            stderr=subprocess.STDOUT,
            env=validation_env(),
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        uv_version = f"unavailable ({exc})"
    print(f"uv: {uv_version}")
    print(f"Node: {node_version()}")
    print("Manual smoke still required: Facebook login, metadata resolver, posts/comments scan, notifications.")
    print()
    sys.stdout.flush()


def validation_steps(
    *,
    skip_sync: bool,
    git_checkout: bool,
    include_audit: bool = True,
    include_artifacts: bool = False,
    require_artifact_manifest: bool = True,
    artifact_platform: str = "windows",
    expected_signer_subject: str = "",
    expected_tag: str = "",
) -> list[ValidationStep]:
    """建立 release validation command 清單。"""

    steps = [
        ValidationStep(
            "pytest",
            uv_command(
                "run",
                "pytest",
                "-q",
                "--cov=facebook_monitor",
                "--cov-report=term-missing",
                "--cov-fail-under=80",
            ),
        ),
        ValidationStep(
            "frontend vendor manifest",
            uv_command(
                "run",
                "python",
                "scripts/admin/check_frontend_vendor_manifest.py",
            ),
        ),
        ValidationStep("mypy", uv_command("run", "mypy")),
        ValidationStep(
            "ruff",
            uv_command("run", "ruff", "check", "src", "scripts", "tests"),
        ),
        ValidationStep(
            "compileall",
            uv_command(
                "run",
                "python",
                "-m",
                "compileall",
                "-q",
                "src",
                "scripts",
                "tests",
            ),
        ),
        ValidationStep(
            "static js syntax",
            uv_command(
                "run",
                "python",
                "scripts/admin/check_static_js_syntax.py",
            ),
        ),
    ]
    if include_audit:
        steps.append(ValidationStep("pip-audit", uv_command("run", "pip-audit")))
    if include_artifacts:
        artifact_command = [
            *uv_command(
                "run",
                "python",
                "scripts/admin/release_artifact_validation.py",
                "--platform",
                artifact_platform,
            )
        ]
        if require_artifact_manifest:
            artifact_command.append("--require-manifest")
        if expected_signer_subject:
            artifact_command.extend(
                ["--expected-signer-subject", expected_signer_subject]
            )
        if expected_tag:
            artifact_command.extend(["--expected-tag", expected_tag])
        steps.append(
            ValidationStep(
                "release artifacts",
                artifact_command,
            )
        )
    if git_checkout:
        steps.append(ValidationStep("git diff --check", ["git", "diff", "--check"]))
    if skip_sync:
        return steps
    return [
        ValidationStep("uv sync", uv_command("sync", "--locked", "--all-extras", "--dev")),
        *steps,
    ]


def run_step(step: ValidationStep) -> int:
    """執行單一 validation step 並回傳 process return code。"""

    print(f"\n==> {step.label}")
    print(" ".join(step.command))
    sys.stdout.flush()
    completed = subprocess.run(step.command, cwd=ROOT, check=False, env=validation_env())
    if completed.returncode != 0:
        print()
        print(f"FAILED: {step.label} exited with {completed.returncode}")
        if step.label != "uv sync":
            print("若錯誤是缺少依賴，請先執行本腳本的預設流程或手動執行 uv sync --locked。")
        return completed.returncode
    return 0


def validation_env() -> dict[str, str]:
    """建立 validation subprocess environment，固定 uv cache 位置。"""

    env = os.environ.copy()
    env["UV_CACHE_DIR"] = str(ROOT / ".uv-cache")
    return env


def validate_cli_args(args: argparse.Namespace) -> str | None:
    """驗證跨參數組合，避免 release validation 靜默忽略安全檢查。"""

    if args.expected_signer_subject and not args.include_artifacts:
        return "--expected-signer-subject requires --include-artifacts"
    if args.expected_tag and not args.include_artifacts:
        return "--expected-tag requires --include-artifacts"
    if args.skip_artifact_manifest and not args.include_artifacts:
        return "--skip-artifact-manifest requires --include-artifacts"
    if args.artifact_platform != "windows" and not args.include_artifacts:
        return "--artifact-platform requires --include-artifacts"
    if args.artifact_platform != "windows" and args.expected_signer_subject:
        return "--expected-signer-subject is only supported for Windows artifacts"
    return None


def completion_message(args: argparse.Namespace) -> str:
    """依 skip flags 建立不會誤導為 CI / upload-ready 的完成訊息。"""

    qualifiers: list[str] = []
    if args.skip_sync:
        qualifiers.append("uv sync skipped")
    if args.skip_audit:
        qualifiers.append("pip-audit skipped; not CI/upload complete")
    if args.include_artifacts and args.skip_artifact_manifest:
        qualifiers.append("artifact manifest skipped; not upload-ready")
    label = (
        "Local release validation with artifact checks passed"
        if args.include_artifacts
        else "Local release validation passed"
    )
    if not qualifiers:
        return f"{label}."
    return f"{label} ({'; '.join(qualifiers)})."


def main() -> int:
    """CLI entrypoint：依序執行 release validation。"""

    args = parse_args()
    args_error = validate_cli_args(args)
    if args_error is not None:
        print(args_error)
        return 2
    print_environment()
    git_checkout = is_git_checkout()
    if not git_checkout:
        print("非 Git checkout，已跳過 git diff --check。")
    if args.skip_audit:
        print("已跳過 pip-audit；CI 仍會執行 dependency audit。")
    else:
        print("已啟用 pip-audit；此步驟可能需要網路或 advisory DB。")
    if args.include_artifacts:
        print(f"已啟用 {args.artifact_platform} release artifact 一致性檢查。")
        if args.skip_artifact_manifest:
            print("artifact validation 將略過 signed manifest；finalize 後仍需重驗。")
    for step in validation_steps(
        skip_sync=args.skip_sync,
        git_checkout=git_checkout,
        include_audit=not bool(args.skip_audit),
        include_artifacts=args.include_artifacts,
        require_artifact_manifest=not bool(args.skip_artifact_manifest),
        artifact_platform=args.artifact_platform,
        expected_signer_subject=args.expected_signer_subject,
        expected_tag=args.expected_tag,
    ):
        return_code = run_step(step)
        if return_code != 0:
            return return_code
    print(f"\n{completion_message(args)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
