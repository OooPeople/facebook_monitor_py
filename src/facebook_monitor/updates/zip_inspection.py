"""Zip member preflight inspection shared by updater runtime and release gates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import zipfile

from facebook_monitor.updates.validation import SENSITIVE_RELEASE_PATH_PARTS
from facebook_monitor.updates.validation import decode_zip_symlink_target
from facebook_monitor.updates.validation import normalized_zip_member_key
from facebook_monitor.updates.validation import resolve_zip_symlink_target
from facebook_monitor.updates.validation import validate_zip_member_path
from facebook_monitor.updates.validation import zip_member_is_symlink
from facebook_monitor.updates.zip_policy import MAX_ZIP_ENTRIES
from facebook_monitor.updates.zip_policy import MAX_ZIP_SINGLE_FILE_BYTES
from facebook_monitor.updates.zip_policy import MAX_ZIP_SYMLINK_TARGET_BYTES
from facebook_monitor.updates.zip_policy import MAX_ZIP_UNCOMPRESSED_BYTES


@dataclass(frozen=True)
class ZipMemberInspectionPolicy:
    """描述 zip preflight 檢查的大小限制與 release/runtime 差異。"""

    max_entries: int = MAX_ZIP_ENTRIES
    max_single_file_bytes: int = MAX_ZIP_SINGLE_FILE_BYTES
    max_uncompressed_bytes: int = MAX_ZIP_UNCOMPRESSED_BYTES
    max_symlink_target_bytes: int = MAX_ZIP_SYMLINK_TARGET_BYTES
    forbidden_symlink_target_parts: frozenset[str] = SENSITIVE_RELEASE_PATH_PARTS
    allow_symlinks: bool = True
    count_symlink_targets_in_total: bool = False
    continue_after_entry_count_violation: bool = False


@dataclass(frozen=True)
class ZipMemberViolation:
    """保存 runtime 錯誤碼與 release gate 訊息。"""

    code: str
    message: str
    member_name: str = ""
    path: PurePosixPath | None = None


@dataclass(frozen=True)
class ZipInspectedMember:
    """保存已通過 path / duplicate 正規化的 zip member。"""

    info: zipfile.ZipInfo
    path: PurePosixPath
    name: str
    is_symlink: bool
    symlink_target: str | None = None


@dataclass(frozen=True)
class ZipInspectionResult:
    """Zip preflight 結果；呼叫端依使用情境決定累積訊息或 fail-fast。"""

    members: tuple[ZipInspectedMember, ...]
    names: frozenset[str]
    violations: tuple[ZipMemberViolation, ...]


@dataclass
class _ZipInspectionAccumulator:
    """保存 zip member inspection 的 mutable 狀態。"""

    inspected: list[ZipInspectedMember]
    violations: list[ZipMemberViolation]
    names: set[str]
    normalized_path_keys: set[str]
    paths: set[PurePosixPath]
    symlink_paths: set[PurePosixPath]
    total_uncompressed: int = 0

    @classmethod
    def create(cls) -> _ZipInspectionAccumulator:
        """建立空的 zip inspection accumulator。"""

        return cls(
            inspected=[],
            violations=[],
            names=set(),
            normalized_path_keys=set(),
            paths=set(),
            symlink_paths=set(),
        )

    def to_result(self) -> ZipInspectionResult:
        """將 mutable accumulator 收斂成不可變 inspection result。"""

        return ZipInspectionResult(
            members=tuple(self.inspected),
            names=frozenset(self.names),
            violations=tuple(self.violations),
        )


def inspect_zip_members(
    archive: zipfile.ZipFile,
    *,
    policy: ZipMemberInspectionPolicy = ZipMemberInspectionPolicy(),
) -> ZipInspectionResult:
    """檢查 zip member path、大小、duplicate 與 symlink 安全性。"""

    members = archive.infolist()
    state = _ZipInspectionAccumulator.create()
    if len(members) > policy.max_entries:
        state.violations.append(
            ZipMemberViolation(
                code="zip_too_many_entries",
                message="zip too many entries",
            )
        )
        if not policy.continue_after_entry_count_violation:
            return state.to_result()

    for member in members:
        if _inspect_zip_member(archive, member, policy=policy, state=state):
            break

    _record_symlink_parent_violations(state)
    return state.to_result()


def _inspect_zip_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    *,
    policy: ZipMemberInspectionPolicy,
    state: _ZipInspectionAccumulator,
) -> bool:
    """檢查單一 zip member；回傳是否因 total size 達限而停止。"""

    path = _register_zip_member_path(member, state)
    if path is None:
        return False
    normalized = path.as_posix()
    if zip_member_is_symlink(member):
        return _inspect_zip_symlink_member(
            archive,
            member,
            path,
            normalized=normalized,
            policy=policy,
            state=state,
        )
    return _inspect_regular_zip_member(
        member,
        path,
        normalized=normalized,
        policy=policy,
        state=state,
    )


def _register_zip_member_path(
    member: zipfile.ZipInfo,
    state: _ZipInspectionAccumulator,
) -> PurePosixPath | None:
    """驗證 zip member path 並登記 duplicate 檢查資料。"""

    try:
        path = validate_zip_member_path(member.filename)
    except ValueError:
        state.violations.append(
            ZipMemberViolation(
                code="zip_member_path_unsafe",
                message=f"zip member path unsafe: {member.filename}",
                member_name=member.filename,
            )
        )
        return None

    normalized = path.as_posix()
    path_key = normalized_zip_member_key(path)
    if normalized in state.names or path_key in state.normalized_path_keys:
        state.violations.append(
            ZipMemberViolation(
                code="zip_duplicate_member_path",
                message=f"zip duplicate entry: {normalized}",
                member_name=member.filename,
                path=path,
            )
        )
        return None

    state.names.add(normalized)
    state.normalized_path_keys.add(path_key)
    state.paths.add(path)
    return path


def _inspect_zip_symlink_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    path: PurePosixPath,
    *,
    normalized: str,
    policy: ZipMemberInspectionPolicy,
    state: _ZipInspectionAccumulator,
) -> bool:
    """檢查 symlink zip member；回傳是否應停止累積。"""

    state.symlink_paths.add(path)
    symlink_target: str | None = None
    stop_after_member = False
    if not policy.allow_symlinks:
        state.violations.append(
            ZipMemberViolation(
                code="zip_symlink_unsupported",
                message=f"zip symlink unsupported: {normalized}",
                member_name=member.filename,
                path=path,
            )
        )
    elif member.file_size > policy.max_symlink_target_bytes:
        state.violations.append(
            ZipMemberViolation(
                code="zip_symlink_target_too_large",
                message=f"zip symlink target too large: {normalized}",
                member_name=member.filename,
                path=path,
            )
        )
    else:
        stop_after_member = _add_uncompressed_size(
            state,
            member.file_size,
            policy=policy,
            enabled=policy.count_symlink_targets_in_total,
        )
        if not stop_after_member:
            symlink_target, violation = _inspect_zip_symlink_target(
                archive,
                member,
                path,
                policy=policy,
            )
            if violation is not None:
                state.violations.append(violation)
    state.inspected.append(
        ZipInspectedMember(
            info=member,
            path=path,
            name=normalized,
            is_symlink=True,
            symlink_target=symlink_target,
        )
    )
    return stop_after_member


def _inspect_regular_zip_member(
    member: zipfile.ZipInfo,
    path: PurePosixPath,
    *,
    normalized: str,
    policy: ZipMemberInspectionPolicy,
    state: _ZipInspectionAccumulator,
) -> bool:
    """檢查一般 zip member；回傳是否應停止累積。"""

    state.inspected.append(
        ZipInspectedMember(
            info=member,
            path=path,
            name=normalized,
            is_symlink=False,
        )
    )
    if member.is_dir():
        return False
    if member.file_size > policy.max_single_file_bytes:
        state.violations.append(
            ZipMemberViolation(
                code="zip_member_too_large",
                message=f"zip member too large: {normalized}",
                member_name=member.filename,
                path=path,
            )
        )
    return _add_uncompressed_size(
        state,
        member.file_size,
        policy=policy,
        enabled=True,
    )


def _add_uncompressed_size(
    state: _ZipInspectionAccumulator,
    size: int,
    *,
    policy: ZipMemberInspectionPolicy,
    enabled: bool,
) -> bool:
    """累積 uncompressed size；回傳是否超過總量限制。"""

    if not enabled:
        return False
    state.total_uncompressed += size
    if state.total_uncompressed <= policy.max_uncompressed_bytes:
        return False
    state.violations.append(
        ZipMemberViolation(
            code="zip_uncompressed_too_large",
            message="zip uncompressed size too large",
        )
    )
    return True


def _record_symlink_parent_violations(state: _ZipInspectionAccumulator) -> None:
    """標記任何落在 symlink parent 底下的 member path。"""

    for path in state.paths:
        if any(parent in state.symlink_paths for parent in path.parents):
            state.violations.append(
                ZipMemberViolation(
                    code="zip_member_path_unsafe",
                    message=f"zip member path unsafe: {path.as_posix()}",
                    path=path,
                )
            )


def _inspect_zip_symlink_target(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    path: PurePosixPath,
    *,
    policy: ZipMemberInspectionPolicy,
) -> tuple[str | None, ZipMemberViolation | None]:
    """讀取並檢查 zip symlink target。"""

    try:
        raw_target = archive.read(member)
    except (OSError, KeyError, zipfile.BadZipFile):
        return None, ZipMemberViolation(
            code="zip_symlink_target_unsafe",
            message=f"zip symlink target unreadable: {member.filename}",
            member_name=member.filename,
            path=path,
        )
    try:
        target_text = decode_zip_symlink_target(raw_target)
    except ValueError:
        return None, ZipMemberViolation(
            code="zip_symlink_target_unsafe",
            message=f"zip symlink target invalid: {member.filename}",
            member_name=member.filename,
            path=path,
        )
    resolved = resolve_zip_symlink_target(path, target_text)
    if resolved is None:
        return target_text, ZipMemberViolation(
            code="zip_symlink_target_unsafe",
            message=f"zip symlink target unsafe: {member.filename}",
            member_name=member.filename,
            path=path,
        )
    lower_parts = {part.casefold() for part in resolved.parts}
    if policy.forbidden_symlink_target_parts & lower_parts:
        return target_text, ZipMemberViolation(
            code="zip_symlink_target_unsafe",
            message=f"zip symlink target unsafe: {member.filename}",
            member_name=member.filename,
            path=path,
        )
    return target_text, None
