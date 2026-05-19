"""Release artifact validation 測試。"""

from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

import pytest

from scripts.admin import release_artifact_validation as validation


def test_validate_release_artifacts_accepts_matching_zip_and_sha(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """zip、sha、版本資訊與必要檔案一致時通過。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "version_info.txt"
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
    monkeypatch.setattr(validation, "VERSION_INFO_FILE", version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert result.ok


def test_validate_release_artifacts_rejects_sha_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """`.sha256` 內容不一致時要失敗。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "version_info.txt"
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
    monkeypatch.setattr(validation, "VERSION_INFO_FILE", version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert not result.ok
    assert any("sha256 file mismatch" in message for message in result.messages)


def test_validate_release_artifacts_checks_exe_metadata_inside_zip(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """zip 內 EXE stale 時，即使 loose dist 目錄是新版也不能通過。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "version_info.txt"
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
    monkeypatch.setattr(validation, "VERSION_INFO_FILE", version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert not result.ok
    assert any("FileVersion mismatch" in message for message in result.messages)


def test_validate_release_artifacts_rejects_stale_fixed_file_info(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """StringStruct 正確但 FixedFileInfo stale 時仍要失敗。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "version_info.txt"
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
    monkeypatch.setattr(validation, "VERSION_INFO_FILE", version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert not result.ok
    assert any("filevers does not match" in message for message in result.messages)


def test_validate_release_artifacts_rejects_duplicate_zip_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """duplicate normalized zip entries 應失敗，避免 release 內容不確定。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "version_info.txt"
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
        for entry in validation.REQUIRED_ZIP_ENTRIES:
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
    monkeypatch.setattr(validation, "VERSION_INFO_FILE", version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert not result.ok
    assert any("zip duplicate entry" in message for message in result.messages)


def test_validate_release_artifacts_rejects_expected_tag_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """呼叫端提供 tag 時，必須與 version 對齊。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "version_info.txt"
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
        for entry in validation.REQUIRED_ZIP_ENTRIES:
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
    monkeypatch.setattr(validation, "VERSION_INFO_FILE", version_info)

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
    version_info = tmp_path / "version_info.txt"
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
        for entry in validation.REQUIRED_ZIP_ENTRIES:
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
    monkeypatch.setattr(validation, "VERSION_INFO_FILE", version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert result.ok


def test_validate_release_artifacts_rejects_missing_python_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """zip 缺 Python runtime DLL 時必須失敗。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "version_info.txt"
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
        for entry in validation.REQUIRED_ZIP_ENTRIES:
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
    monkeypatch.setattr(validation, "VERSION_INFO_FILE", version_info)

    result = validation.validate_release_artifacts(version="0.1.0", dist_dir=dist_dir)

    assert not result.ok
    assert any("python313.dll" in message for message in result.messages)


def test_validate_release_artifacts_checks_signer_on_zipped_exes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """signer validation 應套用到 zip 內 EXE，不是 loose dist app。"""

    dist_dir = tmp_path / "dist"
    version_info = tmp_path / "version_info.txt"
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
        for entry in validation.REQUIRED_ZIP_ENTRIES:
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
    monkeypatch.setattr(validation, "VERSION_INFO_FILE", version_info)

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
        _writestr_with_mode(archive, "facebook-monitor/facebook-monitor", "app", 0o755)
        _writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            "updater",
            0o755,
        )
        _writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            "chromium",
            0o755,
        )
        archive.writestr(
            "facebook-monitor/_internal/facebook_monitor/webapp/templates/index.html",
            "<html></html>",
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

    assert result.ok


def test_validate_release_artifacts_accepts_macos_chrome_for_testing_zip(
    tmp_path: Path,
) -> None:
    """macOS artifact validation 接受 Apple Silicon Playwright bundle 名稱。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        _writestr_with_mode(archive, "facebook-monitor/facebook-monitor", "app", 0o755)
        _writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            "updater",
            0o755,
        )
        _writestr_with_mode(
            archive,
            (
                "facebook-monitor/browser/Google Chrome for Testing.app/"
                "Contents/MacOS/Google Chrome for Testing"
            ),
            "chromium",
            0o755,
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

    assert result.ok


def test_validate_release_artifacts_rejects_macos_zip_without_executable_bit(
    tmp_path: Path,
) -> None:
    """macOS zip 若沒保留 executable bit，解壓後無法直接啟動。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        _writestr_with_mode(archive, "facebook-monitor/facebook-monitor", "app", 0o644)
        _writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            "updater",
            0o755,
        )
        _writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            "chromium",
            0o755,
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
    assert any("zip executable bit missing" in message for message in result.messages)


def test_validate_release_artifacts_rejects_macos_zip_with_private_data(
    tmp_path: Path,
) -> None:
    """macOS artifact 不可夾帶 data/profile/logs 類 runtime 私人資料。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        _writestr_with_mode(archive, "facebook-monitor/facebook-monitor", "app", 0o755)
        _writestr_with_mode(
            archive,
            "facebook-monitor/facebook-monitor-updater",
            "updater",
            0o755,
        )
        _writestr_with_mode(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
            "chromium",
            0o755,
        )
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


def _writestr_with_mode(
    archive: zipfile.ZipFile,
    name: str,
    content: str,
    mode: int,
) -> None:
    """寫入帶 POSIX mode 的 zip member。"""

    info = zipfile.ZipInfo(name)
    info.external_attr = (mode & 0o777) << 16
    archive.writestr(info, content)
