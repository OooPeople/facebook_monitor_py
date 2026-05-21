"""Updater / release validation helpers.

職責：集中放置 updater 與 release gate 共用的安全判斷，避免 macOS
executable bit、Mach-O 與危險路徑規則散落在 runtime、scripts 與 tests。
"""

from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath
import os
import stat
import struct
import zipfile


CPU_TYPE_ARM64 = 0x0100000C
MACHO_MAGIC_64 = 0xFEEDFACF
MACHO_CIGAM_64 = 0xCFFAEDFE
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
FAT_CIGAM = 0xBEBAFECA
FAT_CIGAM_64 = 0xBFBAFECA
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


def is_dangerous_root(path: Path) -> bool:
    """避免 updater 對磁碟根目錄或 home 這類過寬路徑操作。"""

    resolved = path.resolve()
    if resolved == resolved.parent:
        return True
    try:
        return resolved == Path.home().resolve()
    except RuntimeError:
        return False


def is_reparse_or_symlink(path: Path) -> bool:
    """判斷 path 是否為 symlink 或 Windows junction / reparse point。"""

    if path.is_symlink():
        return True
    return is_junction(path)


def is_junction(path: Path) -> bool:
    """判斷 path 是否為 Windows junction / reparse point。"""

    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def has_unsafe_existing_path_component(path: Path, *, root: Path) -> bool:
    """檢查 root 到 path 之間既有 component 是否含 symlink/junction。"""

    path = path.absolute()
    root = root.absolute()
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    if (current.exists() or current.is_symlink()) and is_reparse_or_symlink(current):
        return True
    for part in relative.parts:
        current = current / part
        if (current.exists() or current.is_symlink()) and is_reparse_or_symlink(current):
            return True
    return False


def has_posix_executable_bit(path: Path) -> bool:
    """檢查一般檔案是否有 POSIX executable bit。"""

    try:
        return path.is_file() and bool(path.stat().st_mode & 0o111)
    except OSError:
        return False


def zip_member_has_executable_bit(info: zipfile.ZipInfo) -> bool:
    """檢查 zip member 是否保留 POSIX executable bit。"""

    mode = (info.external_attr >> 16) & 0o777
    return bool(mode & 0o111)


def zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    """判斷 zip member 是否是 POSIX symlink。"""

    mode = (info.external_attr >> 16) & 0o170000
    return mode == stat.S_IFLNK


def decode_zip_symlink_target(
    target_data: bytes,
    *,
    reason: str = "zip_symlink_target_unsafe",
) -> str:
    """讀取 zip symlink target；只接受 POSIX-style UTF-8 相對路徑文字。"""

    try:
        target_text = target_data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(reason) from exc
    if "\x00" in target_text or "\\" in target_text or not target_text:
        raise ValueError(reason)
    return target_text


def resolve_zip_relative_path(
    base: PurePosixPath,
    target: PurePosixPath,
) -> PurePosixPath | None:
    """以 POSIX path 規則解析 symlink 目標；逃出 zip root 時回傳 None。"""

    parts: list[str] = []
    for part in (*base.parts, *target.parts):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return PurePosixPath(*parts)


def resolve_zip_symlink_target(
    link_path: PurePosixPath,
    target_text: str,
) -> PurePosixPath | None:
    """解析 zip symlink target 在 archive root 內的正規相對路徑。"""

    target = PurePosixPath(target_text)
    if target.is_absolute() or not target.parts:
        return None
    return resolve_zip_relative_path(link_path.parent, target)


def symlink_target_stays_within_root(path: Path, *, root: Path) -> bool:
    """確認 symlink 使用相對目標且解析後仍留在 root 內。"""

    return symlink_target_relative_to_root(path, root=root) is not None


def symlink_target_relative_to_root(path: Path, *, root: Path) -> Path | None:
    """回傳 symlink target 相對於 root 的路徑；不安全時回傳 None。"""

    if os.name == "nt":
        return None
    try:
        target = path.readlink()
    except OSError:
        return None
    if target.is_absolute():
        return None
    try:
        resolved_target = (path.parent / target).resolve(strict=False)
        resolved_root = root.resolve(strict=False)
    except OSError:
        return None
    if not resolved_target.is_relative_to(resolved_root):
        return None
    return resolved_target.relative_to(resolved_root)


def validate_tree_links_stay_within_root(
    path: Path,
    *,
    root: Path,
    reason: str,
    forbidden_target_parts: frozenset[str] = frozenset(),
) -> None:
    """遞迴確認 tree 內沒有 junction，且 symlink 皆不逃出 root。"""

    if is_junction(path):
        raise ValueError(f"{reason}:{path}")
    if path.is_symlink():
        relative_target = symlink_target_relative_to_root(path, root=root)
        if relative_target is None:
            raise ValueError(f"{reason}:{path}")
        lower_parts = {part.casefold() for part in relative_target.parts}
        if forbidden_target_parts & lower_parts:
            raise ValueError(f"{reason}:{path}")
        return
    if not path.is_dir():
        return
    for child in path.iterdir():
        validate_tree_links_stay_within_root(
            child,
            root=root,
            reason=reason,
            forbidden_target_parts=forbidden_target_parts,
        )


def plist_value_is_true(value: object) -> bool:
    """判斷 plist 內常見 boolean 表示法是否為 true。"""

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes"}
    return False


def is_macho_arm64(data: bytes) -> bool:
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
