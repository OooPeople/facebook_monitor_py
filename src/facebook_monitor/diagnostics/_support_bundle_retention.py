"""Support bundle retention cleanup。

職責：刪除過期或超出數量上限的 support bundle zip。建立 support bundle
的流程可直接呼叫此模組，避免 retention 規則散在 zip writer 內。
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
import logging
import os
from pathlib import Path

from facebook_monitor.diagnostics._support_bundle_constants import SUPPORT_BUNDLE_FILENAME_PREFIX
from facebook_monitor.diagnostics._support_bundle_constants import SUPPORT_BUNDLE_FILENAME_SUFFIX
from facebook_monitor.updates.validation import is_reparse_or_symlink

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SupportBundleCandidate:
    """保存可被 retention 清理的 support bundle 檔案。"""

    path: Path
    mtime: float
    sort_key: tuple[float, str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sort_key", (self.mtime, self.path.name))


def prune_old_support_bundles(
    bundle_dir: Path,
    *,
    max_age_days: int,
    max_files: int,
    now: datetime | None = None,
    preserve: tuple[Path, ...] = (),
) -> int:
    """清理舊 support bundle；只處理 allowlisted zip 檔名。"""

    current_time = now or datetime.now(timezone.utc)
    candidates = _list_support_bundle_candidates(bundle_dir)
    candidates.sort(key=lambda candidate: candidate.sort_key, reverse=True)
    preserved_paths = {_support_bundle_path_identity(path) for path in preserve}
    keep_count = max(1, int(max_files))
    keep_paths = {
        _support_bundle_path_identity(candidate.path)
        for candidate in candidates[:keep_count]
    }
    cutoff = current_time.timestamp() - max(0, int(max_age_days)) * 86400
    deleted_count = 0
    for candidate in candidates:
        path = candidate.path
        path_identity = _support_bundle_path_identity(path)
        if path_identity in preserved_paths:
            continue
        should_delete_for_count = path_identity not in keep_paths
        should_delete_for_age = candidate.mtime < cutoff
        if not (should_delete_for_count or should_delete_for_age):
            continue
        try:
            path.unlink()
            deleted_count += 1
        except OSError:
            logger.warning("Failed to prune old support bundle: %s", path, exc_info=True)
    return deleted_count


def _list_support_bundle_candidates(bundle_dir: Path) -> list[_SupportBundleCandidate]:
    """列出符合 support bundle retention 規則的 regular files。"""

    candidates: list[_SupportBundleCandidate] = []
    for path in bundle_dir.glob(
        f"{SUPPORT_BUNDLE_FILENAME_PREFIX}*{SUPPORT_BUNDLE_FILENAME_SUFFIX}"
    ):
        try:
            if is_reparse_or_symlink(path) or not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        candidates.append(_SupportBundleCandidate(path=path, mtime=stat.st_mtime))
    return candidates


def _support_bundle_path_identity(path: Path) -> str:
    """取得可比對的 path identity；resolve 失敗時退回 absolute path。"""

    try:
        return os.path.normcase(str(path.resolve()))
    except OSError:
        return os.path.normcase(str(path.absolute()))
