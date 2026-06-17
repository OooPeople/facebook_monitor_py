"""Admin tool：建立 release zip 與同名 SHA256 檔。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
import stat
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.updates.artifacts import RELEASE_ARCHIVE_ROOT_NAME
from facebook_monitor.updates.artifacts import release_sha256_asset_name
from facebook_monitor.updates.artifacts import update_artifact_policy_for_key
from facebook_monitor.updates.artifacts import UpdateArtifactPolicy
from facebook_monitor.updates.platforms import MACOS_ARM64_LAYOUT_POLICY
from facebook_monitor.updates.platforms import WINDOWS_LAYOUT_POLICY
from facebook_monitor.updates.platforms import UpdaterLayoutPolicy
from facebook_monitor.updates.platforms import macos_known_executable_staging_paths
from facebook_monitor.updates.platforms import missing_required_paths
from facebook_monitor.updates.checksum import calculate_sha256
from facebook_monitor.updates.checksum import render_sha256_sidecar
from facebook_monitor.updates.validation import SENSITIVE_RELEASE_PATH_PARTS
from facebook_monitor.updates.validation import has_posix_executable_bit
from facebook_monitor.updates.validation import is_macho_arm64
from facebook_monitor.updates.validation import validate_tree_links_stay_within_root
from facebook_monitor.version import APP_VERSION


ZIP_ROOT_NAME = RELEASE_ARCHIVE_ROOT_NAME
MACOS_FIRST_RUN_README = """Facebook Monitor macOS 首次開啟說明

如果第一次從 GitHub Release 下載後，開啟 Facebook Monitor.app 時出現
「已損毀」、「無法驗證開發者」或被 macOS 阻擋，請打開 Terminal 執行：

cd ~/Downloads
xattr -dr com.apple.quarantine "./facebook-monitor"

如果你把資料夾解壓到其他位置，請把 ~/Downloads 改成實際位置。完成後
再開啟 Facebook Monitor.app。

這個步驟通常只在第一次用瀏覽器從 GitHub 下載時需要；之後從 app 內更新器
下載並替換新版，就不需要再執行一次這個指令。

