"""Release artifact validation 測試。"""

from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

import pytest

from scripts.admin import release_artifact_validation as validation
from tests.helpers.macos_bundle import MACHO_ARM64_BYTES
from tests.helpers.macos_bundle import writestr_symlink
from tests.helpers.macos_bundle import writestr_with_mode
from tests.helpers.macos_bundle import write_macos_app_bundle_to_zip


def _set_windows_version_resources(monkeypatch, version_info: Path) -> None:
    """將舊測試 fixture 轉成 app/updater 兩份 generated version resource。"""

    text = version_info.read_text(encoding="utf-8")
    app_info = version_info.with_name("windows_app_version_info.txt")
    updater_info = version_info.with_name("windows_updater_version_info.txt")
    app_info.write_text(
        text
        + "StringStruct('InternalName', 'facebook-monitor')\n"
        + "StringStruct('OriginalFilename', 'facebook-monitor.exe')\n",
        encoding="utf-8",
    )
    updater_info.write_text(
        text
        + "StringStruct('InternalName', 'facebook-monitor-updater')\n"
        + "StringStruct('OriginalFilename', 'facebook-monitor-updater.exe')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        validation,
        "WINDOWS_VERSION_RESOURCE_FILES",
        (
            validation.WindowsVersionResourceFile(
                path=app_info,
                internal_name="facebook-monitor",
                original_filename="facebook-monitor.exe",
            ),
            validation.WindowsVersionResourceFile(
                path=updater_info,
                internal_name="facebook-monitor-updater",
                original_filename="facebook-monitor-updater.exe",
            ),
        ),
    )


def test_validate_release_artifacts_accepts_matching_zip_and_sha(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """zip、sha、版本資訊與必要檔案一致時通過。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "windows_version_info.txt"
    version_info.write_text(
        "filevers=(0, 1, 0, 0)\n"
        "prodvers=(0, 1, 0, 0)\n"
        "StringStruct('ProductVersion', '0.1.0')\n"
        "StringStruct('FileVersion', '0.1.0.0')\n",
        encoding="utf-8",
    )
    app_dir = dist_dir / "facebook-monitor"
    (app_dir / "_internal" / "browser").mkdir(parents=True)
    (app_dir / "_internal" / "assets").mkdir(parents=True)
    required_files = (
        app_dir / "facebook-monitor.exe",
        app_dir / "facebook-monitor-updater.exe",
        app_dir / "_internal" / "python313.dll",
        app_dir / "_internal" / "browser" / "chrome.exe",
        app_dir / "_internal" / "assets" / "facebook-monitor.ico",
        app_dir / "_internal" / "assets" / "facebook-monitor-tray.ico",
    )
    for path in required_files:
        path.write_text("x", encoding="utf-8")
    zip_path = dist_dir / "facebook-monitor-0.1.0-windows-portable.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in required_files:
            archive.write(path, path.relative_to(dist_dir))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    monkeypatch.setattr(
        validation,
        "_read_windows_version_info",
        lambda path: ("0.1.0.0", "0.1.0"),
    )
    _set_windows_version_resources(monkeypatch, version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert result.ok


def test_validate_release_artifacts_rejects_generated_resource_identity_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """generated version resource 檔也必須分別對齊 app/updater identity。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-windows-portable.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for entry in validation.WINDOWS_REQUIRED_ZIP_ENTRIES:
            archive.writestr(entry, "x")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )
    version_info = tmp_path / "windows_version_info.txt"
    version_info.write_text(
        "filevers=(0, 1, 0, 0)\n"
        "prodvers=(0, 1, 0, 0)\n"
        "StringStruct('ProductVersion', '0.1.0')\n"
        "StringStruct('FileVersion', '0.1.0.0')\n",
        encoding="utf-8",
    )
    _set_windows_version_resources(monkeypatch, version_info)
    updater_resource = validation.WINDOWS_VERSION_RESOURCE_FILES[1]
    updater_resource.path.write_text(
        version_info.read_text(encoding="utf-8")
        + "StringStruct('InternalName', 'facebook-monitor-updater')\n"
        + "StringStruct('OriginalFilename', 'facebook-monitor.exe')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        validation,
        "_read_windows_version_info",
        lambda path: ("0.1.0.0", "0.1.0"),
    )

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert not result.ok
    assert any(
        "windows version resource OriginalFilename does not match"
        in message
        for message in result.messages
    )


