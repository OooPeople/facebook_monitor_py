"""Release build orchestration 共用工具。"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from pathlib import Path

from scripts.admin.sign_release_manifest import PRIVATE_KEY_ENV


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KEY_ID = "release-ed25519-2026q2"
DEFAULT_RELEASE_SIGNING_DIR = Path.home() / ".facebook-monitor" / "release-signing"
DEFAULT_PRIVATE_KEY_FILE = DEFAULT_RELEASE_SIGNING_DIR / f"{DEFAULT_KEY_ID}.private-key.b64"
PYINSTALLER_VERSION = "6.20.0"
PYINSTALLER_REQUIREMENT = f"pyinstaller=={PYINSTALLER_VERSION}"


@dataclass(frozen=True)
class ReleaseBuildStep:
    """描述 release build 流程中的單一步驟。"""

    label: str
    command: tuple[str, ...]
    env_overrides: dict[str, str] = field(default_factory=dict)


def python_command(*args: str) -> tuple[str, ...]:
    """使用目前 Python executable 建立 command。"""

    return (sys.executable, *args)


def pyinstaller_version_command() -> tuple[str, ...]:
    """建立 PyInstaller 版本驗證 command，避免 skip install 使用錯版打包。"""

    return python_command(
        "-c",
        "\n".join(
            (
                "import sys",
                "try:",
                "    import PyInstaller",
                "except Exception as exc:",
                "    print(f'PyInstaller import failed: {exc}', file=sys.stderr)",
                "    raise SystemExit(1)",
                f"expected = {PYINSTALLER_VERSION!r}",
                "actual = getattr(PyInstaller, '__version__', '')",
                "if actual != expected:",
                "    print(",
                "        f'Expected PyInstaller {expected}, got {actual or \"unknown\"}',",
                "        file=sys.stderr,",
                "    )",
                "    raise SystemExit(1)",
                "print(f'PyInstaller {actual}')",
            )
        ),
    )


def current_build_date() -> str:
    """回傳 release build metadata 使用的 UTC build date。"""

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def current_git_commit() -> str:
    """讀取目前 Git commit；非 Git checkout 時回傳 unknown。"""

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def metadata_env(*, packaging_mode: str) -> dict[str, str]:
    """建立 PyInstaller build metadata 環境變數。"""

    return {
        "FACEBOOK_MONITOR_BUILD_DATE": current_build_date(),
        "FACEBOOK_MONITOR_GIT_COMMIT": current_git_commit(),
        "FACEBOOK_MONITOR_PACKAGING_MODE": packaging_mode,
    }


def command_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """建立 subprocess 環境，固定 uv cache 並套用單步覆寫。"""

    env = os.environ.copy()
    env["UV_CACHE_DIR"] = str(ROOT / ".uv-cache")
    if overrides:
        env.update(overrides)
    return env


def private_key_args(private_key_file: Path | None) -> tuple[str, ...]:
    """回傳 manifest signer 檔案參數；環境變數存在時不注入預設檔。"""

    if private_key_file is None:
        if os.environ.get(PRIVATE_KEY_ENV, "").strip():
            return ()
        candidate = DEFAULT_PRIVATE_KEY_FILE
        if not candidate.is_file():
            return ()
        return ("--private-key-file", str(candidate))
    return ("--private-key-file", str(private_key_file))


def force_args(*, force: bool) -> tuple[str, ...]:
    """依 force flag 回傳共用覆蓋參數。"""

    return ("--force",) if force else ()


def maybe_expected_tag_args(expected_tag: str) -> tuple[str, ...]:
    """回傳可選 expected tag 參數。"""

    return ("--expected-tag", expected_tag) if expected_tag else ()


def run_steps(steps: list[ReleaseBuildStep]) -> int:
    """依序執行 release build steps，任一步失敗即停止。"""

    for step in steps:
        print(f"\n==> {step.label}")
        print(" ".join(step.command))
        sys.stdout.flush()
        completed = subprocess.run(
            step.command,
            cwd=ROOT,
            check=False,
            env=command_env(step.env_overrides),
        )
        if completed.returncode != 0:
            print()
            print(f"FAILED: {step.label} exited with {completed.returncode}")
            return completed.returncode
    print("\nRelease build completed.")
    return 0
