"""Updater app tree validation helpers."""

from __future__ import annotations

from pathlib import Path
import os
import plistlib

from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_INFO_PLIST
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_IDENTIFIER
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER
from facebook_monitor.updates.platforms import MACOS_ARM64_LAYOUT_POLICY
from facebook_monitor.updates.platforms import UpdaterLayoutPolicy
from facebook_monitor.updates.platforms import detect_layout_policy
from facebook_monitor.updates.platforms import macos_app_executable_staging_paths
from facebook_monitor.updates.platforms import missing_required_paths
from facebook_monitor.updates.validation import SENSITIVE_RELEASE_PATH_PARTS
from facebook_monitor.updates.validation import has_posix_executable_bit
from facebook_monitor.updates.validation import is_dangerous_root
from facebook_monitor.updates.validation import is_macho_arm64
from facebook_monitor.updates.validation import plist_value_is_true
from facebook_monitor.updates.validation import validate_tree_links_stay_within_root


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


def _is_protected_data_path(path: Path, data_dir: Path) -> bool:
    """判斷 path 是否是必須保留的 data dir。"""

    try:
        return path.resolve() == data_dir.resolve()
    except OSError:
        return False


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
    _validate_macos_launcher_bundle_metadata(
        app_root,
        expected_version=expected_version,
    )


def _read_macos_info_plist(plist_path: Path, *, reason: str) -> dict[str, object]:
    """讀取 macOS bundle Info.plist，失敗時保留 updater reason。"""

    try:
        value = plistlib.loads(plist_path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ValueError(reason) from exc
    if not isinstance(value, dict):
        raise ValueError(reason)
    return value


def _validate_macos_launcher_bundle_metadata(
    app_root: Path,
    *,
    expected_version: str | None,
) -> None:
    """確認主 macOS `.app` metadata 不會破壞 launcher / Dock 語義。"""

    plist = _read_macos_info_plist(
        app_root / MACOS_APP_BUNDLE_INFO_PLIST,
        reason="staging_macos_info_plist_invalid",
    )
    if plist.get("CFBundleExecutable") != Path(MACOS_APP_BUNDLE_LAUNCHER).name:
        raise ValueError("staging_macos_bundle_executable_mismatch")
    if plist.get("CFBundleIdentifier") != MACOS_APP_BUNDLE_IDENTIFIER:
        raise ValueError("staging_macos_bundle_identifier_mismatch")
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
