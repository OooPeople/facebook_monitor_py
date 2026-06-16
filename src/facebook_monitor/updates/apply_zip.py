"""Updater zip extraction and archive metadata validation helpers."""

from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath
import os
import shutil
import zipfile

from facebook_monitor.updates.platforms import UpdaterLayoutPolicy
from facebook_monitor.updates.platforms import macos_app_executable_staging_paths
from facebook_monitor.updates.validation import SENSITIVE_RELEASE_PATH_PARTS
from facebook_monitor.updates.validation import decode_zip_symlink_target
from facebook_monitor.updates.validation import has_unsafe_existing_path_component
from facebook_monitor.updates.validation import normalized_zip_member_key
from facebook_monitor.updates.validation import resolve_zip_symlink_target
from facebook_monitor.updates.validation import validate_zip_member_path
from facebook_monitor.updates.validation import zip_member_has_executable_bit
from facebook_monitor.updates.validation import zip_member_is_symlink
from facebook_monitor.updates.zip_policy import MAX_ZIP_ENTRIES
from facebook_monitor.updates.zip_policy import MAX_ZIP_SINGLE_FILE_BYTES
from facebook_monitor.updates.zip_policy import MAX_ZIP_SYMLINK_TARGET_BYTES
from facebook_monitor.updates.zip_policy import MAX_ZIP_UNCOMPRESSED_BYTES


def safe_extract_zip(
    zip_path: Path,
    destination: Path,
    *,
    max_entries: int = MAX_ZIP_ENTRIES,
    max_single_file_bytes: int = MAX_ZIP_SINGLE_FILE_BYTES,
    max_uncompressed_bytes: int = MAX_ZIP_UNCOMPRESSED_BYTES,
) -> None:
    """安全解壓 zip，拒絕 path traversal、絕對路徑與過大 archive。"""

    if has_unsafe_existing_path_component(destination, root=destination.parent):
        raise ValueError("zip_destination_unsafe")
    destination = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > max_entries:
            raise ValueError("zip_too_many_entries")
        total_uncompressed = 0
        member_paths: dict[zipfile.ZipInfo, PurePosixPath] = {}
        normalized_paths: set[str] = set()
        symlink_member_paths: set[PurePosixPath] = set()
        for member in members:
            member_path = _zip_member_relative_path(member)
            member_key = normalized_zip_member_key(member_path)
            if member_key in normalized_paths:
                raise ValueError("zip_duplicate_member_path")
            normalized_paths.add(member_key)
            member_paths[member] = member_path
            if zip_member_is_symlink(member):
                symlink_member_paths.add(member_path)
                if member.file_size > MAX_ZIP_SYMLINK_TARGET_BYTES:
                    raise ValueError("zip_symlink_target_too_large")
                _validate_zip_symlink_target(
                    member_path,
                    archive.read(member),
                )
                continue
            if member.is_dir():
                continue
            if member.file_size > max_single_file_bytes:
                raise ValueError("zip_member_too_large")
            total_uncompressed += member.file_size
            if total_uncompressed > max_uncompressed_bytes:
                raise ValueError("zip_uncompressed_too_large")
        for member_path in member_paths.values():
            if any(parent in symlink_member_paths for parent in member_path.parents):
                raise ValueError("zip_member_path_unsafe")
        for member in members:
            _extract_zip_member(archive, member, destination, member_paths[member])


def validate_macos_zip_executable_bits(
    zip_path: Path,
    *,
    layout_policy: UpdaterLayoutPolicy,
) -> None:
    """檢查 macOS update zip metadata 是否保留可執行檔 POSIX executable bit。"""

    if layout_policy.platform_key != "macos-arm64":
        return
    with zipfile.ZipFile(zip_path) as archive:
        member_infos = _zip_member_infos_by_path(archive.infolist())
        app_root_path = _find_zip_app_root_path(
            member_infos,
            layout_policy=layout_policy,
        )
        browser_paths = _select_zip_any_group_files(
            member_infos,
            app_root_path=app_root_path,
            any_groups=layout_policy.required_staging_any_groups,
        )
        executable_paths = (
            *macos_app_executable_staging_paths(layout_policy),
            *browser_paths,
        )
        for relative_path in executable_paths:
            member_path = _join_zip_member_path(app_root_path, relative_path)
            info = member_infos.get(member_path)
            if info is None:
                continue
            if not zip_member_has_executable_bit(info):
                raise ValueError(f"staging_executable_bit_missing:{member_path}")


