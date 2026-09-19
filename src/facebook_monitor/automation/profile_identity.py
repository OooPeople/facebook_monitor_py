"""Managed automation profile identity helper。

職責：在 managed profile root 內建立不含帳號資料的 UUID marker，並以 marker
產生跨資料目錄搬移仍穩定的 opaque profile scope key。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from uuid import UUID
from uuid import uuid4


PROFILE_ID_MARKER_FILENAME = ".facebook-monitor-profile-id"


@dataclass(frozen=True)
class ManagedProfileIdentity:
    """保存 runtime 使用的 opaque managed profile identity。"""

    profile_scope_key: str
    marker_path: Path
    marker_uuid: UUID


def load_or_create_managed_profile_identity(
    *,
    profiles_root: Path,
    profile_dir: Path,
) -> ManagedProfileIdentity:
    """讀取或原子建立 managed profile UUID marker。"""

    expanded_root = profiles_root.expanduser()
    expanded_root.mkdir(parents=True, exist_ok=True)
    resolved_root = expanded_root.resolve()
    resolved_profile = profile_dir.expanduser().resolve()
    if not resolved_profile.is_relative_to(resolved_root):
        raise ValueError("managed profile must stay under profiles root")
    if resolved_profile == resolved_root:
        raise ValueError("managed profile must be a child of profiles root")
    resolved_profile.mkdir(parents=True, exist_ok=True)
    marker_path = resolved_profile / PROFILE_ID_MARKER_FILENAME
    marker_uuid = _load_or_create_marker(marker_path)
    scope_key = hashlib.sha256(f"managed-profile-id-v1:{marker_uuid}".encode("ascii")).hexdigest()
    return ManagedProfileIdentity(
        profile_scope_key=scope_key,
        marker_path=marker_path,
        marker_uuid=marker_uuid,
    )


def read_managed_profile_identity(
    *,
    profiles_root: Path,
    profile_dir: Path,
) -> ManagedProfileIdentity | None:
    """唯讀取得既有 managed profile identity；marker 不存在時不建立。"""

    resolved_root = profiles_root.expanduser().resolve()
    resolved_profile = profile_dir.expanduser().resolve()
    if not resolved_profile.is_relative_to(resolved_root):
        raise ValueError("managed profile must stay under profiles root")
    if resolved_profile == resolved_root:
        raise ValueError("managed profile must be a child of profiles root")
    marker_path = resolved_profile / PROFILE_ID_MARKER_FILENAME
    try:
        marker_uuid = _read_marker(marker_path, apply_private_permissions=False)
    except FileNotFoundError:
        return None
    scope_key = hashlib.sha256(f"managed-profile-id-v1:{marker_uuid}".encode("ascii")).hexdigest()
    return ManagedProfileIdentity(
        profile_scope_key=scope_key,
        marker_path=marker_path,
        marker_uuid=marker_uuid,
    )


def _load_or_create_marker(marker_path: Path) -> UUID:
    """以同目錄完整暫存檔加 atomic hard-link 發布 marker。"""

    try:
        return _read_marker(marker_path)
    except FileNotFoundError:
        pass
    candidate = uuid4()
    temp_path = marker_path.with_name(
        f".{marker_path.name}.{uuid4().hex}.tmp"
    )
    _write_candidate_marker(temp_path, candidate)
    try:
        os.link(temp_path, marker_path)
    except FileExistsError:
        return _read_marker(marker_path)
    finally:
        temp_path.unlink(missing_ok=True)
    _apply_private_permissions(marker_path)
    return candidate


def _write_candidate_marker(marker_path: Path, candidate: UUID) -> None:
    """先完整寫入 private temporary marker，避免其他 process 讀到半筆。"""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(marker_path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as file:
            file.write(f"{candidate}\n")
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        marker_path.unlink(missing_ok=True)
        raise
    _apply_private_permissions(marker_path)


def _read_marker(
    marker_path: Path,
    *,
    apply_private_permissions: bool = True,
) -> UUID:
    """讀取 marker 並拒絕 symlink、空值或非 UUID 內容。"""

    if marker_path.is_symlink():
        raise ValueError("managed profile identity marker must not be a symlink")
    raw_value = marker_path.read_text(encoding="ascii").strip()
    try:
        marker_uuid = UUID(raw_value)
    except (AttributeError, ValueError) as exc:
        raise ValueError("managed profile identity marker is invalid") from exc
    if apply_private_permissions:
        _apply_private_permissions(marker_path)
    return marker_uuid


def _apply_private_permissions(marker_path: Path) -> None:
    """Best-effort 將 marker 權限限制為 owner read/write。"""

    try:
        marker_path.chmod(0o600)
    except OSError:
        return


__all__ = [
    "ManagedProfileIdentity",
    "PROFILE_ID_MARKER_FILENAME",
    "load_or_create_managed_profile_identity",
    "read_managed_profile_identity",
]
