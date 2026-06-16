"""Admin tool：依 dist 內 release zip 集合產生唯一 signed manifest。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.updates.artifacts import RELEASE_ASSET_PREFIX
from facebook_monitor.updates.artifacts import UPDATE_ARTIFACT_POLICIES
from facebook_monitor.updates.artifacts import release_sha256_asset_name
from facebook_monitor.updates.checksum import calculate_sha256
from facebook_monitor.updates.checksum import render_sha256_sidecar
from facebook_monitor.updates.manifest import release_manifest_asset_name
from facebook_monitor.updates.manifest import release_manifest_signature_asset_name
from facebook_monitor.updates.release_check import DEFAULT_UPDATE_REPOSITORY
from facebook_monitor.version import APP_VERSION
from scripts.admin._release_build import DEFAULT_KEY_ID
from scripts.admin._release_build import DEFAULT_PRIVATE_KEY_FILE
from scripts.admin.create_release_manifest import create_release_manifest
from scripts.admin.release_artifact_validation import validate_release_artifacts
from scripts.admin.sign_release_manifest import PRIVATE_KEY_ENV
from scripts.admin.sign_release_manifest import sign_release_manifest


@dataclass(frozen=True)
class FinalizedReleaseManifest:
    """保存 finalized release manifest 的輸出與涵蓋平台。"""

    manifest_path: Path
    signature_path: Path
    platforms: tuple[str, ...]
    asset_specs: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    """解析 finalize release manifest CLI 參數。"""

    parser = argparse.ArgumentParser(
        description="Create and sign the final release manifest from dist artifacts."
    )
    parser.add_argument("--version", default=APP_VERSION)
    parser.add_argument("--repository", default=DEFAULT_UPDATE_REPOSITORY)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--key-id", default=DEFAULT_KEY_ID)
    parser.add_argument(
        "--private-key-file",
        type=Path,
        default=None,
        help="Ed25519 private key file. Defaults to repo-external local signing path when present.",
    )
    parser.add_argument("--private-key-b64", default="")
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
        "--skip-artifact-validation",
        action="store_true",
        help="Only create/sign manifest; skip final --require-manifest validation.",
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def finalize_release_manifest(
    *,
    version: str = APP_VERSION,
    repository: str = DEFAULT_UPDATE_REPOSITORY,
    dist_dir: Path = ROOT / "dist",
    key_id: str = DEFAULT_KEY_ID,
    private_key_b64: str = "",
    private_key_file: Path | None = None,
    expected_tag: str = "",
    expected_signer_subject: str = "",
    validate_artifacts: bool = True,
    force: bool = False,
) -> FinalizedReleaseManifest:
    """依目前 dist 內正式平台 zip 建立 final manifest 並簽署。"""

    resolved_dist_dir = dist_dir.resolve()
    if not resolved_dist_dir.is_dir():
        raise ValueError(f"release_manifest_dist_missing:{resolved_dist_dir}")

    artifacts = _discover_release_artifacts(
        version=version,
        dist_dir=resolved_dist_dir,
    )
    if not artifacts:
        raise ValueError("release_manifest_no_platform_assets")

    manifest_path = resolved_dist_dir / release_manifest_asset_name(version)
    signature_path = resolved_dist_dir / release_manifest_signature_asset_name(version)
    asset_specs = tuple(
        f"{platform}={path.as_posix()}" for platform, path in artifacts
    )
    create_release_manifest(
        version=version,
        repository=repository,
        key_id=key_id,
        asset_specs=list(asset_specs),
        output=manifest_path,
        force=force,
    )
    sign_release_manifest(
        manifest_path=manifest_path,
        private_key_b64=private_key_b64,
        private_key_file=_resolve_private_key_file(
            private_key_file,
            private_key_b64=private_key_b64,
        ),
        output=signature_path,
        force=force,
    )
    platforms = tuple(platform for platform, _ in artifacts)
    if validate_artifacts:
        _validate_final_artifacts(
            version=version,
            dist_dir=resolved_dist_dir,
            platforms=platforms,
            expected_tag=expected_tag,
            expected_signer_subject=expected_signer_subject,
        )
    return FinalizedReleaseManifest(
        manifest_path=manifest_path,
        signature_path=signature_path,
        platforms=platforms,
        asset_specs=asset_specs,
    )


def _discover_release_artifacts(
    *,
    version: str,
    dist_dir: Path,
) -> tuple[tuple[str, Path], ...]:
    """找出目前版本的正式平台 zip，並拒絕 dist 內可混淆的 release zip。"""

    expected_names = {
        policy.asset_name(version): policy.platform_key
        for policy in UPDATE_ARTIFACT_POLICIES
    }
    expected_sha_names = {
        release_sha256_asset_name(name): name for name in expected_names
    }
    _reject_unexpected_release_files(
        dist_dir=dist_dir,
        expected_zip_names=set(expected_names),
        expected_sha_names=expected_sha_names,
        expected_manifest_names={
            release_manifest_asset_name(version),
            release_manifest_signature_asset_name(version),
        },
    )

    artifacts: list[tuple[str, Path]] = []
    for policy in UPDATE_ARTIFACT_POLICIES:
        zip_path = dist_dir / policy.asset_name(version)
        if not zip_path.is_file():
            continue
        _validate_sha256_sidecar(zip_path)
        artifacts.append((policy.platform_key, zip_path))
    return tuple(artifacts)


def _reject_unexpected_release_files(
    *,
    dist_dir: Path,
    expected_zip_names: set[str],
    expected_sha_names: dict[str, str],
    expected_manifest_names: set[str],
) -> None:
    """拒絕 dist 內殘留的 release zip 或孤兒 sidecar，避免上傳錯檔。"""

    unexpected = [
        path.name
        for path in dist_dir.glob(f"{RELEASE_ASSET_PREFIX}-*.zip")
        if path.name not in expected_zip_names
    ]
    unexpected.extend(
        path.name
        for path in dist_dir.glob(f"{RELEASE_ASSET_PREFIX}-*.zip.sha256")
        if path.name not in expected_sha_names
        or not (dist_dir / expected_sha_names[path.name]).is_file()
    )
    unexpected.extend(
        path.name
        for path in dist_dir.glob(f"{RELEASE_ASSET_PREFIX}-*-manifest.json")
        if path.name not in expected_manifest_names
    )
    unexpected.extend(
        path.name
        for path in dist_dir.glob(f"{RELEASE_ASSET_PREFIX}-*-manifest.json.sig")
        if path.name not in expected_manifest_names
    )
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise ValueError(f"release_manifest_unexpected_artifact:{names}")


def _validate_sha256_sidecar(zip_path: Path) -> None:
    """確認 release zip 的同名 `.sha256` 存在且內容完全一致。"""

    sha_path = zip_path.with_name(release_sha256_asset_name(zip_path.name))
    if not sha_path.is_file():
        raise ValueError(f"release_manifest_sha256_missing:{sha_path}")
    actual = calculate_sha256(zip_path)
    expected = render_sha256_sidecar(actual, zip_path.name).strip()
    content = sha_path.read_text(encoding="ascii").strip()
    if content != expected:
        raise ValueError(f"release_manifest_sha256_mismatch:{sha_path.name}")


def _resolve_private_key_file(
    private_key_file: Path | None,
    *,
    private_key_b64: str,
) -> Path | None:
    """回傳私鑰檔；CLI/env key 缺席時才採用 repo 外預設檔。"""

    if private_key_file is not None:
        return private_key_file
    if private_key_b64.strip():
        return None
    if os.environ.get(PRIVATE_KEY_ENV, "").strip():
        return None
    if DEFAULT_PRIVATE_KEY_FILE.is_file():
        return DEFAULT_PRIVATE_KEY_FILE
    return None


def _validate_final_artifacts(
    *,
    version: str,
    dist_dir: Path,
    platforms: tuple[str, ...],
    expected_tag: str,
    expected_signer_subject: str,
) -> None:
    """逐平台驗證 finalized manifest / `.sig` 與 zip metadata 對齊。"""

    messages: list[str] = []
    for platform in platforms:
        result = validate_release_artifacts(
            version=version,
            dist_dir=dist_dir,
            platform_name=platform,
            expected_signer_subject=(
                expected_signer_subject if platform == "windows" else ""
            ),
            expected_tag=expected_tag,
            require_manifest=True,
        )
        if not result.ok:
            messages.extend(f"{platform}: {message}" for message in result.messages)
    if messages:
        detail = "; ".join(messages)
        raise ValueError(f"release_manifest_artifact_validation_failed:{detail}")


def main() -> int:
    """CLI entrypoint。"""

    args = parse_args()
    try:
        result = finalize_release_manifest(
            version=str(args.version),
            repository=str(args.repository),
            dist_dir=args.dist_dir,
            key_id=str(args.key_id),
            private_key_b64=str(args.private_key_b64),
            private_key_file=args.private_key_file,
            expected_tag=str(args.expected_tag),
            expected_signer_subject=str(args.expected_signer_subject),
            validate_artifacts=not bool(args.skip_artifact_validation),
            force=bool(args.force),
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"manifest: {result.manifest_path}")
    print(f"signature: {result.signature_path}")
    print("platforms: " + ", ".join(result.platforms))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