這是因為目前 macOS 版尚未做 Developer ID signing / notarization；
從瀏覽器下載的 zip 會被 macOS 加上 com.apple.quarantine 標記。
這個指令只會移除 macOS 對這次瀏覽器下載加上的 quarantine 標記，
不等於正式簽章或 notarization。
"""


@dataclass(frozen=True)
class ReleaseZipTextFile:
    """描述 release zip 需要額外放入的使用者文字說明。"""

    relative_path: str
    content: str


@dataclass(frozen=True)
class ReleaseZipTarget:
    """描述 release zip 的平台命名與 onedir layout。"""

    artifact_policy: UpdateArtifactPolicy
    layout_policy: UpdaterLayoutPolicy
    executable_paths: frozenset[str] = frozenset()
    extra_text_files: tuple[ReleaseZipTextFile, ...] = ()


@dataclass(frozen=True)
class ReleaseZipResult:
    """release zip 建立結果。"""

    zip_path: Path
    sha256_path: Path
    sha256: str


RELEASE_ZIP_TARGETS = {
    "windows": ReleaseZipTarget(
        artifact_policy=update_artifact_policy_for_key("windows"),
        layout_policy=WINDOWS_LAYOUT_POLICY,
    ),
    "macos-arm64": ReleaseZipTarget(
        artifact_policy=update_artifact_policy_for_key("macos-arm64"),
        layout_policy=MACOS_ARM64_LAYOUT_POLICY,
        executable_paths=frozenset(
            macos_known_executable_staging_paths(MACOS_ARM64_LAYOUT_POLICY)
        ),
        extra_text_files=(
            ReleaseZipTextFile(
                relative_path="README.txt",
                content=MACOS_FIRST_RUN_README,
            ),
        ),
    ),
}


def parse_args() -> argparse.Namespace:
    """解析 CLI 參數。"""

    parser = argparse.ArgumentParser(
        description=(
            "Create a platform release zip and matching .sha256 from dist/facebook-monitor."
        )
    )
    parser.add_argument(
        "--platform",
        choices=tuple(RELEASE_ZIP_TARGETS),
        default="windows",
        help="Release artifact platform.",
    )
    parser.add_argument(
        "--version",
        default=APP_VERSION,
        help="Release version. Defaults to facebook_monitor.version.APP_VERSION.",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=ROOT / "dist",
        help="Directory that contains the frozen onedir app and receives the zip.",
    )
    parser.add_argument(
        "--app-root",
        type=Path,
        default=None,
        help="Frozen onedir app root. Defaults to <dist-dir>/facebook-monitor.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output zip path. Defaults to the platform release asset name.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing zip or .sha256 file.",
    )
    return parser.parse_args()


def create_release_zip(
    *,
    platform_name: str,
    version: str = APP_VERSION,
    dist_dir: Path = ROOT / "dist",
    app_root: Path | None = None,
    output: Path | None = None,
    force: bool = False,
) -> ReleaseZipResult:
    """建立指定平台 release zip，並產生同名 `.sha256`。"""

    target = _release_zip_target(platform_name)
    resolved_dist_dir = dist_dir.resolve()
    resolved_app_root = (app_root or (resolved_dist_dir / ZIP_ROOT_NAME)).resolve()
    zip_path = (
        output.resolve()
        if output is not None
        else resolved_dist_dir / target.artifact_policy.asset_name(version)
    )
    sha256_path = zip_path.with_name(release_sha256_asset_name(zip_path.name))

    _validate_release_source_tree(resolved_app_root, target.layout_policy)
    _prepare_output_paths(zip_path, sha256_path, force=force)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    _write_release_zip(
        resolved_app_root,
        zip_path,
        executable_paths=target.executable_paths,
        extra_text_files=target.extra_text_files,
    )
    digest = calculate_sha256(zip_path)
    _write_sha256_file(sha256_path, digest=digest, zip_name=zip_path.name)
    return ReleaseZipResult(zip_path=zip_path, sha256_path=sha256_path, sha256=digest)


def _release_zip_target(platform_name: str) -> ReleaseZipTarget:
    """回傳 release zip 平台策略。"""

    policy = update_artifact_policy_for_key(platform_name)
    return RELEASE_ZIP_TARGETS[policy.platform_key]


def _validate_release_source_tree(
    app_root: Path,
    layout_policy: UpdaterLayoutPolicy,
) -> None:
    """確認待壓縮 onedir 符合平台 layout，且未混入 runtime 私密資料。"""

    if not app_root.is_dir():
        raise ValueError(f"release_zip_missing_app_root:{app_root}")
    missing = missing_required_paths(
        app_root,
        required_paths=layout_policy.required_staging_files,
        any_groups=layout_policy.required_staging_any_groups,
    )
    if missing:
        missing_text = ", ".join(str(path) for path in missing)
        raise ValueError(f"release_zip_missing_required_path:{missing_text}")
    validate_tree_links_stay_within_root(
        app_root,
        root=app_root,
        reason="release_zip_unsafe_link",
        forbidden_target_parts=SENSITIVE_RELEASE_PATH_PARTS,
    )
    _validate_macos_release_executables(app_root, layout_policy)
    for path in app_root.rglob("*"):
        relative_path = path.relative_to(app_root)
        lower_parts = {part.casefold() for part in relative_path.parts}
        if SENSITIVE_RELEASE_PATH_PARTS & lower_parts:
            raise ValueError(f"release_zip_sensitive_path:{relative_path.as_posix()}")


def _validate_macos_release_executables(
    app_root: Path,
    layout_policy: UpdaterLayoutPolicy,
) -> None:
    """壓縮 macOS arm64 artifact 前先確認可執行檔 architecture 與 mode。"""

    if layout_policy.platform_key != MACOS_ARM64_LAYOUT_POLICY.platform_key:
        return
    for relative_path in macos_known_executable_staging_paths(layout_policy):
        path = app_root / relative_path
        if not path.exists():
            continue
        if not path.is_file():
            raise ValueError(f"release_zip_executable_not_file:{relative_path}")
        if os.name != "nt" and not has_posix_executable_bit(path):
            raise ValueError(f"release_zip_executable_bit_missing:{relative_path}")
        if not is_macho_arm64(_read_file_prefix(path)):
            raise ValueError(f"release_zip_macho_arm64_missing:{relative_path}")


def _prepare_output_paths(zip_path: Path, sha256_path: Path, *, force: bool) -> None:
    """準備輸出位置；未指定 force 時避免覆蓋既有 artifact。"""

    existing = [path for path in (zip_path, sha256_path) if path.exists()]
    if existing and not force:
        existing_text = ", ".join(str(path) for path in existing)
        raise ValueError(f"release_zip_output_exists:{existing_text}")
    for path in existing:
        path.unlink()


def _write_release_zip(
    app_root: Path,
    zip_path: Path,
    *,
    executable_paths: frozenset[str],
    extra_text_files: tuple[ReleaseZipTextFile, ...] = (),
) -> None:
    """寫出共用 release zip，保留 POSIX mode 與 symlink metadata。"""

    extra_relative_paths = {
        _normalize_extra_text_file_path(extra.relative_path)
        for extra in extra_text_files
    }
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        strict_timestamps=False,
    ) as archive:
        for path in sorted(app_root.rglob("*"), key=lambda item: item.as_posix()):
            relative_path = path.relative_to(app_root)
            relative_posix = relative_path.as_posix()
            if relative_posix in extra_relative_paths:
                continue
            arcname = _archive_name(relative_path)
            if path.is_symlink():
                _write_symlink(archive, path, arcname)
                continue
            info = zipfile.ZipInfo.from_file(path, arcname)
            if path.is_file():
                if relative_posix in executable_paths:
                    _ensure_zip_executable(info)
                info.compress_type = zipfile.ZIP_DEFLATED
                with path.open("rb") as file:
                    archive.writestr(info, file.read())
            else:
                if not info.filename.endswith("/"):
                    info.filename += "/"
                archive.writestr(info, b"")
        for extra in extra_text_files:
            relative_posix = _normalize_extra_text_file_path(extra.relative_path)
            _write_extra_text_file(
                archive,
                _archive_name(Path(relative_posix)),
                extra.content,
            )


def _normalize_extra_text_file_path(path: str) -> str:
    """確認額外文字檔只能寫進 release root 內的相對路徑。"""

    pure_path = PurePosixPath(path)
    if pure_path.is_absolute() or not pure_path.parts:
        raise ValueError(f"release_zip_extra_file_path_invalid:{path}")
    if any(part in {"", ".", ".."} for part in pure_path.parts):
        raise ValueError(f"release_zip_extra_file_path_invalid:{path}")
    return pure_path.as_posix()


def _write_extra_text_file(
    archive: zipfile.ZipFile,
    arcname: str,
    content: str,
) -> None:
    """寫入 release zip 內的 UTF-8 使用者文字說明。"""

    info = zipfile.ZipInfo(arcname)
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, content.encode("utf-8"))


def _archive_name(relative_path: Path) -> str:
    """將平台路徑轉成 release zip 內的 POSIX root path。"""

    return PurePosixPath(ZIP_ROOT_NAME, *relative_path.parts).as_posix()


def _write_symlink(archive: zipfile.ZipFile, path: Path, arcname: str) -> None:
    """以 POSIX symlink metadata 寫入 zip member。"""

    info = zipfile.ZipInfo(arcname)
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive.writestr(info, path.readlink().as_posix())


def _ensure_zip_executable(info: zipfile.ZipInfo) -> None:
    """將已知 macOS executable 的 POSIX executable bit 寫進 zip metadata。"""

    mode = info.external_attr >> 16
    file_type = mode & 0o170000 or stat.S_IFREG
    permission_bits = (mode & 0o777) | 0o755
    info.external_attr = ((file_type | permission_bits) << 16) | (
        info.external_attr & 0xFFFF
    )


def _write_sha256_file(path: Path, *, digest: str, zip_name: str) -> None:
    """寫出 updater 期待的 `.sha256` 格式。"""

    path.write_text(render_sha256_sidecar(digest, zip_name), encoding="ascii")


def _read_file_prefix(path: Path, *, size: int = 4096) -> bytes:
    """讀取 Mach-O 判斷所需的檔案前段。"""

    with path.open("rb") as file:
        return file.read(size)


def main() -> int:
    """CLI entrypoint。"""

    args = parse_args()
    result = create_release_zip(
        platform_name=str(args.platform),
        version=str(args.version),
        dist_dir=args.dist_dir,
        app_root=args.app_root,
        output=args.output,
        force=bool(args.force),
    )
    print(f"created: {result.zip_path}")
    print(f"sha256: {result.sha256_path}")
    print(result.sha256)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