def test_validate_release_artifacts_rejects_sha_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """`.sha256` 內容不一致時要失敗。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "windows_version_info.txt"
    version_info.write_text(
        "filevers=(0, 1, 0, 0)\n"
        "prodvers=(0, 1, 0, 0)\n"
        "StringStruct('ProductVersion', '0.1.0')\n"
        "StringStruct('FileVersion', '0.1.0.0')\n",
        encoding="utf-8",
    )
    app_dir = dist_dir / "facebook-monitor"
    (app_dir / "_internal" / "browser").mkdir(parents=True)
    (app_dir / "_internal" / "assets").mkdir(parents=True)
    required_files = (
        app_dir / "facebook-monitor.exe",
        app_dir / "facebook-monitor-updater.exe",
        app_dir / "_internal" / "python313.dll",
        app_dir / "_internal" / "browser" / "chrome.exe",
        app_dir / "_internal" / "assets" / "facebook-monitor.ico",
        app_dir / "_internal" / "assets" / "facebook-monitor-tray.ico",
    )
    for path in required_files:
        path.write_text("x", encoding="utf-8")
    zip_path = dist_dir / "facebook-monitor-0.1.0-windows-portable.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in required_files:
            archive.write(path, path.relative_to(dist_dir))
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{'0' * 64}  {zip_path.name}",
        encoding="ascii",
    )
    monkeypatch.setattr(
        validation,
        "_read_windows_version_info",
        lambda path: ("0.1.0.0", "0.1.0"),
    )
    _set_windows_version_resources(monkeypatch, version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert not result.ok
    assert any("sha256 file mismatch" in message for message in result.messages)


def test_validate_release_artifacts_rejects_windows_zip_with_private_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Windows artifact 也不可夾帶 profiles/logs/session 類 runtime 私人資料。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "windows_version_info.txt"
    version_info.write_text(
        "filevers=(0, 1, 0, 0)\n"
        "prodvers=(0, 1, 0, 0)\n"
        "StringStruct('ProductVersion', '0.1.0')\n"
        "StringStruct('FileVersion', '0.1.0.0')\n",
        encoding="utf-8",
    )
    zip_path = dist_dir / "facebook-monitor-0.1.0-windows-portable.zip"
    dist_dir.mkdir()
    with zipfile.ZipFile(zip_path, "w") as archive:
        for name in (
            "facebook-monitor/facebook-monitor.exe",
            "facebook-monitor/facebook-monitor-updater.exe",
            "facebook-monitor/_internal/python313.dll",
            "facebook-monitor/_internal/browser/chrome.exe",
            "facebook-monitor/_internal/assets/facebook-monitor.ico",
            "facebook-monitor/_internal/assets/facebook-monitor-tray.ico",
        ):
            archive.writestr(name, "x")
        archive.writestr("facebook-monitor/profiles/automation_default/Cookies", "private")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )
    monkeypatch.setattr(
        validation,
        "_read_windows_version_info",
        lambda path: ("0.1.0.0", "0.1.0"),
    )
    _set_windows_version_resources(monkeypatch, version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert not result.ok
    assert any("runtime/private data" in message for message in result.messages)


def test_validate_release_artifacts_checks_exe_metadata_inside_zip(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """zip 內 EXE stale 時，即使 loose dist 目錄是新版也不能通過。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "windows_version_info.txt"
    version_info.write_text(
        "filevers=(0, 1, 0, 0)\n"
        "prodvers=(0, 1, 0, 0)\n"
        "StringStruct('ProductVersion', '0.1.0')\n"
        "StringStruct('FileVersion', '0.1.0.0')\n",
        encoding="utf-8",
    )
    app_dir = dist_dir / "facebook-monitor"
    (app_dir / "_internal" / "browser").mkdir(parents=True)
    (app_dir / "_internal" / "assets").mkdir(parents=True)
    required_files = (
        app_dir / "facebook-monitor.exe",
        app_dir / "facebook-monitor-updater.exe",
        app_dir / "_internal" / "python313.dll",
        app_dir / "_internal" / "browser" / "chrome.exe",
        app_dir / "_internal" / "assets" / "facebook-monitor.ico",
        app_dir / "_internal" / "assets" / "facebook-monitor-tray.ico",
    )
    for path in required_files:
        path.write_text("valid", encoding="utf-8")
    zip_path = dist_dir / "facebook-monitor-0.1.0-windows-portable.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in required_files:
            if path.name.endswith(".exe"):
                archive.writestr(str(path.relative_to(dist_dir)).replace("\\", "/"), "stale")
            else:
                archive.write(path, path.relative_to(dist_dir))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    def fake_version_info(path: Path) -> tuple[str, str]:
        if path.read_text(encoding="utf-8") == "valid":
            return "0.1.0.0", "0.1.0"
        return "0.0.9.0", "0.0.9"

    monkeypatch.setattr(validation, "_read_windows_version_info", fake_version_info)
    _set_windows_version_resources(monkeypatch, version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert not result.ok
    assert any("FileVersion mismatch" in message for message in result.messages)


def test_validate_release_artifacts_rejects_updater_original_filename_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """updater EXE 的 OriginalFilename 不可沿用主程式檔名。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "windows_version_info.txt"
    version_info.write_text(
        "filevers=(0, 1, 0, 0)\n"
        "prodvers=(0, 1, 0, 0)\n"
        "StringStruct('ProductVersion', '0.1.0')\n"
        "StringStruct('FileVersion', '0.1.0.0')\n",
        encoding="utf-8",
    )
    zip_path = dist_dir / "facebook-monitor-0.1.0-windows-portable.zip"
    dist_dir.mkdir()
    with zipfile.ZipFile(zip_path, "w") as archive:
        for entry in validation.WINDOWS_REQUIRED_ZIP_ENTRIES:
            archive.writestr(entry, entry)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    def fake_version_info(path: Path) -> validation.WindowsExeVersionInfo:
        original_filename = (
            ""
            if path.name == "facebook-monitor-updater.exe"
            else path.name
        )
        return validation.WindowsExeVersionInfo(
            file_version="0.1.0.0",
            product_version="0.1.0",
            original_filename=original_filename,
        )

    monkeypatch.setattr(validation, "_read_windows_version_info", fake_version_info)
    _set_windows_version_resources(monkeypatch, version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert not result.ok
    assert any("OriginalFilename mismatch" in message for message in result.messages)


def test_validate_release_artifacts_rejects_stale_fixed_file_info(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """StringStruct 正確但 FixedFileInfo stale 時仍要失敗。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "windows_version_info.txt"
    version_info.write_text(
        "filevers=(0, 0, 9, 0)\n"
        "prodvers=(0, 0, 9, 0)\n"
        "StringStruct('ProductVersion', '0.1.0')\n"
        "StringStruct('FileVersion', '0.1.0.0')\n",
        encoding="utf-8",
    )
    app_dir = dist_dir / "facebook-monitor"
    (app_dir / "_internal" / "browser").mkdir(parents=True)
    (app_dir / "_internal" / "assets").mkdir(parents=True)
    required_files = (
        app_dir / "facebook-monitor.exe",
        app_dir / "facebook-monitor-updater.exe",
        app_dir / "_internal" / "python313.dll",
        app_dir / "_internal" / "browser" / "chrome.exe",
        app_dir / "_internal" / "assets" / "facebook-monitor.ico",
        app_dir / "_internal" / "assets" / "facebook-monitor-tray.ico",
    )
    for path in required_files:
        path.write_text("x", encoding="utf-8")
    zip_path = dist_dir / "facebook-monitor-0.1.0-windows-portable.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in required_files:
            archive.write(path, path.relative_to(dist_dir))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )
    monkeypatch.setattr(
        validation,
        "_read_windows_version_info",
        lambda path: ("0.1.0.0", "0.1.0"),
    )
    _set_windows_version_resources(monkeypatch, version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert not result.ok
    assert any("filevers does not match" in message for message in result.messages)


def test_validate_release_artifacts_rejects_duplicate_zip_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """duplicate normalized zip entries 應失敗，避免 release 內容不確定。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "windows_version_info.txt"
    version_info.write_text(
        "filevers=(0, 1, 0, 0)\n"
        "prodvers=(0, 1, 0, 0)\n"
        "StringStruct('ProductVersion', '0.1.0')\n"
        "StringStruct('FileVersion', '0.1.0.0')\n",
        encoding="utf-8",
    )
    zip_path = dist_dir / "facebook-monitor-0.1.0-windows-portable.zip"
    dist_dir.mkdir()
    with zipfile.ZipFile(zip_path, "w") as archive:
        for entry in validation.WINDOWS_REQUIRED_ZIP_ENTRIES:
            archive.writestr(entry, "x")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("facebook-monitor/facebook-monitor.exe", "duplicate")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )
    monkeypatch.setattr(
        validation,
        "_read_windows_version_info",
        lambda path: ("0.1.0.0", "0.1.0"),
    )
    _set_windows_version_resources(monkeypatch, version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert not result.ok
    assert any("zip duplicate entry" in message for message in result.messages)


def test_validate_release_artifacts_rejects_expected_tag_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """呼叫端提供 tag 時，必須與 version 對齊。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "windows_version_info.txt"
    version_info.write_text(
        "filevers=(0, 1, 0, 0)\n"
        "prodvers=(0, 1, 0, 0)\n"
        "StringStruct('ProductVersion', '0.1.0')\n"
        "StringStruct('FileVersion', '0.1.0.0')\n",
        encoding="utf-8",
    )
    zip_path = dist_dir / "facebook-monitor-0.1.0-windows-portable.zip"
    dist_dir.mkdir()
    with zipfile.ZipFile(zip_path, "w") as archive:
        for entry in validation.WINDOWS_REQUIRED_ZIP_ENTRIES:
            archive.writestr(entry, "x")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )
    monkeypatch.setattr(
        validation,
        "_read_windows_version_info",
        lambda path: ("0.1.0.0", "0.1.0"),
    )
    _set_windows_version_resources(monkeypatch, version_info)

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        expected_tag="v0.1.1",
    )

    assert not result.ok
    assert any("expected tag mismatch" in message for message in result.messages)


