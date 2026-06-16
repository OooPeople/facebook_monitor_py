"""Admin tool：驗證 Web UI vendored frontend 檔案 provenance manifest。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_PATH = (
    ROOT
    / "src"
    / "facebook_monitor"
    / "webapp"
    / "static"
    / "vendor"
    / "frontend-vendor.manifest.json"
)
VENDOR_ROOT_RELATIVE = PurePosixPath("src/facebook_monitor/webapp/static/vendor")
REQUIRED_ASSET_FIELDS = (
    "name",
    "package",
    "version",
    "license",
    "source",
    "path",
    "sha256",
)
SHA256_PATTERN = re.compile(r"^[0-9A-F]{64}$")


def validate_frontend_vendor_manifest(
    *,
    root: Path = ROOT,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> list[str]:
    """回傳 frontend vendor manifest 的驗證錯誤清單。"""

    issues: list[str] = []
    payload = _load_json(manifest_path, issues)
    if payload is None:
        return issues
    if payload.get("schema_version") != 1:
        issues.append("schema_version must be 1")
    assets = payload.get("assets")
    if not isinstance(assets, list) or not assets:
        issues.append("assets must be a non-empty list")
        return issues
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            issues.append(f"assets[{index}] must be an object")
            continue
        issues.extend(_validate_asset(root=root, asset=asset, index=index))
    return issues


def _load_json(path: Path, issues: list[str]) -> dict[str, Any] | None:
    """讀取 manifest JSON；失敗時加入錯誤。"""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        issues.append(f"{path.as_posix()}: unable to read manifest: {exc}")
        return None
    except json.JSONDecodeError as exc:
        issues.append(f"{path.as_posix()}: invalid JSON: {exc}")
        return None
    if not isinstance(payload, dict):
        issues.append("manifest root must be an object")
        return None
    return payload


def _validate_asset(*, root: Path, asset: dict[str, Any], index: int) -> list[str]:
    """驗證單一 vendor asset 條目。"""

    issues: list[str] = []
    label = f"assets[{index}]"
    issues.extend(_validate_required_asset_fields(asset, label=label))
    path_text = str(asset.get("path") or "")
    sha256 = str(asset.get("sha256") or "")
    asset_path = _validate_asset_path(
        root=root,
        path_text=path_text,
        label=label,
        issues=issues,
    )
    _validate_asset_checksum(
        asset_path=asset_path,
        path_text=path_text,
        sha256=sha256,
        label=label,
        issues=issues,
    )
    return issues


def _validate_required_asset_fields(
    asset: dict[str, Any],
    *,
    label: str,
) -> list[str]:
    """驗證 asset provenance 必填欄位。"""

    issues: list[str] = []
    for field in REQUIRED_ASSET_FIELDS:
        value = asset.get(field)
        if not isinstance(value, str) or not value.strip():
            issues.append(f"{label}.{field} must be a non-empty string")
    return issues


def _validate_asset_path(
    *,
    root: Path,
    path_text: str,
    label: str,
    issues: list[str],
) -> Path | None:
    """驗證 asset path 並回傳已解析檔案路徑。"""

    if path_text and not _is_safe_repo_relative_path(path_text):
        issues.append(f"{label}.path must be a repo-relative POSIX path")
        return None
    if not path_text:
        return None
    asset_path = (root / Path(path_text)).resolve()
    root_path = root.resolve()
    vendor_root = (root / Path(VENDOR_ROOT_RELATIVE.as_posix())).resolve()
    try:
        asset_path.relative_to(root_path)
    except ValueError:
        issues.append(f"{label}.path resolves outside repo: {path_text}")
        return None
    try:
        asset_path.relative_to(vendor_root)
    except ValueError:
        issues.append(
            f"{label}.path must be inside frontend vendor directory: {path_text}"
        )
        return None
    if not asset_path.is_file():
        issues.append(f"{label}.path does not exist: {path_text}")
        return None
    return asset_path


def _validate_asset_checksum(
    *,
    asset_path: Path | None,
    path_text: str,
    sha256: str,
    label: str,
    issues: list[str],
) -> None:
    """驗證 asset SHA256 格式與實際檔案內容。"""

    if sha256 and not SHA256_PATTERN.fullmatch(sha256):
        issues.append(f"{label}.sha256 must be uppercase 64-char hex")
    if asset_path is None:
        return
    actual_sha256 = _sha256_file(asset_path)
    if sha256 and actual_sha256 != sha256:
        issues.append(
            f"{label}.sha256 mismatch for {path_text}: "
            f"expected {sha256}, actual {actual_sha256}"
        )


def _is_safe_repo_relative_path(path_text: str) -> bool:
    """檢查 manifest path 是否為 repo-relative POSIX path。"""

    if "\\" in path_text or ":" in path_text:
        return False
    if PureWindowsPath(path_text).drive:
        return False
    pure_path = PurePosixPath(path_text)
    if pure_path.is_absolute() or Path(path_text).is_absolute():
        return False
    return ".." not in pure_path.parts


def _sha256_file(path: Path) -> str:
    """回傳檔案 checkout bytes 的大寫 SHA256。"""

    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 CLI 參數。"""

    parser = argparse.ArgumentParser(
        description="Validate vendored frontend asset provenance manifest."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Path to frontend-vendor.manifest.json.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint。"""

    args = parse_args(argv)
    issues = validate_frontend_vendor_manifest(
        root=ROOT,
        manifest_path=args.manifest,
    )
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print("Frontend vendor manifest validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