def _zip_member_infos_by_path(
    members: list[zipfile.ZipInfo],
) -> dict[PurePosixPath, zipfile.ZipInfo]:
    """以正規化後的 POSIX path 索引 zip member。"""

    return {_zip_member_relative_path(member): member for member in members if not member.is_dir()}


def _find_zip_app_root_path(
    member_infos: dict[PurePosixPath, zipfile.ZipInfo],
    *,
    layout_policy: UpdaterLayoutPolicy,
) -> PurePosixPath:
    """在 zip member path 中找出 app root prefix，對齊 staging root 搜尋語義。"""

    candidates = sorted(
        (
            member_path.parent
            for member_path in member_infos
            if member_path.name == layout_policy.app_entry_name
        ),
        key=lambda path: len(path.parts),
    )
    for candidate in candidates:
        if all(
            _zip_member_file_exists(member_infos, candidate, relative_path)
            for relative_path in layout_policy.required_staging_files
        ):
            return candidate
    return candidates[0] if candidates else PurePosixPath()


def _select_zip_any_group_files(
    member_infos: dict[PurePosixPath, zipfile.ZipInfo],
    *,
    app_root_path: PurePosixPath,
    any_groups: tuple[tuple[str, ...], ...],
) -> tuple[str, ...]:
    """依 zip metadata 選出 any group 命中的檔案，供 executable bit 驗證。"""

    selected_paths: list[str] = []
    for group in any_groups:
        for relative_path in group:
            if _zip_member_file_exists(member_infos, app_root_path, relative_path):
                selected_paths.append(relative_path)
                break
    return tuple(selected_paths)


def _zip_member_file_exists(
    member_infos: dict[PurePosixPath, zipfile.ZipInfo],
    app_root_path: PurePosixPath,
    relative_path: str,
) -> bool:
    """判斷 app root 下的相對檔案是否存在於 zip。"""

    return _join_zip_member_path(app_root_path, relative_path) in member_infos


def _join_zip_member_path(
    app_root_path: PurePosixPath,
    relative_path: str,
) -> PurePosixPath:
    """將 app root prefix 與 app 內相對路徑組成 zip member path。"""

    relative = PurePosixPath(relative_path)
    if app_root_path == PurePosixPath():
        return relative
    return app_root_path / relative


def _extract_zip_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    destination: Path,
    member_path: PurePosixPath,
) -> None:
    """解出單一 zip member，並保留 POSIX executable bit。"""

    target = _zip_member_target(destination, member_path)
    if has_unsafe_existing_path_component(target.parent, root=destination):
        raise ValueError("zip_member_path_unsafe")
    if zip_member_is_symlink(member):
        if os.name == "nt":
            raise ValueError("zip_symlink_unsupported")
        if target.exists() or target.is_symlink():
            raise ValueError("zip_duplicate_member_path")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(_decode_zip_symlink_target(archive.read(member)))
        return
    if member.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        _apply_zip_member_mode(target, member)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise ValueError("zip_duplicate_member_path")
    with archive.open(member) as source, target.open("xb") as output:
        shutil.copyfileobj(source, output)
    _apply_zip_member_mode(target, member)


def _zip_member_relative_path(member: zipfile.ZipInfo) -> PurePosixPath:
    """正規化 zip member path 並拒絕絕對路徑或 traversal。"""

    return validate_zip_member_path(member.filename)


def _zip_member_target(destination: Path, member_path: PurePosixPath) -> Path:
    """將 zip 內 POSIX path 轉成 destination 內的實際 path。"""

    target = destination.joinpath(*member_path.parts)
    if not target.resolve(strict=False).is_relative_to(destination):
        raise ValueError("zip_member_path_unsafe")
    return target


def _validate_zip_symlink_target(
    member_path: PurePosixPath,
    target_data: bytes,
) -> None:
    """確認 zip symlink target 不會逃出 staging root。"""

    target_text = decode_zip_symlink_target(target_data)
    resolved = resolve_zip_symlink_target(member_path, target_text)
    if resolved is None:
        raise ValueError("zip_symlink_target_unsafe")
    lower_parts = {part.casefold() for part in resolved.parts}
    if SENSITIVE_RELEASE_PATH_PARTS & lower_parts:
        raise ValueError("zip_symlink_target_unsafe")


def _decode_zip_symlink_target(target_data: bytes) -> str:
    """讀取 zip symlink target；PyInstaller 產物應使用文字相對路徑。"""

    return decode_zip_symlink_target(target_data)


def _apply_zip_member_mode(target: Path, member: zipfile.ZipInfo) -> None:
    """套用 zip member 內保存的 POSIX permission bits。"""

    mode = (member.external_attr >> 16) & 0o777
    if mode:
        target.chmod(mode)
