"""Admin tool：建置 Windows release artifact 並完成簽章驗證。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.version import APP_VERSION
from scripts.admin._release_build import DEFAULT_KEY_ID
from scripts.admin._release_build import ReleaseBuildStep
from scripts.admin._release_build import force_args
from scripts.admin._release_build import maybe_expected_tag_args
from scripts.admin._release_build import metadata_env
from scripts.admin._release_build import private_key_args
from scripts.admin._release_build import python_command
from scripts.admin._release_build import run_steps


WINDOWS_PACKAGING_MODE = "pyinstaller-onedir-gui-tray"


def parse_args() -> argparse.Namespace:
    """解析 Windows release build CLI 參數。"""

    parser = argparse.ArgumentParser(
        description="Build Windows PyInstaller onedir release artifacts."
    )
    parser.add_argument("--force", action="store_true", help="Overwrite release outputs.")
    parser.add_argument("--key-id", default=DEFAULT_KEY_ID, help="Release signing key id.")
    parser.add_argument(
        "--private-key-file",
        type=Path,
        default=None,
        help="Ed25519 private key file. Defaults to docs/local path when present, otherwise env.",
    )
    parser.add_argument(
        "--expected-tag",
        default=f"v{APP_VERSION}",
        help="Expected GitHub tag name for validation. Use empty string to skip.",
    )
    parser.add_argument(
        "--expected-signer-subject",
        default="",
        help="Optional Windows Authenticode signer subject substring.",
    )
    parser.add_argument(
        "--skip-pyinstaller-install",
        action="store_true",
        help="Skip `python -m pip install pyinstaller`.",
    )
    parser.add_argument(
        "--skip-playwright-install",
        action="store_true",
        help="Skip Playwright Chromium installation.",
    )
    parser.add_argument(
        "--skip-release-validation",
        action="store_true",
        help="Skip full release_validation.py after artifact validation.",
    )
    return parser.parse_args()


def build_steps(args: argparse.Namespace, *, version: str = APP_VERSION) -> list[ReleaseBuildStep]:
    """建立 Windows release build 流程。"""

    zip_name = f"facebook-monitor-{version}-windows-portable.zip"
    manifest_name = f"facebook-monitor-{version}-manifest.json"
    steps: list[ReleaseBuildStep] = []
    if not args.skip_pyinstaller_install:
        steps.append(
            ReleaseBuildStep(
                "install pyinstaller",
                python_command("-m", "pip", "install", "pyinstaller"),
            )
        )
    if not args.skip_playwright_install:
        steps.append(
            ReleaseBuildStep(
                "install playwright chromium",
                python_command("-m", "playwright", "install", "chromium"),
            )
        )
    steps.extend(
        [
            ReleaseBuildStep(
                "pyinstaller windows onedir",
                python_command(
                    "-m",
                    "PyInstaller",
                    "packaging/pyinstaller/facebook_monitor.spec",
                    "--clean",
                    "--noconfirm",
                ),
                env_overrides=metadata_env(packaging_mode=WINDOWS_PACKAGING_MODE),
            ),
            ReleaseBuildStep(
                "create windows release zip",
                python_command(
                    "scripts/admin/create_release_zip.py",
                    "--platform",
                    "windows",
                    *force_args(force=bool(args.force)),
                ),
            ),
            ReleaseBuildStep(
                "create signed manifest payload",
                python_command(
                    "scripts/admin/create_release_manifest.py",
                    "--version",
                    version,
                    "--key-id",
                    str(args.key_id),
                    "--asset",
                    f"windows=dist/{zip_name}",
                    "--output",
                    f"dist/{manifest_name}",
                    *force_args(force=bool(args.force)),
                ),
            ),
            ReleaseBuildStep(
                "sign manifest",
                python_command(
                    "scripts/admin/sign_release_manifest.py",
                    f"dist/{manifest_name}",
                    *private_key_args(args.private_key_file),
                    *force_args(force=bool(args.force)),
                ),
            ),
            ReleaseBuildStep(
                "validate windows artifact",
                python_command(
                    "scripts/admin/release_artifact_validation.py",
                    "--platform",
                    "windows",
                    "--require-manifest",
                    *maybe_expected_tag_args(str(args.expected_tag)),
                    *(
                        ("--expected-signer-subject", str(args.expected_signer_subject))
                        if args.expected_signer_subject
                        else ()
                    ),
                ),
            ),
        ]
    )
    if not args.skip_release_validation:
        steps.append(
            ReleaseBuildStep(
                "full release validation",
                python_command(
                    "scripts/admin/release_validation.py",
                    "--include-artifacts",
                    "--artifact-platform",
                    "windows",
                    *maybe_expected_tag_args(str(args.expected_tag)),
                    *(
                        ("--expected-signer-subject", str(args.expected_signer_subject))
                        if args.expected_signer_subject
                        else ()
                    ),
                ),
            )
        )
    return steps


def main() -> int:
    """CLI entrypoint。"""

    args = parse_args()
    if os.name != "nt":
        print("build_windows_release.py must be run on Windows.")
        return 2
    return run_steps(build_steps(args))


if __name__ == "__main__":
    raise SystemExit(main())
