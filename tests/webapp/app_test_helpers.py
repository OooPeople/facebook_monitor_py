"""Shared helpers for Web UI route tests."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from facebook_monitor.runtime.paths import resolve_runtime_paths
from facebook_monitor.updates.download import UpdateDownloadResult
from facebook_monitor.updates.download import VERIFIED_DOWNLOAD_SET_MARKER_NAME
from facebook_monitor.updates.download import VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION
from facebook_monitor.updates.manifest import release_manifest_asset_name
from facebook_monitor.updates.manifest import release_manifest_signature_asset_name
from facebook_monitor.updates.release_check import UpdateCheckResult
from facebook_monitor.webapp.app import create_app as create_production_app


def create_app(**kwargs):
    """Web route tests 預設關閉 CSRF；CSRF 專門測試使用 production factory。"""

    kwargs.setdefault("enforce_csrf", False)
    return create_production_app(**kwargs)


def page_feedback(response_text: str) -> dict[str, object]:
    """讀取頁面 toast feedback JSON。"""

    match = re.search(
        r'<template id="page-feedback">(.+?)</template>',
        response_text,
        re.DOTALL,
    )
    assert match is not None
    payload = json.loads(match.group(1))
    assert isinstance(payload, dict)
    return payload


def make_supported_update_paths(tmp_path: Path):
    """建立 settings 更新 route 測試用的 PyInstaller-like app root。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.app_base_dir.mkdir(parents=True, exist_ok=True)
    (paths.app_base_dir / "facebook-monitor-updater.exe").write_text(
        "updater",
        encoding="utf-8",
    )
    return paths


def make_supported_macos_update_paths(tmp_path: Path):
    """建立 settings 更新 route 測試用的 macOS onedir-like app root。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.app_base_dir.mkdir(parents=True, exist_ok=True)
    (paths.app_base_dir / "facebook-monitor-updater").write_text(
        "updater",
        encoding="utf-8",
    )
    return paths


def verified_update_download_result(
    *,
    update_check: UpdateCheckResult,
    file_path: Path,
) -> UpdateDownloadResult:
    """建立包含 signed manifest metadata 的 settings 測試下載結果。"""

    manifest_path = file_path.with_name(release_manifest_asset_name(update_check.latest_version))
    signature_path = file_path.with_name(
        release_manifest_signature_asset_name(update_check.latest_version)
    )
    manifest_path.write_text("manifest", encoding="utf-8")
    signature_path.write_text("sig", encoding="utf-8")
    actual_sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    sha256_path = file_path.with_name(file_path.name + ".sha256")
    sha256_path.write_text(f"{actual_sha256}  {file_path.name}\n", encoding="utf-8")
    marker_path = file_path.parent / VERIFIED_DOWNLOAD_SET_MARKER_NAME
    marker_path.write_text(
        json.dumps(
            {
                "schema_version": VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION,
                "asset_name": file_path.name,
                "asset_sha256": actual_sha256,
                "asset_size": file_path.stat().st_size,
                "sha256_name": sha256_path.name,
                "sha256_sha256": hashlib.sha256(sha256_path.read_bytes()).hexdigest(),
                "manifest_name": manifest_path.name,
                "manifest_sha256": manifest_sha256,
                "manifest_key_id": "test-key",
                "manifest_signature_name": signature_path.name,
                "manifest_signature_sha256": hashlib.sha256(
                    signature_path.read_bytes()
                ).hexdigest(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return UpdateDownloadResult(
        status="verified",
        downloaded=True,
        verified=True,
        file_path=file_path,
        sha256_path=sha256_path,
        expected_sha256=actual_sha256,
        actual_sha256=actual_sha256,
        failure_reason="",
        manifest_path=manifest_path,
        manifest_signature_path=signature_path,
        manifest_sha256=manifest_sha256,
        manifest_key_id="test-key",
        verified_set_marker_path=marker_path,
    )
