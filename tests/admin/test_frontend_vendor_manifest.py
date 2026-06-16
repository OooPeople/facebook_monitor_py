"""Frontend vendor manifest validation tests。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.admin.check_frontend_vendor_manifest import validate_frontend_vendor_manifest


VENDOR_ASSET_PATH = "src/facebook_monitor/webapp/static/vendor/vendor.js"


def test_frontend_vendor_manifest_validation_accepts_current_manifest() -> None:
    """repository 內的 frontend vendor manifest 必須對齊實際 vendored 檔案。"""

    assert validate_frontend_vendor_manifest() == []


def test_frontend_vendor_manifest_reports_checksum_mismatch(tmp_path: Path) -> None:
    """vendor 檔案異動但 manifest 未更新時應 fail。"""

    _write_vendor_asset(tmp_path, "console.log('changed');\n")
    manifest = _write_manifest(
        tmp_path,
        {
            "path": VENDOR_ASSET_PATH,
            "sha256": "A" * 64,
        },
    )

    issues = validate_frontend_vendor_manifest(root=tmp_path, manifest_path=manifest)

    assert len(issues) == 1
    assert "sha256 mismatch" in issues[0]
    assert "expected AAAAAAAAAAAA" in issues[0]
    assert "actual" in issues[0]


def test_frontend_vendor_manifest_reports_missing_asset(tmp_path: Path) -> None:
    """manifest 指到不存在的 vendor 檔案時應 fail。"""

    path = "src/facebook_monitor/webapp/static/vendor/missing.js"
    manifest = _write_manifest(
        tmp_path,
        {
            "path": path,
            "sha256": "A" * 64,
        },
    )

    issues = validate_frontend_vendor_manifest(root=tmp_path, manifest_path=manifest)

    assert issues == [f"assets[0].path does not exist: {path}"]


def test_frontend_vendor_manifest_requires_metadata_fields(tmp_path: Path) -> None:
    """version/license/source 等 provenance 欄位不可留空。"""

    _write_vendor_asset(tmp_path, "")
    manifest = _write_manifest(
        tmp_path,
        {
            "license": "",
            "source": "",
            "version": "",
            "path": VENDOR_ASSET_PATH,
            "sha256": "E3B0C44298FC1C149AFBF4C8996FB924"
            "27AE41E4649B934CA495991B7852B855",
        },
    )

    issues = validate_frontend_vendor_manifest(root=tmp_path, manifest_path=manifest)

    assert "assets[0].license must be a non-empty string" in issues
    assert "assets[0].source must be a non-empty string" in issues
    assert "assets[0].version must be a non-empty string" in issues


def test_frontend_vendor_manifest_rejects_paths_outside_repo(tmp_path: Path) -> None:
    """manifest path 必須是 repo-relative POSIX path。"""

    manifest = _write_manifest(
        tmp_path,
        {
            "path": "../outside.js",
            "sha256": "A" * 64,
        },
    )

    issues = validate_frontend_vendor_manifest(root=tmp_path, manifest_path=manifest)

    assert issues == ["assets[0].path must be a repo-relative POSIX path"]


def test_frontend_vendor_manifest_rejects_repo_files_outside_vendor_dir(
    tmp_path: Path,
) -> None:
    """manifest 只能列 Web UI vendored frontend 檔案。"""

    non_vendor_asset = tmp_path / "docs" / "not-vendor.js"
    non_vendor_asset.parent.mkdir(parents=True, exist_ok=True)
    non_vendor_asset.write_text("console.log('not vendor');\n", encoding="utf-8")
    manifest = _write_manifest(
        tmp_path,
        {
            "path": "docs/not-vendor.js",
            "sha256": "A" * 64,
        },
    )

    issues = validate_frontend_vendor_manifest(root=tmp_path, manifest_path=manifest)

    assert issues == [
        "assets[0].path must be inside frontend vendor directory: docs/not-vendor.js"
    ]


@pytest.mark.parametrize(
    "path_text",
    [
        "/absolute/vendor.js",
        "C:/absolute/vendor.js",
        "C:relative/vendor.js",
        "vendor\\sortable.js",
        "../outside.js",
    ],
)
def test_frontend_vendor_manifest_rejects_non_posix_repo_relative_paths(
    tmp_path: Path,
    path_text: str,
) -> None:
    """Windows 與 POSIX 絕對路徑都不可混入 manifest。"""

    manifest = _write_manifest(
        tmp_path,
        {
            "path": path_text,
            "sha256": "A" * 64,
        },
    )

    issues = validate_frontend_vendor_manifest(root=tmp_path, manifest_path=manifest)

    assert issues == ["assets[0].path must be a repo-relative POSIX path"]


def _write_manifest(
    root: Path,
    overrides: dict[str, str],
) -> Path:
    """寫出測試用 frontend vendor manifest。"""

    asset = {
        "name": "Vendor",
        "package": "vendor",
        "version": "1.0.0",
        "license": "MIT",
        "source": "https://example.test/vendor",
        "path": VENDOR_ASSET_PATH,
        "sha256": "A" * 64,
        **overrides,
    }
    manifest = root / "frontend-vendor.manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "assets": [asset]}),
        encoding="utf-8",
    )
    return manifest


def _write_vendor_asset(root: Path, content: str) -> Path:
    """寫出測試用 vendored frontend asset。"""

    asset = root / Path(VENDOR_ASSET_PATH)
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_text(content, encoding="utf-8")
    return asset