def test_validate_release_artifacts_accepts_zip_without_loose_dist_app(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """artifact validation 的 source of truth 是 zip，不依賴 loose dist app 目錄。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "windows_version_info.txt"
    version_info.write_text(
        "filevers=(0, 1, 0, 0)\n"
        "prodvers=(0, 1, 0, 0)\n"
        "StringStruct('ProductVersion', '0.1.0')\n"
        "StringStruct('FileVersion', '0.1.0.0')\n",
        encoding="utf-8",
    )
    zip_path = dist_dir / "facebook-monitor-0.1.0-windows-portable.zip"
    dist_dir.mkdir()
    with zipfile.ZipFile(zip_path, "w") as archive:
        for entry in validation.WINDOWS_REQUIRED_ZIP_ENTRIES:
            archive.writestr(entry, "x")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )
    monkeypatch.setattr(
        validation,
        "_read_windows_version_info",
        lambda path: ("0.1.0.0", "0.1.0"),
    )
    _set_windows_version_resources(monkeypatch, version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert result.ok


def test_validate_release_artifacts_rejects_missing_python_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """zip 缺 Python runtime DLL 時必須失敗。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "windows_version_info.txt"
    version_info.write_text(
        "filevers=(0, 1, 0, 0)\n"
        "prodvers=(0, 1, 0, 0)\n"
        "StringStruct('ProductVersion', '0.1.0')\n"
        "StringStruct('FileVersion', '0.1.0.0')\n",
        encoding="utf-8",
    )
    zip_path = dist_dir / "facebook-monitor-0.1.0-windows-portable.zip"
    dist_dir.mkdir()
    with zipfile.ZipFile(zip_path, "w") as archive:
        for entry in validation.WINDOWS_REQUIRED_ZIP_ENTRIES:
            if entry.endswith("python313.dll"):
                continue
            archive.writestr(entry, "x")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )
    monkeypatch.setattr(
        validation,
        "_read_windows_version_info",
        lambda path: ("0.1.0.0", "0.1.0"),
    )
    _set_windows_version_resources(monkeypatch, version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert not result.ok
    assert any("python313.dll" in message for message in result.messages)


def test_validate_release_artifacts_checks_signer_on_zipped_exes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """signer validation 應套用到 zip 內 EXE，不是 loose dist app。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "windows_version_info.txt"
    version_info.write_text(
        "filevers=(0, 1, 0, 0)\n"
        "prodvers=(0, 1, 0, 0)\n"
        "StringStruct('ProductVersion', '0.1.0')\n"
        "StringStruct('FileVersion', '0.1.0.0')\n",
        encoding="utf-8",
    )
    zip_path = dist_dir / "facebook-monitor-0.1.0-windows-portable.zip"
    dist_dir.mkdir()
    with zipfile.ZipFile(zip_path, "w") as archive:
        for entry in validation.WINDOWS_REQUIRED_ZIP_ENTRIES:
            content = "bad-signer" if entry.endswith(".exe") else "x"
            archive.writestr(entry, content)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )
    monkeypatch.setattr(
        validation,
        "_read_windows_version_info",
        lambda path: ("0.1.0.0", "0.1.0"),
    )

    def fake_signature(path: Path) -> tuple[str, str]:
        if path.read_text(encoding="utf-8") == "bad-signer":
            return "Valid", "CN=Unexpected Publisher"
        return "Valid", "CN=Expected Publisher"

    monkeypatch.setattr(validation, "_read_authenticode_signature", fake_signature)
    _set_windows_version_resources(monkeypatch, version_info)

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        expected_signer_subject="Expected Publisher",
    )

    assert not result.ok
    assert any("signer subject mismatch" in message for message in result.messages)


def test_validate_release_artifacts_accepts_macos_arm64_onedir_zip(
    tmp_path: Path,
) -> None:
    """macOS onedir zip、sha 與必要 executable / browser entry 一致時通過。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor",
            MACHO_ARM64_BYTES + b"app",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        archive.writestr(
            "facebook-monitor/_internal/facebook_monitor/webapp/templates/index.html",
            "<html></html>",
        )
        _write_macos_app_bundle(archive)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert result.ok


def test_validate_release_artifacts_accepts_macos_symlink_to_sibling_in_root(
    tmp_path: Path,
) -> None:
    """release validation 與 runtime extraction 共用 zip symlink 相對路徑規則。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor",
            MACHO_ARM64_BYTES + b"app",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        _write_macos_app_bundle(archive)
        writestr_symlink(
            archive,
            "facebook-monitor/bin/updater-link",
            "../facebook-monitor-updater",
        )
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert result.ok, result.messages


def test_validate_release_artifacts_accepts_macos_chrome_for_testing_zip(
    tmp_path: Path,
) -> None:
    """macOS artifact validation 接受 Apple Silicon Playwright bundle 名稱。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor",
            MACHO_ARM64_BYTES + b"app",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            (
                "facebook-monitor/browser/Google Chrome for Testing.app/"
                "Contents/MacOS/Google Chrome for Testing"
            ),
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        _write_macos_app_bundle(archive)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert result.ok


def test_validate_release_artifacts_rejects_macos_browser_outside_layout(
    tmp_path: Path,
) -> None:
    """macOS bundled browser 必須位於 updater layout policy 接受的精確路徑。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor",
            MACHO_ARM64_BYTES + b"app",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/not-browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        _write_macos_app_bundle(archive)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert not result.ok
    assert any("macOS Chromium executable" in message for message in result.messages)


def test_validate_release_artifacts_rejects_macos_zip_without_executable_bit(
    tmp_path: Path,
) -> None:
    """macOS zip 若沒保留 executable bit，解壓後無法直接啟動。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor",
            MACHO_ARM64_BYTES + b"app",
            0o644,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        _write_macos_app_bundle(archive)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert not result.ok
    assert any("zip executable bit missing" in message for message in result.messages)


def test_validate_release_artifacts_rejects_macos_zip_with_private_data(
    tmp_path: Path,
) -> None:
    """macOS artifact 不可夾帶 data/profile/logs 類 runtime 私人資料。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor",
            MACHO_ARM64_BYTES + b"app",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        _write_macos_app_bundle(archive)
        archive.writestr("facebook-monitor/data/app.db", "private")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert not result.ok
    assert any("runtime/private data" in message for message in result.messages)


def test_validate_release_artifacts_rejects_macos_symlink_to_private_data(
    tmp_path: Path,
) -> None:
    """release validation 也要擋下 symlink 指向 runtime private data。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor",
            MACHO_ARM64_BYTES + b"app",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        _write_macos_app_bundle(archive)
        writestr_symlink(archive, "facebook-monitor/profile-link", "data/profiles")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert not result.ok
    assert any("zip symlink target unsafe" in message for message in result.messages)


def test_validate_release_artifacts_rejects_macos_backslash_symlink_target(
    tmp_path: Path,
) -> None:
    """release validation 不把 backslash target 正規化成另一條 POSIX symlink。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor",
            MACHO_ARM64_BYTES + b"app",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        _write_macos_app_bundle(archive)
        writestr_symlink(
            archive,
            "facebook-monitor/lib-link",
            "_internal\\lib.dylib",
        )
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert not result.ok
    assert any("zip symlink target invalid" in message for message in result.messages)


def test_validate_release_artifacts_rejects_member_under_symlink(
    tmp_path: Path,
) -> None:
    """release validation 不可接受 symlink path 下另有 member。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor",
            MACHO_ARM64_BYTES + b"app",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        _write_macos_app_bundle(archive)
        writestr_symlink(archive, "facebook-monitor/link", "_internal")
        archive.writestr("facebook-monitor/link/file.txt", "unsafe")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert not result.ok
    assert any("zip member path unsafe" in message for message in result.messages)


def test_validate_release_artifacts_rejects_oversized_symlink_target(
    tmp_path: Path,
) -> None:
    """release validation 讀取 symlink target 前需先套用大小限制。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor",
            MACHO_ARM64_BYTES + b"app",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        _write_macos_app_bundle(archive)
        writestr_symlink(
            archive,
            "facebook-monitor/huge-link",
            "a" * (validation.MAX_ZIP_SYMLINK_TARGET_BYTES + 1),
        )
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert not result.ok
    assert any("zip symlink target too large" in message for message in result.messages)


def test_validate_release_artifacts_rejects_macos_shell_app_launcher(
    tmp_path: Path,
) -> None:
    """macOS `.app` launcher 不可退回會讓 Dock item 消失的 shell wrapper。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor",
            MACHO_ARM64_BYTES + b"app",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        _write_macos_app_bundle(archive, launcher_content=b"#!/bin/sh\nexec app\n")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert not result.ok
    assert any("arm64 Mach-O" in message for message in result.messages)


def test_validate_release_artifacts_rejects_macos_non_macho_root_executable(
    tmp_path: Path,
) -> None:
    """macOS root app/updater/browser 不能只是有 executable bit，還要是 arm64 Mach-O。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(archive, "facebook-monitor/facebook-monitor", b"not macho", 0o755)
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        _write_macos_app_bundle(archive)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert not result.ok
    assert any("zip executable must be arm64 Mach-O" in message for message in result.messages)


def test_validate_release_artifacts_rejects_macos_stale_app_bundle_version(
    tmp_path: Path,
) -> None:
    """macOS `.app` Info.plist version 也必須與 release version 對齊。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor",
            MACHO_ARM64_BYTES + b"app",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        _write_macos_app_bundle(archive, version="0.0.9")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert not result.ok
    assert any("short version" in message for message in result.messages)
    assert any("bundle version" in message for message in result.messages)


def test_validate_release_artifacts_rejects_macos_bundle_identifier_mismatch(
    tmp_path: Path,
) -> None:
    """macOS `.app` bundle id 必須維持通知權限使用的主 app identity。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor",
            MACHO_ARM64_BYTES + b"app",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        _write_macos_app_bundle(
            archive,
            extra_plist_values={"CFBundleIdentifier": "com.example.other"},
        )
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert not result.ok
    assert any("bundle identifier" in message for message in result.messages)


def test_validate_release_artifacts_rejects_macos_string_hidden_dock_plist(
    tmp_path: Path,
) -> None:
    """macOS Info.plist 用字串表示 LSUIElement 也不可讓 app 隱藏 Dock。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor",
            MACHO_ARM64_BYTES + b"app",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        _write_macos_app_bundle(archive, extra_plist_values={"LSUIElement": "1"})
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert not result.ok
    assert any("visible in Dock" in message for message in result.messages)


def test_validate_release_artifacts_rejects_macos_integer_background_only_plist(
    tmp_path: Path,
) -> None:
    """macOS Info.plist 用非零 integer 表示 LSBackgroundOnly 也不可隱藏 Dock。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor",
            MACHO_ARM64_BYTES + b"app",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            MACHO_ARM64_BYTES + b"updater",
            0o755,
        )
        writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"chromium",
            0o755,
        )
        _write_macos_app_bundle(archive, extra_plist_values={"LSBackgroundOnly": 2})
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}",
        encoding="ascii",
    )

    result = validation.validate_release_artifacts(
        version="0.1.0",
        dist_dir=dist_dir,
        platform_name="macos-arm64",
    )

    assert not result.ok
    assert any("visible in Dock" in message for message in result.messages)


def _write_macos_app_bundle(
    archive: zipfile.ZipFile,
    *,
    version: str = "0.1.0",
    launcher_content: bytes = MACHO_ARM64_BYTES,
    extra_plist_values: dict[str, object] | None = None,
) -> None:
    """寫入測試用 Finder/Dock `.app` launcher bundle。"""

    write_macos_app_bundle_to_zip(
        archive,
        version=version,
        launcher_content=launcher_content,
        extra_plist_values=extra_plist_values,
    )
