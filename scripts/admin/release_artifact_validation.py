"""Admin tool：驗證 release artifact 一致性。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
from pathlib import PurePosixPath
import re
import struct
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.version import APP_VERSION
from facebook_monitor.updates.artifacts import MACOS_ARM64_ONEDIR_SUFFIX
from facebook_monitor.updates.artifacts import WINDOWS_PORTABLE_SUFFIX
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_INFO_PLIST
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER
from facebook_monitor.updates.platforms import MACOS_APP_ENTRY
from facebook_monitor.updates.platforms import MACOS_ARM64_LAYOUT_POLICY
from facebook_monitor.updates.platforms import MACOS_UPDATER_ENTRY


VERSION_INFO_FILE = ROOT / "packaging" / "pyinstaller" / "version_info.txt"
MAX_ZIP_ENTRIES = 50_000
MAX_ZIP_SINGLE_FILE_BYTES = 1024 * 1024 * 1024
MAX_ZIP_UNCOMPRESSED_BYTES = 3 * 1024 * 1024 * 1024
MACOS_ZIP_ROOT = "facebook-monitor"
CPU_TYPE_ARM64 = 0x0100000C
MACHO_MAGIC_64 = 0xFEEDFACF
MACHO_CIGAM_64 = 0xCFFAEDFE
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
FAT_CIGAM = 0xBEBAFECA
FAT_CIGAM_64 = 0xBFBAFECA
REQUIRED_ZIP_ENTRIES = frozenset(
    {
        "facebook-monitor/facebook-monitor.exe",
        "facebook-monitor/facebook-monitor-updater.exe",
        "facebook-monitor/_internal/python313.dll",
        "facebook-monitor/_internal/browser/chrome.exe",
        "facebook-monitor/_internal/assets/facebook-monitor.ico",
        "facebook-monitor/_internal/assets/facebook-monitor-tray.ico",
    }
)
ZIP_EXE_ENTRIES = (
    "facebook-monitor/facebook-monitor.exe",
    "facebook-monitor/facebook-monitor-updater.exe",
)
MACOS_REQUIRED_ZIP_ENTRIES = frozenset(
    f"{MACOS_ZIP_ROOT}/{path}"
    for path in MACOS_ARM64_LAYOUT_POLICY.required_staging_files
)
MACOS_EXECUTABLE_ENTRIES = (
    f"{MACOS_ZIP_ROOT}/{MACOS_APP_ENTRY}",
    f"{MACOS_ZIP_ROOT}/{MACOS_UPDATER_ENTRY}",
    f"{MACOS_ZIP_ROOT}/{MACOS_APP_BUNDLE_LAUNCHER}",
)
MACOS_APP_INFO_PLIST_ENTRY = f"{MACOS_ZIP_ROOT}/{MACOS_APP_BUNDLE_INFO_PLIST}"
MACOS_APP_LAUNCHER_ENTRY = f"{MACOS_ZIP_ROOT}/{MACOS_APP_BUNDLE_LAUNCHER}"
MACOS_APP_LAUNCHER_NAME = PurePosixPath(MACOS_APP_BUNDLE_LAUNCHER).name
MACOS_BROWSER_ENTRY_SUFFIXES = tuple(
    suffix
    for group in MACOS_ARM64_LAYOUT_POLICY.required_staging_any_groups
    for suffix in group
)
SENSITIVE_RELEASE_PATH_PARTS = frozenset(
    {
        "data",
        "profiles",
        "logs",
        "cookies",
        "tokens",
        "session",
        "sessions",
    }
)


@dataclass(frozen=True)
class ArtifactValidationResult:
    """Release artifact 驗證結果。"""

    ok: bool
    messages: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    """解析 CLI 參數。"""

    parser = argparse.ArgumentParser(
        description="Validate release zip and SHA256 artifact."
    )
    parser.add_argument(
        "--version",
        default=APP_VERSION,
        help="Expected app version. Defaults to facebook_monitor.version.APP_VERSION.",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=ROOT / "dist",
        help="Directory containing release artifacts.",
    )
    parser.add_argument(
        "--platform",
        default="windows",
        choices=("windows", "macos-arm64"),
        help="Artifact platform to validate.",
    )
    parser.add_argument(
        "--expected-signer-subject",
        default="",
        help="Optional Authenticode signer subject substring expected for EXEs.",
    )
    parser.add_argument(
        "--expected-tag",
        default="",
        help="Optional GitHub tag expected for this artifact, for example v0.2.0.",
    )
    return parser.parse_args()


def validate_release_artifacts(
    *,
    version: str,
    dist_dir: Path,
    platform_name: str = "windows",
    expected_signer_subject: str = "",
    expected_tag: str = "",
) -> ArtifactValidationResult:
    """驗證 release zip、SHA256、version metadata 與必要內容。"""

    messages: list[str] = []
    normalized_platform = _normalize_platform_name(platform_name)
    zip_name = f"facebook-monitor-{version}{_artifact_suffix(normalized_platform)}"
    zip_path = dist_dir / zip_name
    sha_path = zip_path.with_name(zip_path.name + ".sha256")

    _require_file(zip_path, messages)
    _require_file(sha_path, messages)
    _validate_expected_tag(version, expected_tag, messages)
    if normalized_platform == "windows":
        _validate_version_info_file(version, messages)
    if zip_path.is_file() and normalized_platform == "windows":
        _validate_zip_contents(zip_path, messages)
        _validate_zipped_exes(zip_path, version, expected_signer_subject, messages)
    elif zip_path.is_file():
        _validate_macos_zip_contents(zip_path, version, messages)
    if zip_path.is_file() and sha_path.is_file():
        _validate_sha256(zip_path, sha_path, messages)

    if messages:
        return ArtifactValidationResult(ok=False, messages=tuple(messages))
    return ArtifactValidationResult(
        ok=True,
        messages=(f"release artifacts valid for {version} {normalized_platform}: {zip_name}",),
    )


def _normalize_platform_name(platform_name: str) -> str:
    """整理 artifact platform 名稱。"""

    normalized = platform_name.strip().casefold()
    if normalized in {"windows", "win32"}:
        return "windows"
    if normalized in {"macos-arm64", "darwin-arm64"}:
        return "macos-arm64"
    raise ValueError(f"unsupported artifact platform: {platform_name}")


def _artifact_suffix(platform_name: str) -> str:
    """回傳 artifact platform 對應的 release 檔名 suffix。"""

    if platform_name == "macos-arm64":
        return MACOS_ARM64_ONEDIR_SUFFIX
    return WINDOWS_PORTABLE_SUFFIX


def _require_file(path: Path, messages: list[str]) -> None:
    """確認檔案存在。"""

    if not path.is_file():
        messages.append(f"missing file: {path}")


def _validate_version_info_file(version: str, messages: list[str]) -> None:
    """確認 PyInstaller version resource template 與 app version 對齊。"""

    if not VERSION_INFO_FILE.is_file():
        messages.append(f"missing version info file: {VERSION_INFO_FILE}")
        return
    text = VERSION_INFO_FILE.read_text(encoding="utf-8")
    expected_file_version = _windows_file_version(version)
    expected_version_tuple = _windows_version_tuple(version)
    expected_tuple_text = ", ".join(str(part) for part in expected_version_tuple)
    if f"StringStruct('ProductVersion', '{version}')" not in text:
        messages.append("version_info ProductVersion does not match app version")
    if f"StringStruct('FileVersion', '{expected_file_version}')" not in text:
        messages.append("version_info FileVersion does not match app version")
    if f"filevers=({expected_tuple_text})" not in text:
        messages.append("version_info filevers does not match app version")
    if f"prodvers=({expected_tuple_text})" not in text:
        messages.append("version_info prodvers does not match app version")


def _validate_expected_tag(
    version: str,
    expected_tag: str,
    messages: list[str],
) -> None:
    """若呼叫端提供 tag，確認 tag 與 app version 對齊。"""

    if expected_tag and expected_tag != f"v{version}":
        messages.append(f"expected tag mismatch: {expected_tag} != v{version}")


def _validate_zip_contents(zip_path: Path, messages: list[str]) -> None:
    """確認 portable zip 內含必要 onedir 檔案且不含 data dir。"""

    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = _validated_zip_names(archive, messages)
    except zipfile.BadZipFile:
        messages.append(f"bad zip file: {zip_path}")
        return
    missing = sorted(REQUIRED_ZIP_ENTRIES - names)
    for name in missing:
        messages.append(f"zip missing required entry: {name}")
    if any(name.startswith("facebook-monitor/data/") for name in names):
        messages.append("zip must not include portable data directory")


def _validate_macos_zip_contents(
    zip_path: Path,
    version: str,
    messages: list[str],
) -> None:
    """確認 macOS onedir zip 內含必要檔案且不含 runtime data。"""

    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = _validated_zip_names(archive, messages)
            infos = {info.filename.replace("\\", "/"): info for info in archive.infolist()}
            _validate_macos_app_bundle_metadata(archive, names, version, messages)
    except zipfile.BadZipFile:
        messages.append(f"bad zip file: {zip_path}")
        return
    missing = sorted(MACOS_REQUIRED_ZIP_ENTRIES - names)
    for name in missing:
        messages.append(f"zip missing required entry: {name}")
    for entry in MACOS_EXECUTABLE_ENTRIES:
        info = infos.get(entry)
        if info is not None and not _zip_member_has_executable_bit(info):
            messages.append(f"zip executable bit missing: {entry}")
    browser_entries = [
        name
        for name in names
        if any(name.endswith(suffix) for suffix in MACOS_BROWSER_ENTRY_SUFFIXES)
    ]
    if not browser_entries:
        messages.append("zip missing required macOS Chromium executable")
    for entry in browser_entries:
        info = infos.get(entry)
        if info is not None and not _zip_member_has_executable_bit(info):
            messages.append(f"zip executable bit missing: {entry}")
    _validate_no_sensitive_runtime_paths(names, messages)


def _validate_macos_app_bundle_metadata(
    archive: zipfile.ZipFile,
    names: set[str],
    version: str,
    messages: list[str],
) -> None:
    """確認 macOS launcher `.app` 會顯示在 Dock 並保持 native app 生命周期。"""

    if MACOS_APP_INFO_PLIST_ENTRY not in names:
        return
    try:
        plist = plistlib.loads(archive.read(MACOS_APP_INFO_PLIST_ENTRY))
    except (OSError, plistlib.InvalidFileException, KeyError):
        messages.append("macOS app bundle Info.plist is invalid")
        return
    if plist.get("CFBundlePackageType") != "APPL":
        messages.append("macOS app bundle CFBundlePackageType must be APPL")
    if plist.get("CFBundleExecutable") != MACOS_APP_LAUNCHER_NAME:
        messages.append("macOS app bundle executable does not match launcher")
    if plist.get("LSUIElement") is True or plist.get("LSBackgroundOnly") is True:
        messages.append("macOS app bundle must remain visible in Dock")
    if not plist.get("CFBundleIconFile"):
        messages.append("macOS app bundle icon is missing")
    if plist.get("CFBundleShortVersionString") != version:
        messages.append("macOS app bundle short version does not match app version")
    if plist.get("CFBundleVersion") != version:
        messages.append("macOS app bundle version does not match app version")
    try:
        launcher_data = archive.read(MACOS_APP_LAUNCHER_ENTRY)
    except (OSError, KeyError):
        return
    if not _is_macho_arm64(launcher_data):
        messages.append("macOS app bundle launcher must be an arm64 Mach-O executable")


def _is_macho_arm64(data: bytes) -> bool:
    """判斷 bytes 是否為 arm64 Mach-O 或包含 arm64 slice 的 universal binary。"""

    if len(data) < 8:
        return False
    little_magic = struct.unpack_from("<I", data, 0)[0]
    big_magic = struct.unpack_from(">I", data, 0)[0]
    if little_magic == MACHO_MAGIC_64:
        return struct.unpack_from("<i", data, 4)[0] == CPU_TYPE_ARM64
    if big_magic in {MACHO_MAGIC_64, MACHO_CIGAM_64}:
        return struct.unpack_from(">i", data, 4)[0] == CPU_TYPE_ARM64
    if big_magic in {FAT_MAGIC, FAT_MAGIC_64}:
        return _fat_binary_contains_arm64(data, endian=">")
    if little_magic in {FAT_CIGAM, FAT_CIGAM_64}:
        return _fat_binary_contains_arm64(data, endian="<")
    return False


def _fat_binary_contains_arm64(data: bytes, *, endian: str) -> bool:
    """檢查 universal binary 的 fat_arch / fat_arch_64 table 是否包含 arm64。"""

    if len(data) < 8:
        return False
    magic = struct.unpack_from(f"{endian}I", data, 0)[0]
    arch_size = 32 if magic in {FAT_MAGIC_64, FAT_CIGAM_64} else 20
    arch_count = struct.unpack_from(f"{endian}I", data, 4)[0]
    if arch_count > 64:
        return False
    offset = 8
    for _ in range(arch_count):
        if len(data) < offset + arch_size:
            return False
        cpu_type = struct.unpack_from(f"{endian}i", data, offset)[0]
        if cpu_type == CPU_TYPE_ARM64:
            return True
        offset += arch_size
    return False


def _zip_member_has_executable_bit(info: zipfile.ZipInfo) -> bool:
    """檢查 zip member 是否保留 POSIX executable bit。"""

    mode = (info.external_attr >> 16) & 0o777
    return bool(mode & 0o111)


def _validate_no_sensitive_runtime_paths(names: set[str], messages: list[str]) -> None:
    """避免 release artifact 夾帶 runtime data、profile 或 session 類資料。"""

    for name in names:
        path = PurePosixPath(name)
        lower_parts = {part.casefold() for part in path.parts}
        if SENSITIVE_RELEASE_PATH_PARTS & lower_parts:
            messages.append(f"zip must not include runtime/private data: {name}")


def _validate_zipped_exes(
    zip_path: Path,
    version: str,
    expected_signer_subject: str,
    messages: list[str],
) -> None:
    """解出 zip 內 EXE 後驗證 metadata，避免 loose dist 目錄掩蓋 stale zip。"""

    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = _validated_zip_names(archive, messages)
            missing = [entry for entry in ZIP_EXE_ENTRIES if entry not in names]
            if missing:
                return
            with tempfile.TemporaryDirectory(prefix="facebook-monitor-artifact-") as temp:
                temp_dir = Path(temp)
                for entry in ZIP_EXE_ENTRIES:
                    exe_path = temp_dir / Path(entry).name
                    exe_path.write_bytes(archive.read(entry))
                    _validate_exe_version(exe_path, version, messages)
                    _validate_authenticode(
                        exe_path,
                        expected_signer_subject,
                        messages,
                    )
    except zipfile.BadZipFile:
        return
    except OSError as exc:
        messages.append(f"cannot validate zipped EXEs for {zip_path}: {exc}")


def _validated_zip_names(
    archive: zipfile.ZipFile,
    messages: list[str],
) -> set[str]:
    """檢查 zip member 安全性並回傳 normalized names。"""

    members = archive.infolist()
    names: set[str] = set()
    total_uncompressed = 0
    if len(members) > MAX_ZIP_ENTRIES:
        messages.append("zip too many entries")
    for member in members:
        normalized = member.filename.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or os.path.isabs(normalized):
            messages.append(f"zip member path unsafe: {member.filename}")
            continue
        if normalized in names:
            messages.append(f"zip duplicate entry: {normalized}")
            continue
        names.add(normalized)
        if member.is_dir():
            continue
        if member.file_size > MAX_ZIP_SINGLE_FILE_BYTES:
            messages.append(f"zip member too large: {normalized}")
        total_uncompressed += member.file_size
        if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
            messages.append("zip uncompressed size too large")
            break
    return names


def _validate_sha256(zip_path: Path, sha_path: Path, messages: list[str]) -> None:
    """確認 `.sha256` 內容與 zip hash / 檔名一致。"""

    digest = hashlib.sha256()
    with zip_path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    expected_line = f"{actual}  {zip_path.name}"
    content = sha_path.read_text(encoding="ascii").strip()
    if content != expected_line:
        messages.append(
            f"sha256 file mismatch: expected `{expected_line}`, got `{content}`"
        )


def _validate_exe_version(exe_path: Path, version: str, messages: list[str]) -> None:
    """確認 EXE version resource 與 app version 對齊。"""

    try:
        file_version, product_version = _read_windows_version_info(exe_path)
    except (OSError, subprocess.CalledProcessError) as exc:
        messages.append(f"cannot read EXE version for {exe_path}: {exc}")
        return
    expected_file_version = _windows_file_version(version)
    if file_version != expected_file_version:
        messages.append(
            f"{exe_path.name} FileVersion mismatch: {file_version} != {expected_file_version}"
        )
    if product_version != version:
        messages.append(
            f"{exe_path.name} ProductVersion mismatch: {product_version} != {version}"
        )


def _validate_authenticode(
    exe_path: Path,
    expected_signer_subject: str,
    messages: list[str],
) -> None:
    """若指定 signer，確認 Authenticode 簽章有效且 subject 符合。"""

    if not expected_signer_subject:
        return
    try:
        status, subject = _read_authenticode_signature(exe_path)
    except (OSError, subprocess.CalledProcessError) as exc:
        messages.append(f"cannot read Authenticode signature for {exe_path}: {exc}")
        return
    if status != "Valid":
        messages.append(f"{exe_path.name} Authenticode status is {status}, expected Valid")
    if expected_signer_subject not in subject:
        messages.append(
            f"{exe_path.name} signer subject mismatch: `{subject}` does not contain "
            f"`{expected_signer_subject}`"
        )


def _read_windows_version_info(exe_path: Path) -> tuple[str, str]:
    """透過 PowerShell 讀取 Windows EXE version resource。"""

    command = (
        f"$v=(Get-Item -LiteralPath {_powershell_literal(exe_path)}).VersionInfo; "
        "[Console]::WriteLine($v.FileVersion); "
        "[Console]::WriteLine($v.ProductVersion)"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
    )
    lines = completed.stdout.splitlines()
    if len(lines) < 2:
        raise OSError("missing version info output")
    return lines[0].strip(), lines[1].strip()


def _read_authenticode_signature(exe_path: Path) -> tuple[str, str]:
    """透過 PowerShell 讀取 Authenticode 簽章狀態與 signer subject。"""

    command = (
        f"$s=Get-AuthenticodeSignature -LiteralPath {_powershell_literal(exe_path)}; "
        "[Console]::WriteLine($s.Status); "
        "[Console]::WriteLine($s.SignerCertificate.Subject)"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
    )
    lines = completed.stdout.splitlines()
    if len(lines) < 2:
        raise OSError("missing signature output")
    return lines[0].strip(), lines[1].strip()


def _powershell_literal(path: Path) -> str:
    """回傳 PowerShell single-quoted literal path。"""

    return "'" + str(path).replace("'", "''") + "'"


def _windows_file_version(version: str) -> str:
    """將 semver/rc 轉成 Windows FileVersion 字串。"""

    major, minor, patch, build = _windows_version_tuple(version)
    return f"{major}.{minor}.{patch}.{build}"


def _windows_version_tuple(version: str) -> tuple[int, int, int, int]:
    """將 semver/rc 轉成 Windows FixedFileInfo tuple。"""

    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?", version)
    if match is None:
        raise ValueError(f"unsupported release version: {version}")
    build = match.group(4) or "0"
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        int(build),
    )


def main() -> int:
    """CLI entrypoint。"""

    args = parse_args()
    result = validate_release_artifacts(
        version=str(args.version),
        dist_dir=args.dist_dir.resolve(),
        platform_name=str(args.platform),
        expected_signer_subject=str(args.expected_signer_subject),
        expected_tag=str(args.expected_tag),
    )
    for message in result.messages:
        print(message)
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
