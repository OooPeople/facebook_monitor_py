"""獨立 updater 套用更新。

職責：在主程式已關閉後，讀取 pending update、重驗 zip SHA256、解壓到
staging、驗證 PyInstaller onedir 結構，並以保留 `data/` 的方式替換 app
files。此模組不讀寫 profile/DB/secrets 內容。
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from pathlib import Path
from pathlib import PurePosixPath
import os
import plistlib
import re
import shutil
import time
import uuid
import zipfile

from facebook_monitor.runtime.instance_lock import AppInstanceLockError
from facebook_monitor.runtime.instance_lock import AppInstanceLock
from facebook_monitor.runtime.instance_lock import acquire_app_instance_lock
from facebook_monitor.updates.artifacts import release_sha256_asset_name
from facebook_monitor.updates.artifacts import sanitize_release_asset_name
from facebook_monitor.updates.download import calculate_sha256
from facebook_monitor.updates.handoff import PendingUpdate
from facebook_monitor.updates.handoff import load_pending_update
from facebook_monitor.updates.handoff import validate_pending_update_paths
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_INFO_PLIST
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER
from facebook_monitor.updates.platforms import MACOS_ARM64_LAYOUT_POLICY
from facebook_monitor.updates.platforms import UpdaterLayoutPolicy
from facebook_monitor.updates.platforms import detect_layout_policy
from facebook_monitor.updates.platforms import macos_app_executable_staging_paths
from facebook_monitor.updates.platforms import missing_required_paths
from facebook_monitor.updates.validation import SENSITIVE_RELEASE_PATH_PARTS
from facebook_monitor.updates.validation import decode_zip_symlink_target
from facebook_monitor.updates.validation import has_posix_executable_bit
from facebook_monitor.updates.validation import has_unsafe_existing_path_component
from facebook_monitor.updates.validation import is_dangerous_root
from facebook_monitor.updates.validation import is_junction
from facebook_monitor.updates.validation import is_macho_arm64
from facebook_monitor.updates.validation import is_reparse_or_symlink
from facebook_monitor.updates.validation import plist_value_is_true
from facebook_monitor.updates.validation import resolve_zip_symlink_target
from facebook_monitor.updates.validation import validate_tree_links_stay_within_root
from facebook_monitor.updates.validation import zip_member_has_executable_bit
from facebook_monitor.updates.validation import zip_member_is_symlink
from facebook_monitor.updates.zip_policy import MAX_ZIP_ENTRIES
from facebook_monitor.updates.zip_policy import MAX_ZIP_SINGLE_FILE_BYTES
from facebook_monitor.updates.zip_policy import MAX_ZIP_SYMLINK_TARGET_BYTES
from facebook_monitor.updates.zip_policy import MAX_ZIP_UNCOMPRESSED_BYTES


STAGING_DIR_NAME = "update_staging"
BACKUP_DIR_NAME = "update_backups"
BACKUP_RETENTION_COUNT = 3


@dataclass(frozen=True)
class UpdaterApplyResult:
    """更新套用結果。"""

    status: str
    applied: bool
    message: str
    backup_dir: Path | None = None
    staging_dir: Path | None = None


def apply_pending_update_file(
    path: Path,
    *,
    wait_for_lock_seconds: float = 0,
    poll_seconds: float = 1,
    log_path: Path | None = None,
) -> UpdaterApplyResult:
    """從 pending update JSON 套用更新。"""

    try:
        pending = load_pending_update(path)
    except (OSError, ValueError) as exc:
        result = UpdaterApplyResult(
            status="failed",
            applied=False,
            message=str(exc),
        )
        _append_updater_log(log_path, result)
        return result
    return apply_loaded_pending_update_file(
        pending,
        path,
        wait_for_lock_seconds=wait_for_lock_seconds,
        poll_seconds=poll_seconds,
        log_path=log_path,
    )


def apply_loaded_pending_update_file(
    pending: PendingUpdate,
    path: Path,
    *,
    wait_for_lock_seconds: float = 0,
    poll_seconds: float = 1,
    log_path: Path | None = None,
) -> UpdaterApplyResult:
    """使用已讀取的 pending update 套用更新並處理 handoff cleanup。"""

    result = apply_pending_update(
        pending,
        wait_for_lock_seconds=wait_for_lock_seconds,
        poll_seconds=poll_seconds,
    )
    cleanup_warnings: tuple[str, ...] = ()
    if result.applied:
        cleanup_warnings = (
            *_cleanup_applied_update(path, pending),
            *_cleanup_old_backup_dirs(
                pending.runtime_dir / BACKUP_DIR_NAME,
                keep_count=BACKUP_RETENTION_COUNT,
                preserve=result.backup_dir,
            ),
        )
    _append_updater_log(log_path, result)
    _append_cleanup_warning_log(log_path, cleanup_warnings)
    return result


def apply_pending_update(
    pending: PendingUpdate,
    *,
    wait_for_lock_seconds: float = 0,
    poll_seconds: float = 1,
) -> UpdaterApplyResult:
    """套用已驗證更新包；主程式仍執行時會拒絕替換。"""

    try:
        validate_pending_update_paths(pending)
        _validate_pending_hash(pending)
        with _wait_for_app_lock(
            pending.runtime_dir,
            wait_for_lock_seconds=wait_for_lock_seconds,
            poll_seconds=poll_seconds,
        ):
            layout_policy = detect_layout_policy(pending.app_base_dir)
            validate_macos_zip_executable_bits(
                pending.zip_path,
                layout_policy=layout_policy,
            )
            staging_dir = _prepare_empty_dir(
                pending.runtime_dir / STAGING_DIR_NAME / sanitize_release_asset_name(
                    pending.version
                ),
                work_root=pending.runtime_dir,
            )
            safe_extract_zip(pending.zip_path, staging_dir)
            staging_app_root = find_staging_app_root(
                staging_dir,
                layout_policy=layout_policy,
            )
            validate_staging_app_root(
                staging_app_root,
                layout_policy=layout_policy,
                expected_version=pending.version,
            )
            validate_current_app_root(
                pending.app_base_dir,
                layout_policy=layout_policy,
                data_dir=pending.data_dir,
            )
            backup_dir = _prepare_empty_dir(
                pending.runtime_dir
                / BACKUP_DIR_NAME
                / _backup_folder_name(pending.version),
                work_root=pending.runtime_dir,
            )
            backup_current_app_files(
                app_base_dir=pending.app_base_dir,
                backup_dir=backup_dir,
                data_dir=pending.data_dir,
            )
            try:
                replace_app_files(
                    staging_app_root=staging_app_root,
                    app_base_dir=pending.app_base_dir,
                    data_dir=pending.data_dir,
                )
            except Exception:
                restore_backup(
                    app_base_dir=pending.app_base_dir,
                    backup_dir=backup_dir,
                    data_dir=pending.data_dir,
                )
                raise
    except AppInstanceLockError as exc:
        return UpdaterApplyResult(
            status="app_running",
            applied=False,
            message=str(exc),
        )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return UpdaterApplyResult(
            status="failed",
            applied=False,
            message=str(exc),
        )
    return UpdaterApplyResult(
        status="applied",
        applied=True,
        message="updated",
        backup_dir=backup_dir,
        staging_dir=staging_dir,
    )


@contextmanager
def _wait_for_app_lock(
    runtime_dir: Path,
    *,
    wait_for_lock_seconds: float,
    poll_seconds: float,
) -> Iterator[AppInstanceLock]:
    """等待主程式釋放 app lock，避免 updater 啟動太早就失敗。"""

    deadline = time.monotonic() + max(0, wait_for_lock_seconds)
    last_error: AppInstanceLockError | None = None
    while True:
        try:
            with acquire_app_instance_lock(runtime_dir, "updater") as app_lock:
                yield app_lock
                return
        except AppInstanceLockError as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                raise last_error
            time.sleep(max(0.1, poll_seconds))


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
        symlink_member_paths: set[PurePosixPath] = set()
        for member in members:
            member_path = _zip_member_relative_path(member)
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

    return {
        _zip_member_relative_path(member): member
        for member in members
        if not member.is_dir()
    }


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

    member_path = PurePosixPath(member.filename.replace("\\", "/"))
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ValueError("zip_member_path_unsafe")
    return member_path


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


def find_staging_app_root(
    staging_dir: Path,
    *,
    layout_policy: UpdaterLayoutPolicy | None = None,
) -> Path:
    """尋找 update zip 內的 app root，支援 zip 包住單一 `facebook-monitor/` 目錄。"""

    policy = layout_policy or detect_layout_policy(staging_dir)
    if policy.app_entry(staging_dir).is_file():
        return staging_dir
    child_dirs = [path for path in staging_dir.iterdir() if path.is_dir()]
    for child in child_dirs:
        if policy.app_entry(child).is_file():
            return child
    raise ValueError("staging_app_root_missing")


def validate_staging_app_root(
    app_root: Path,
    *,
    layout_policy: UpdaterLayoutPolicy | None = None,
    expected_version: str | None = None,
) -> None:
    """驗證 staging app root 至少包含目前 frozen onedir 必要檔案。"""

    policy = layout_policy or detect_layout_policy(app_root)
    missing = missing_required_paths(
        app_root,
        required_paths=policy.required_staging_files,
        any_groups=policy.required_staging_any_groups,
    )
    if missing:
        raise ValueError("staging_required_file_missing:" + str(missing[0]))
    _validate_required_files(
        app_root,
        policy.required_staging_files,
        reason_prefix="staging_required_file_not_file",
    )
    selected_any_paths = _validate_any_group_files(
        app_root,
        policy.required_staging_any_groups,
        reason_prefix="staging_required_file_not_file",
    )
    if policy.platform_key == "macos-arm64":
        _validate_macos_app_root(
            app_root,
            browser_paths=selected_any_paths,
            expected_version=expected_version,
        )
    _validate_app_tree_links(app_root, data_dir=None)
    _validate_no_sensitive_runtime_paths(app_root)


def validate_current_app_root(
    app_root: Path,
    *,
    layout_policy: UpdaterLayoutPolicy | None = None,
    data_dir: Path | None = None,
) -> None:
    """驗證目前要被替換的 app root 看起來像本專案的 frozen onedir。"""

    if is_dangerous_root(app_root):
        raise ValueError("app_base_dir_unsafe")
    policy = layout_policy or detect_layout_policy(app_root)
    missing = missing_required_paths(
        app_root,
        required_paths=policy.required_current_paths,
        any_groups=policy.required_current_any_groups,
    )
    if missing:
        raise ValueError("app_required_file_missing:" + str(missing[0]))
    _validate_app_tree_links(app_root, data_dir=data_dir)


def backup_current_app_files(
    *,
    app_base_dir: Path,
    backup_dir: Path,
    data_dir: Path,
) -> None:
    """備份目前 app files；portable `data/` 永遠不進備份或替換範圍。"""

    for child in app_base_dir.iterdir():
        if _is_protected_data_path(child, data_dir):
            continue
        _copy_path(child, backup_dir / child.name, source_root=app_base_dir)


def replace_app_files(
    *,
    staging_app_root: Path,
    app_base_dir: Path,
    data_dir: Path,
) -> None:
    """用 staging app files 替換目前 app files，保留 data dir。"""

    for child in list(app_base_dir.iterdir()):
        if _is_protected_data_path(child, data_dir):
            continue
        _remove_path(child)
    for child in staging_app_root.iterdir():
        if child.name == "data":
            continue
        _copy_path(child, app_base_dir / child.name, source_root=staging_app_root)


def restore_backup(
    *,
    app_base_dir: Path,
    backup_dir: Path,
    data_dir: Path,
) -> None:
    """替換失敗時嘗試從備份還原 app files。"""

    for child in list(app_base_dir.iterdir()):
        if _is_protected_data_path(child, data_dir):
            continue
        _remove_path(child)
    for child in backup_dir.iterdir():
        _copy_path(child, app_base_dir / child.name, source_root=backup_dir)


def _validate_pending_hash(pending: PendingUpdate) -> None:
    """套用前重算 zip SHA256，避免 handoff 後檔案被替換。"""

    actual = calculate_sha256(pending.zip_path)
    if actual != pending.expected_sha256:
        raise ValueError("pending_zip_sha256_mismatch")


def _prepare_empty_dir(path: Path, *, work_root: Path) -> Path:
    """建立空目錄；既有內容會先刪除。"""

    resolved_work_root = work_root.resolve()
    resolved_path = path.resolve(strict=False)
    if resolved_path == resolved_work_root or not resolved_path.is_relative_to(
        resolved_work_root
    ):
        raise ValueError("update_work_dir_unsafe")
    if has_unsafe_existing_path_component(path, root=work_root):
        raise ValueError("update_work_dir_unsafe")
    if path.exists() or path.is_symlink():
        if is_reparse_or_symlink(path):
            raise ValueError("update_work_dir_unsafe")
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _backup_folder_name(version: str) -> str:
    """建立可排序且檔名安全的 backup folder name。"""

    safe_version = sanitize_release_asset_name(version)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{safe_version}-{timestamp}-{uuid.uuid4().hex[:8]}"


def _is_protected_data_path(path: Path, data_dir: Path) -> bool:
    """判斷 path 是否是必須保留的 data dir。"""

    try:
        return path.resolve() == data_dir.resolve()
    except OSError:
        return False


def _copy_path(source: Path, destination: Path, *, source_root: Path) -> None:
    """複製檔案或目錄。"""

    validate_tree_links_stay_within_root(
        source,
        root=source_root,
        reason="app_path_unsafe",
    )
    if source.is_symlink():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source.readlink())
        return
    if is_junction(source):
        raise ValueError("app_path_unsafe")
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _remove_path(path: Path) -> None:
    """移除檔案或目錄。"""

    if is_junction(path):
        raise ValueError("app_path_unsafe")
    if path.is_symlink():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _validate_required_files(
    app_root: Path,
    required_paths: tuple[str, ...],
    *,
    reason_prefix: str,
) -> None:
    """確認 required paths 皆為一般檔案。"""

    for relative_path in required_paths:
        path = app_root / relative_path
        if not path.is_file():
            raise ValueError(f"{reason_prefix}:{path}")


def _validate_any_group_files(
    app_root: Path,
    any_groups: tuple[tuple[str, ...], ...],
    *,
    reason_prefix: str,
) -> tuple[str, ...]:
    """確認 any group 命中的 path 是檔案，並回傳命中的相對路徑。"""

    selected_paths: list[str] = []
    for group in any_groups:
        for relative_path in group:
            path = app_root / relative_path
            if path.exists():
                if not path.is_file():
                    raise ValueError(f"{reason_prefix}:{path}")
                selected_paths.append(relative_path)
                break
    return tuple(selected_paths)


def _validate_macos_app_root(
    app_root: Path,
    *,
    browser_paths: tuple[str, ...],
    expected_version: str | None,
) -> None:
    """驗證 macOS staging root 的 executable、Mach-O 與 `.app` metadata。"""

    executable_paths = (
        *macos_app_executable_staging_paths(MACOS_ARM64_LAYOUT_POLICY),
        *browser_paths,
    )
    for relative_path in executable_paths:
        path = app_root / relative_path
        # Windows 無法可靠呈現 zip 內的 POSIX executable bit；跨平台套用流程
        # 已先用 zip metadata 驗證，POSIX 平台再檢查解壓後的實際 mode。
        if os.name != "nt" and not has_posix_executable_bit(path):
            raise ValueError(f"staging_executable_bit_missing:{path}")
        if not is_macho_arm64(_read_file_prefix(path)):
            raise ValueError(f"staging_macho_arm64_missing:{path}")
    plist_path = app_root / MACOS_APP_BUNDLE_INFO_PLIST
    try:
        plist = plistlib.loads(plist_path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ValueError("staging_macos_info_plist_invalid") from exc
    if plist.get("CFBundleExecutable") != Path(MACOS_APP_BUNDLE_LAUNCHER).name:
        raise ValueError("staging_macos_bundle_executable_mismatch")
    if plist_value_is_true(plist.get("LSUIElement")) or plist_value_is_true(
        plist.get("LSBackgroundOnly")
    ):
        raise ValueError("staging_macos_bundle_hidden_from_dock")
    if expected_version is not None:
        if plist.get("CFBundleShortVersionString") != expected_version:
            raise ValueError("staging_macos_bundle_short_version_mismatch")
        if plist.get("CFBundleVersion") != expected_version:
            raise ValueError("staging_macos_bundle_version_mismatch")


def _validate_no_sensitive_runtime_paths(app_root: Path) -> None:
    """拒絕 update zip 夾帶 runtime/profile/logs 類資料。"""

    for path in app_root.rglob("*"):
        try:
            relative = path.relative_to(app_root)
        except ValueError:
            continue
        lower_parts = {part.casefold() for part in relative.parts}
        if SENSITIVE_RELEASE_PATH_PARTS & lower_parts:
            raise ValueError(f"staging_private_data_path:{path}")


def _validate_app_tree_links(app_root: Path, *, data_dir: Path | None) -> None:
    """拒絕 app files 內的 symlink/junction，避免 backup/replace follow 到外部。"""

    for child in app_root.iterdir():
        if data_dir is not None and _is_protected_data_path(child, data_dir):
            continue
        validate_tree_links_stay_within_root(
            child,
            root=app_root,
            reason="app_path_unsafe",
            forbidden_target_parts=SENSITIVE_RELEASE_PATH_PARTS,
        )


def _read_file_prefix(path: Path, *, size: int = 4096) -> bytes:
    """讀取 Mach-O 判斷所需的檔案前段，避免載入大型 browser executable。"""

    with path.open("rb") as file:
        return file.read(size)


def _append_updater_log(log_path: Path | None, result: UpdaterApplyResult) -> None:
    """寫入 updater 結果 log；log 失敗不應影響更新結果。"""

    if log_path is None:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        line = (
            f"{datetime.now(timezone.utc).isoformat()} "
            f"status={result.status} applied={str(result.applied).lower()} "
            f"message={result.message}\n"
        )
        log_path.open("a", encoding="utf-8").write(line)
    except OSError:
        return


def _append_cleanup_warning_log(log_path: Path | None, warnings: tuple[str, ...]) -> None:
    """寫入 cleanup warning；清理失敗不應遮蔽套用結果。"""

    if log_path is None or not warnings:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as file:
            timestamp = datetime.now(timezone.utc).isoformat()
            for warning in warnings:
                file.write(f"{timestamp} cleanup_warning={warning}\n")
    except OSError:
        return


def _cleanup_applied_update(
    pending_path: Path,
    pending: PendingUpdate,
) -> tuple[str, ...]:
    """成功套用後清除本次下載與 handoff 檔，失敗時保留供診斷。"""

    warnings: list[str] = []
    updates_dir = pending.data_dir / "updates"
    _cleanup_file(pending.zip_path, label="zip", warnings=warnings)
    _cleanup_file(
        pending.zip_path.with_name(release_sha256_asset_name(pending.zip_path.name)),
        label="sha256",
        warnings=warnings,
    )
    parent = pending.zip_path.parent
    try:
        resolved_parent = parent.resolve()
        resolved_updates_dir = updates_dir.resolve()
        if (
            resolved_parent != resolved_updates_dir
            and resolved_parent.is_relative_to(resolved_updates_dir)
        ):
            parent.rmdir()
    except OSError as exc:
        warnings.append(_cleanup_warning("updates_parent", parent, exc))
    _cleanup_file(pending_path, label="pending", warnings=warnings)
    staging_dir = pending.runtime_dir / STAGING_DIR_NAME / sanitize_release_asset_name(
        pending.version
    )
    try:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
    except OSError as exc:
        warnings.append(_cleanup_warning("staging", staging_dir, exc))
    return tuple(warnings)


def _cleanup_file(path: Path, *, label: str, warnings: list[str]) -> None:
    """刪除 cleanup 檔案並收集失敗原因。"""

    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        warnings.append(_cleanup_warning(label, path, exc))


def _cleanup_old_backup_dirs(
    backup_root: Path,
    *,
    keep_count: int,
    preserve: Path | None,
) -> tuple[str, ...]:
    """保留最近 backup，清除舊 backup；失敗只回傳 warning。"""

    warnings: list[str] = []
    if keep_count < 1:
        keep_count = 1
    if not backup_root.exists():
        return ()
    try:
        resolved_root = backup_root.resolve()
        resolved_parent = backup_root.parent.resolve()
    except OSError as exc:
        return (_cleanup_warning("backup_root_resolve", backup_root, exc),)
    if is_reparse_or_symlink(backup_root):
        return (
            _cleanup_warning(
                "backup_root_unsafe",
                backup_root,
                OSError("backup root is symlink or junction"),
            ),
        )
    if resolved_root == resolved_parent or not resolved_root.is_relative_to(
        resolved_parent
    ):
        return (
            _cleanup_warning(
                "backup_root_unsafe",
                backup_root,
                OSError("backup root escaped runtime directory"),
            ),
        )
    try:
        backup_dirs = [path for path in backup_root.iterdir() if path.is_dir()]
    except OSError as exc:
        return (_cleanup_warning("backup_list", backup_root, exc),)

    sortable_dirs: list[tuple[float, str, Path]] = []
    for backup_dir in backup_dirs:
        timestamp = _backup_timestamp(backup_dir)
        if timestamp is None:
            warnings.append(
                _cleanup_warning(
                    "backup_unknown",
                    backup_dir,
                    OSError("backup directory name is not managed by updater"),
                )
            )
            continue
        sortable_dirs.append((timestamp, backup_dir.name, backup_dir))

    keep: set[Path] = set()
    if preserve is not None:
        keep.add(preserve.resolve())
    for _, _, backup_dir in sorted(sortable_dirs, reverse=True):
        if len(keep) >= keep_count:
            break
        resolved = _resolve_safe_backup_dir(
            backup_dir,
            resolved_root=resolved_root,
            warnings=warnings,
        )
        if resolved is None:
            continue
        keep.add(resolved)
    for _, _, backup_dir in sortable_dirs:
        resolved = _resolve_safe_backup_dir(
            backup_dir,
            resolved_root=resolved_root,
            warnings=warnings,
        )
        if resolved is None:
            continue
        if resolved in keep:
            continue
        if (
            _resolve_safe_backup_dir(
                backup_dir,
                resolved_root=resolved_root,
                warnings=warnings,
            )
            is None
        ):
            continue
        try:
            shutil.rmtree(backup_dir)
        except OSError as exc:
            warnings.append(_cleanup_warning("backup", backup_dir, exc))
    return tuple(warnings)


def _resolve_safe_backup_dir(
    backup_dir: Path,
    *,
    resolved_root: Path,
    warnings: list[str],
) -> Path | None:
    """確認 backup dir 仍留在 backup root 內，安全時回傳 resolved path。"""

    if is_reparse_or_symlink(backup_dir):
        warnings.append(
            _cleanup_warning(
                "backup_unsafe",
                backup_dir,
                OSError("backup directory is symlink or junction"),
            )
        )
        return None
    try:
        resolved = backup_dir.resolve()
    except OSError as exc:
        warnings.append(_cleanup_warning("backup_resolve", backup_dir, exc))
        return None
    if not resolved.is_relative_to(resolved_root):
        warnings.append(
            _cleanup_warning(
                "backup_unsafe",
                backup_dir,
                OSError("backup directory escaped backup root"),
            )
        )
        return None
    return resolved


def _backup_timestamp(path: Path) -> float | None:
    """解析 updater backup folder timestamp；未知格式不自動刪除。"""

    match = re.fullmatch(
        r".+-(\d{8}T\d{6}(?:\d{6})?Z)(?:-[0-9a-f]{8})?",
        path.name,
    )
    if match is None:
        return None
    value = match.group(1)
    fmt = "%Y%m%dT%H%M%S%fZ" if len(value) == 22 else "%Y%m%dT%H%M%SZ"
    try:
        return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _cleanup_warning(label: str, path: Path, exc: OSError) -> str:
    """整理 cleanup warning，避免 updater log 只剩模糊失敗。"""

    return (
        f"{label}:{path}:"
        f"{exc.__class__.__name__}:{exc}"
    )
