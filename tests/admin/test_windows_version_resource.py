"""Windows version resource generator tests。"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.admin.windows_version_resource import render_windows_version_info
from scripts.admin.windows_version_resource import render_windows_version_info_for_executable
from scripts.admin.windows_version_resource import windows_file_version
from scripts.admin.windows_version_resource import windows_version_tuple
from scripts.admin.windows_version_resource import write_windows_version_info


def test_render_windows_version_info_uses_release_version() -> None:
    """Windows version resource 內容直接由 release version 產生。"""

    text = render_windows_version_info("1.2.3")

    assert "filevers=(1, 2, 3, 0)" in text
    assert "prodvers=(1, 2, 3, 0)" in text
    assert "StringStruct('FileVersion', '1.2.3.0')" in text
    assert "StringStruct('OriginalFilename', 'facebook-monitor.exe')" in text
    assert "StringStruct('ProductVersion', '1.2.3')" in text


def test_render_windows_version_info_supports_updater_identity() -> None:
    """updater EXE 不應共用主程式的 OriginalFilename。"""

    text = render_windows_version_info_for_executable(
        "1.2.3",
        internal_name="facebook-monitor-updater",
        original_filename="facebook-monitor-updater.exe",
    )

    assert "StringStruct('InternalName', 'facebook-monitor-updater')" in text
    assert "StringStruct('OriginalFilename', 'facebook-monitor-updater.exe')" in text


def test_windows_version_tuple_uses_rc_number_as_build() -> None:
    """rc 版本會把 rc number 放進 Windows build 欄位。"""

    assert windows_version_tuple("1.2.3-rc4") == (1, 2, 3, 4)
    assert windows_file_version("1.2.3-rc4") == "1.2.3.4"


def test_windows_version_tuple_rejects_unsupported_version() -> None:
    """不支援的版本格式要明確失敗，避免產生錯誤 resource。"""

    with pytest.raises(ValueError, match="unsupported release version"):
        windows_version_tuple("1.2")


def test_write_windows_version_info_writes_destination(tmp_path: Path) -> None:
    """產生器可寫出 PyInstaller 可讀的 version resource 檔。"""

    output = tmp_path / "windows_version_info.txt"

    result = write_windows_version_info(
        output,
        version="1.2.3",
        internal_name="facebook-monitor-updater",
        original_filename="facebook-monitor-updater.exe",
    )

    assert result == output
    text = output.read_text(encoding="utf-8")
    assert "StringStruct('ProductVersion', '1.2.3')" in text
    assert "StringStruct('OriginalFilename', 'facebook-monitor-updater.exe')" in text
