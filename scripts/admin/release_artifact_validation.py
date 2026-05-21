"""Admin tool：驗證 release artifact 一致性。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import os
import plistlib
from pathlib import PurePosixPath
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.updates.artifacts import RELEASE_ARCHIVE_ROOT_NAME
from facebook_monitor.updates.artifacts import UPDATE_ARTIFACT_POLICIES
from facebook_monitor.updates.artifacts import release_sha256_asset_name
from facebook_monitor.updates.artifacts import update_artifact_policy_for_key
from facebook_monitor.updates.platforms import WINDOWS_APP_ENTRY
from facebook_monitor.updates.platforms import WINDOWS_LAYOUT_POLICY
from facebook_monitor.updates.platforms import WINDOWS_UPDATER_ENTRY
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_INFO_PLIST
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER
from facebook_monitor.updates.platforms import MACOS_ARM64_LAYOUT_POLICY
from facebook_monitor.updates.platforms import macos_app_executable_staging_paths
from facebook_monitor.updates.platforms import macos_optional_executable_staging_paths
from facebook_monitor.updates.checksum import calculate_sha256
from facebook_monitor.updates.checksum import render_sha256_sidecar
from facebook_monitor.updates.validation import SENSITIVE_RELEASE_PATH_PARTS
from facebook_monitor.updates.validation import decode_zip_symlink_target
from facebook_monitor.updates.validation import is_macho_arm64
from facebook_monitor.updates.validation import plist_value_is_true
from facebook_monitor.updates.validation import resolve_zip_symlink_target
from facebook_monitor.updates.validation import zip_member_has_executable_bit
from facebook_monitor.updates.validation import zip_member_is_symlink
from facebook_monitor.updates.zip_policy import MAX_ZIP_ENTRIES
from facebook_monitor.updates.zip_policy import MAX_ZIP_SINGLE_FILE_BYTES
from facebook_monitor.updates.zip_policy import MAX_ZIP_SYMLINK_TARGET_BYTES
from facebook_monitor.updates.zip_policy import MAX_ZIP_UNCOMPRESSED_BYTES
from facebook_monitor.version import APP_VERSION
from scripts.admin.windows_version_resource import windows_file_version
from scripts.admin.windows_version_resource import windows_version_tuple


WINDOWS_APP_VERSION_INFO_FILE = (
    ROOT / "build" / "pyinstaller_generated" / "windows_app_version_info.txt"
)
WINDOWS_UPDATER_VERSION_INFO_FILE = (
    ROOT / "build" / "pyinstaller_generated" / "windows_updater_version_info.txt"
)
ARTIFACT_PLATFORM_CHOICES = tuple(
    policy.platform_key for policy in UPDATE_ARTIFACT_POLICIES
)
WINDOWS_ZIP_ROOT = RELEASE_ARCHIVE_ROOT_NAME
MACOS_ZIP_ROOT = RELEASE_ARCHIVE_ROOT_NAME
MACHO_PROBE_BYTES = 4096
WINDOWS_REQUIRED_ZIP_ENTRIES = frozenset(
    f"{WINDOWS_ZIP_ROOT}/{path}"
    for path in WINDOWS_LAYOUT_POLICY.required_staging_files
)
WINDOWS_ZIP_EXE_ENTRIES = (
    f"{WINDOWS_ZIP_ROOT}/{WINDOWS_APP_ENTRY}",
    f"{WINDOWS_ZIP_ROOT}/{WINDOWS_UPDATER_ENTRY}",
)
MACOS_REQUIRED_ZIP_ENTRIES = frozenset(
    f"{MACOS_ZIP_ROOT}/{path}"
    for path in MACOS_ARM64_LAYOUT_POLICY.required_staging_files
)
MACOS_EXECUTABLE_ENTRIES = (
    *(
        f"{MACOS_ZIP_ROOT}/{path}"
        for path in macos_app_executable_staging_paths(MACOS_ARM64_LAYOUT_POLICY)
    ),
)
MACOS_APP_INFO_PLIST_ENTRY = f"{MACOS_ZIP_ROOT}/{MACOS_APP_BUNDLE_INFO_PLIST}"
MACOS_APP_LAUNCHER_NAME = PurePosixPath(MACOS_APP_BUNDLE_LAUNCHER).name
MACOS_BROWSER_ENTRY_SUFFIXES = macos_optional_executable_staging_paths(
    MACOS_ARM64_LAYOUT_POLICY
)
MACOS_BROWSER_ENTRIES = tuple(
    f"{MACOS_ZIP_ROOT}/{suffix}" for suffix in MACOS_BROWSER_ENTRY_SUFFIXES
)


@dataclass(frozen=True)
class ArtifactValidationResult:
    """Release artifact 驗證結果。"""

    ok: bool
    messages: tuple[str, ...]


@dataclass(frozen=True)
class WindowsVersionResourceFile:
    """描述 PyInstaller 產生的單一 Windows version resource 檔。"""

    path: Path
    internal_name: str
    original_filename: str


WINDOWS_VERSION_RESOURCE_FILES = (
    WindowsVersionResourceFile(
        path=WINDOWS_APP_VERSION_INFO_FILE,
        internal_name=Path(WINDOWS_APP_ENTRY).stem,
        original_filename=WINDOWS_APP_ENTRY,
    ),
    WindowsVersionResourceFile(
        path=WINDOWS_UPDATER_VERSION_INFO_FILE,
        internal_name=Path(WINDOWS_UPDATER_ENTRY).stem,
        original_filename=WINDOWS_UPDATER_ENTRY,
    ),
)


@dataclass(frozen=True)
class WindowsExeVersionInfo:
    """保存 Windows EXE version resource 主要欄位。"""

    file_version: str
    product_version: str
    original_filename: str = ""


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
        choices=ARTIFACT_PLATFORM_CHOICES,
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
    artifact_policy = update_artifact_policy_for_key(platform_name)
    normalized_platform = artifact_policy.platform_key
    zip_name = artifact_policy.asset_name(version)
    zip_path = dist_dir / zip_name
    sha_path = zip_path.with_name(release_sha256_asset_name(zip_path.name))

    _require_file(zip_path, messages)
    _require_file(sha_path, messages)
    _validate_expected_tag(version, expected_tag, messages)
    if normalized_platform == "windows":
        _validate_windows_version_resources(version, messages)
    if zip_path.is_file() and normalized_platform == "windows":
        _validate_windows_zip_contents(zip_path, messages)
        _validate_zipped_windows_exes(
            zip_path,
            version,
            expected_signer_subject,
            messages,
        )
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


def _require_file(path: Path, messages: list[str]) -> None:
    """確認檔案存在。"""

    if not path.is_file():
        messages.append(f"missing file: {path}")


def _validate_windows_version_resources(version: str, messages: list[str]) -> None:
    """確認 Windows PyInstaller version resources 會由 app version 產生。"""

    for resource in WINDOWS_VERSION_RESOURCE_FILES:
        if not resource.path.is_file():
            messages.append(f"missing Windows version resource: {resource.path}")
            continue
        _validate_windows_version_resource_text(
            resource.path.read_text(encoding="utf-8"),
            version,
            resource=resource,
            messages=messages,
        )


def _validate_windows_version_resource_text(
    text: str,
    version: str,
    *,
    resource: WindowsVersionResourceFile,
    messages: list[str],
) -> None:
    """確認單一 Windows version resource 的版本與 EXE identity。"""

    expected_file_version = windows_file_version(version)
    expected_version_tuple = windows_version_tuple(version)
    expected_tuple_text = ", ".join(str(part) for part in expected_version_tuple)
    if f"StringStruct('ProductVersion', '{version}')" not in text:
        messages.append(
            "windows version resource ProductVersion does not match app version"
        )
    if f"StringStruct('FileVersion', '{expected_file_version}')" not in text:
        messages.append(
            "windows version resource FileVersion does not match app version"
        )
    if f"filevers=({expected_tuple_text})" not in text:
        messages.append("windows version resource filevers does not match app version")
    if f"prodvers=({expected_tuple_text})" not in text:
        messages.append("windows version resource prodvers does not match app version")
    if f"StringStruct('InternalName', '{resource.internal_name}')" not in text:
        messages.append(
            f"windows version resource InternalName does not match "
            f"{resource.original_filename}"
        )
    if f"StringStruct('OriginalFilename', '{resource.original_filename}')" not in text:
        messages.append(
            f"windows version resource OriginalFilename does not match "
            f"{resource.original_filename}"
        )


def _validate_expected_tag(
    version: str,
    expected_tag: str,
    messages: list[str],
) -> None:
    """若呼叫端提供 tag，確認 tag 與 app version 對齊。"""

    if expected_tag and expected_tag != f"v{version}":
        messages.append(f"expected tag mismatch: {expected_tag} != v{version}")


def _validate_windows_zip_contents(zip_path: Path, messages: list[str]) -> None:
    """確認 Windows portable zip 內含必要 onedir 檔案且不含 data dir。"""

    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = _validated_zip_names(archive, messages)
    except zipfile.BadZipFile:
        messages.append(f"bad zip file: {zip_path}")
        return
    missing = sorted(WINDOWS_REQUIRED_ZIP_ENTRIES - names)
    for name in missing:
        messages.append(f"zip missing required entry: {name}")
    if any(name.startswith("facebook-monitor/data/") for name in names):
        messages.append("zip must not include portable data directory")
    _validate_no_sensitive_runtime_paths(names, messages)


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
            missing = sorted(MACOS_REQUIRED_ZIP_ENTRIES - names)
            for name in missing:
                messages.append(f"zip missing required entry: {name}")
            for entry in MACOS_EXECUTABLE_ENTRIES:
                info = infos.get(entry)
                if info is not None and not zip_member_has_executable_bit(info):
                    messages.append(f"zip executable bit missing: {entry}")
                if info is not None and not _zip_member_is_macho_arm64(archive, entry):
                    messages.append(f"zip executable must be arm64 Mach-O: {entry}")
            browser_entries = [name for name in MACOS_BROWSER_ENTRIES if name in names]
            if not browser_entries:
                messages.append("zip missing required macOS Chromium executable")
            for entry in browser_entries:
                info = infos.get(entry)
                if info is not None and not zip_member_has_executable_bit(info):
                    messages.append(f"zip executable bit missing: {entry}")
                if info is not None and not _zip_member_is_macho_arm64(archive, entry):
                    messages.append(f"zip executable must be arm64 Mach-O: {entry}")
            _validate_no_sensitive_runtime_paths(names, messages)
    except zipfile.BadZipFile:
        messages.append(f"bad zip file: {zip_path}")


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
    if plist_value_is_true(plist.get("LSUIElement")) or plist_value_is_true(
        plist.get("LSBackgroundOnly")
    ):
        messages.append("macOS app bundle must remain visible in Dock")
    if not plist.get("CFBundleIconFile"):
        messages.append("macOS app bundle icon is missing")
    if plist.get("CFBundleShortVersionString") != version:
        messages.append("macOS app bundle short version does not match app version")
    if plist.get("CFBundleVersion") != version:
        messages.append("macOS app bundle version does not match app version")

def _zip_member_is_macho_arm64(archive: zipfile.ZipFile, name: str) -> bool:
    """讀取 zip member 前段並確認是 arm64 Mach-O。"""

    try:
        with archive.open(name) as file:
            return is_macho_arm64(file.read(MACHO_PROBE_BYTES))
    except (OSError, KeyError, zipfile.BadZipFile):
        return False


def _validate_no_sensitive_runtime_paths(names: set[str], messages: list[str]) -> None:
    """避免 release artifact 夾帶 runtime data、profile 或 session 類資料。"""

    for name in names:
        path = PurePosixPath(name)
        lower_parts = {part.casefold() for part in path.parts}
        if SENSITIVE_RELEASE_PATH_PARTS & lower_parts:
            messages.append(f"zip must not include runtime/private data: {name}")


def _validate_zipped_windows_exes(
    zip_path: Path,
    version: str,
    expected_signer_subject: str,
    messages: list[str],
) -> None:
    """解出 Windows zip 內 EXE 後驗證 metadata，避免 loose dist 掩蓋 stale zip。"""

    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = _validated_zip_names(archive, messages)
            missing = [entry for entry in WINDOWS_ZIP_EXE_ENTRIES if entry not in names]
            if missing:
                return
            with tempfile.TemporaryDirectory(prefix="facebook-monitor-artifact-") as temp:
                temp_dir = Path(temp)
                for entry in WINDOWS_ZIP_EXE_ENTRIES:
                    exe_path = temp_dir / Path(entry).name
                    exe_path.write_bytes(archive.read(entry))
                    _validate_exe_version(
                        exe_path,
                        version,
                        expected_original_filename=Path(entry).name,
                        messages=messages,
                    )
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
    paths: set[PurePosixPath] = set()
    symlink_paths: set[PurePosixPath] = set()
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
        paths.add(path)
        if zip_member_is_symlink(member):
            symlink_paths.add(path)
            if member.file_size > MAX_ZIP_SYMLINK_TARGET_BYTES:
                messages.append(f"zip symlink target too large: {normalized}")
                continue
            total_uncompressed += member.file_size
            if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                messages.append("zip uncompressed size too large")
                break
            _validate_zip_symlink_member(archive, member, path, messages)
            continue
        if member.is_dir():
            continue
        if member.file_size > MAX_ZIP_SINGLE_FILE_BYTES:
            messages.append(f"zip member too large: {normalized}")
        total_uncompressed += member.file_size
        if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
            messages.append("zip uncompressed size too large")
            break
    for path in paths:
        if any(parent in symlink_paths for parent in path.parents):
            messages.append(f"zip member path unsafe: {path.as_posix()}")
    return names


def _validate_zip_symlink_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    path: PurePosixPath,
    messages: list[str],
) -> None:
    """檢查 zip symlink 目標不會逃出 artifact root 或指向私人資料。"""

    try:
        raw_target = archive.read(member)
    except (OSError, KeyError, zipfile.BadZipFile):
        messages.append(f"zip symlink target unreadable: {member.filename}")
        return
    try:
        target_text = decode_zip_symlink_target(raw_target)
    except ValueError:
        messages.append(f"zip symlink target invalid: {member.filename}")
        return
    resolved = resolve_zip_symlink_target(path, target_text)
    if resolved is None:
        messages.append(f"zip symlink target unsafe: {member.filename}")
        return
    lower_parts = {part.casefold() for part in resolved.parts}
    if SENSITIVE_RELEASE_PATH_PARTS & lower_parts:
        messages.append(f"zip symlink target unsafe: {member.filename}")


def _validate_sha256(zip_path: Path, sha_path: Path, messages: list[str]) -> None:
    """確認 `.sha256` 內容與 zip hash / 檔名一致。"""

    actual = calculate_sha256(zip_path)
    expected_line = render_sha256_sidecar(actual, zip_path.name).strip()
    content = sha_path.read_text(encoding="ascii").strip()
    if content != expected_line:
        messages.append(
            f"sha256 file mismatch: expected `{expected_line}`, got `{content}`"
        )


def _validate_exe_version(
    exe_path: Path,
    version: str,
    *,
    expected_original_filename: str,
    messages: list[str],
) -> None:
    """確認 EXE version resource 與 app version 對齊。"""

    try:
        info = _normalize_windows_version_info(
            _read_windows_version_info(exe_path),
            fallback_original_filename=expected_original_filename,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        messages.append(f"cannot read EXE version for {exe_path}: {exc}")
        return
    expected_file_version = windows_file_version(version)
    if info.file_version != expected_file_version:
        messages.append(
            f"{exe_path.name} FileVersion mismatch: {info.file_version} != {expected_file_version}"
        )
    if info.product_version != version:
        messages.append(
            f"{exe_path.name} ProductVersion mismatch: {info.product_version} != {version}"
        )
    if info.original_filename != expected_original_filename:
        messages.append(
            f"{exe_path.name} OriginalFilename mismatch: "
            f"{info.original_filename} != {expected_original_filename}"
        )


def _normalize_windows_version_info(
    value: object,
    *,
    fallback_original_filename: str = "",
) -> WindowsExeVersionInfo:
    """整理測試替身與 PowerShell 實作回傳的 Windows version info。"""

    if isinstance(value, WindowsExeVersionInfo):
        return value
    if isinstance(value, tuple) and len(value) >= 2:
        return WindowsExeVersionInfo(
            file_version=str(value[0]),
            product_version=str(value[1]),
            original_filename=(
                str(value[2]) if len(value) >= 3 else fallback_original_filename
            ),
        )
    raise OSError("missing version info output")


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


def _read_windows_version_info(exe_path: Path) -> WindowsExeVersionInfo:
    """透過 PowerShell 讀取 Windows EXE version resource。"""

    command = (
        f"$v=(Get-Item -LiteralPath {_powershell_literal(exe_path)}).VersionInfo; "
        "[Console]::WriteLine($v.FileVersion); "
        "[Console]::WriteLine($v.ProductVersion); "
        "[Console]::WriteLine($v.OriginalFilename)"
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
    original_filename = lines[2].strip() if len(lines) >= 3 else ""
    return WindowsExeVersionInfo(
        file_version=lines[0].strip(),
        product_version=lines[1].strip(),
        original_filename=original_filename,
    )


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
