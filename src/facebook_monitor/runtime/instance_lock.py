"""Application-level single-instance lock。

職責：避免同一份 local app runtime data 被多個 Web UI / scheduler process
同時使用，並記錄既有 server 位置供第二次啟動提示或開啟。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import BinaryIO

if os.name == "nt":
    import msvcrt
    _fcntl: object | None = None
else:
    import fcntl as _fcntl_module

    _fcntl = _fcntl_module


LOCK_FILE_NAME = "app.lock"
SERVER_INFO_FILE_NAME = "server.json"
LOCK_BYTE_COUNT = 1


class AppInstanceLockError(RuntimeError):
    """表示目前 runtime dir 已有另一個 app instance 持有 lock。"""

    def __init__(self, message: str, *, server_info: "ServerInfo | None" = None) -> None:
        super().__init__(message)
        self.server_info = server_info


@dataclass(frozen=True)
class ServerInfo:
    """保存目前 local Web UI server 資訊。"""

    pid: int
    host: str
    port: int
    url: str
    started_at: str


@dataclass(frozen=True)
class AppInstanceLock:
    """保存目前持有的 app instance lock。"""

    runtime_dir: Path
    lock_path: Path
    server_info_path: Path
    owner: str

    def write_server_info(self, *, host: str, port: int, url: str) -> ServerInfo:
        """寫入目前 server info，供第二次啟動讀取。"""

        info = ServerInfo(
            pid=os.getpid(),
            host=host,
            port=port,
            url=url,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self.server_info_path.write_text(
            json.dumps(asdict(info), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return info

    def clear_server_info(self) -> None:
        """移除目前 server info；檔案不存在時忽略。"""

        try:
            self.server_info_path.unlink()
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class ResourceLock:
    """保存單一實際資源 lock。"""

    kind: str
    resource_path: Path
    lock_dir: Path
    lock_path: Path


@dataclass(frozen=True)
class ResourceIdentityLock:
    """保存 DB 與 profile 實際資源 identity locks。"""

    db_path: Path
    profile_dir: Path
    owner: str
    locks: tuple[ResourceLock, ...]

    @property
    def lock_paths(self) -> tuple[Path, ...]:
        """回傳目前持有的 resource lock 檔案路徑。"""

        return tuple(lock.lock_path for lock in self.locks)


@contextmanager
def acquire_app_instance_lock(runtime_dir: Path, owner: str) -> Iterator[AppInstanceLock]:
    """取得 app-level single-instance lock，離開 context 時自動釋放。"""

    resolved_runtime_dir = runtime_dir.resolve()
    resolved_runtime_dir.mkdir(parents=True, exist_ok=True)
    lock_path = resolved_runtime_dir / LOCK_FILE_NAME
    server_info_path = resolved_runtime_dir / SERVER_INFO_FILE_NAME
    lock_file: BinaryIO | None = None
    locked = False
    try:
        lock_file = lock_path.open("a+b")
        _lock_file(lock_file, lock_path, server_info_path, owner)
        _write_lock_owner_info(
            lock_file,
            owner=owner,
            resource_kind="runtime",
            resource_path=resolved_runtime_dir,
        )
        locked = True
        yield AppInstanceLock(
            runtime_dir=resolved_runtime_dir,
            lock_path=lock_path,
            server_info_path=server_info_path,
            owner=owner,
        )
    finally:
        if lock_file is not None:
            if locked:
                _unlock_file(lock_file)
            lock_file.close()


@contextmanager
def acquire_resource_identity_lock(
    *,
    db_path: Path,
    profile_dir: Path,
    owner: str,
) -> Iterator[ResourceIdentityLock]:
    """分別鎖住 resolved DB 與 profile，避免不同 data-dir 共用任一資源。"""

    resolved_db_path = db_path.expanduser().resolve()
    resolved_profile_dir = profile_dir.expanduser().resolve()
    lock_files: list[BinaryIO] = []
    locks: list[ResourceLock] = []
    try:
        for kind, resource_path in (
            ("db", resolved_db_path),
            ("profile", resolved_profile_dir),
        ):
            lock_dir = _resource_lock_dir(kind, resource_path)
            lock_dir.mkdir(parents=True, exist_ok=True)
            lock_path = lock_dir / LOCK_FILE_NAME
            lock_file: BinaryIO = lock_path.open("a+b")
            try:
                _lock_resource_file(
                    lock_file,
                    lock_path,
                    owner=owner,
                    kind=kind,
                    resource_path=resource_path,
                )
                _write_lock_owner_info(
                    lock_file,
                    owner=owner,
                    resource_kind=kind,
                    resource_path=resource_path,
                )
            except Exception:
                lock_file.close()
                raise
            lock_files.append(lock_file)
            locks.append(
                ResourceLock(
                    kind=kind,
                    resource_path=resource_path,
                    lock_dir=lock_dir,
                    lock_path=lock_path,
                )
            )
        yield ResourceIdentityLock(
            db_path=resolved_db_path,
            profile_dir=resolved_profile_dir,
            owner=owner,
            locks=tuple(locks),
        )
    finally:
        for lock_file in reversed(lock_files):
            _unlock_file(lock_file)
            lock_file.close()


def read_server_info(runtime_dir: Path) -> ServerInfo | None:
    """讀取 runtime/server.json；不存在或格式錯誤時回傳 None。"""

    path = runtime_dir.resolve() / SERVER_INFO_FILE_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ServerInfo(
            pid=int(payload["pid"]),
            host=str(payload["host"]),
            port=int(payload["port"]),
            url=str(payload["url"]),
            started_at=str(payload["started_at"]),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _resource_lock_dir(kind: str, resource_path: Path) -> Path:
    """將單一實際資源路徑轉成全域 lock 目錄。"""

    identity = json.dumps(
        {"kind": kind, "path": _canonical_resource_identity_path(resource_path)},
        sort_keys=True,
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return Path(tempfile.gettempdir()) / "facebook-monitor" / "resource-locks" / kind / digest


def _canonical_resource_identity_path(path: Path) -> str:
    """回傳 resource lock hash 使用的 canonical path 字串。"""

    resolved = str(path.expanduser().resolve())
    if os.name == "nt":
        return os.path.normcase(resolved)
    return resolved


def _lock_file(
    lock_file: BinaryIO,
    lock_path: Path,
    server_info_path: Path,
    owner: str,
) -> None:
    """取得跨 process lock；失敗時附上既有 server info。"""

    lock_file.seek(0)
    try:
        if os.name == "nt":
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, LOCK_BYTE_COUNT)
        else:
            _posix_flock(lock_file.fileno(), _posix_lock_exclusive_nonblocking_flags())
    except OSError as exc:
        server_info = read_server_info(server_info_path.parent)
        raise AppInstanceLockError(
            f"Facebook Monitor 已在使用此 runtime dir: {lock_path.parent} (request={owner})",
            server_info=server_info,
        ) from exc


def _lock_resource_file(
    lock_file: BinaryIO,
    lock_path: Path,
    *,
    owner: str,
    kind: str,
    resource_path: Path,
) -> None:
    """取得單一 resource identity lock；失敗時直接指出衝突資源。"""

    lock_file.seek(0)
    try:
        if os.name == "nt":
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, LOCK_BYTE_COUNT)
        else:
            _posix_flock(lock_file.fileno(), _posix_lock_exclusive_nonblocking_flags())
    except OSError as exc:
        resource_label = "SQLite DB" if kind == "db" else "browser profile"
        raise AppInstanceLockError(
            f"Facebook Monitor 已有另一個 process 使用相同 {resource_label}: "
            f"{resource_path} (lock={lock_path}, request={owner})"
        ) from exc


def _write_lock_owner_info(
    lock_file: BinaryIO,
    *,
    owner: str,
    resource_kind: str,
    resource_path: Path,
) -> None:
    """寫入目前 lock 持有者資訊，供衝突排查。"""

    payload = {
        "pid": os.getpid(),
        "owner": owner,
        "resource_kind": resource_kind,
        "resource_path": str(resource_path),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    lock_file.flush()


def _unlock_file(lock_file: BinaryIO) -> None:
    """釋放跨 process lock。"""

    lock_file.seek(0)
    if os.name == "nt":
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, LOCK_BYTE_COUNT)
    else:
        _posix_flock(lock_file.fileno(), _posix_unlock_flags())


def _posix_flock(file_descriptor: int, flags: int) -> None:
    """呼叫 POSIX flock；Windows 分支不應進入此函式。"""

    if _fcntl is None:
        raise RuntimeError("fcntl is not available on this platform")
    flock = getattr(_fcntl, "flock")
    flock(file_descriptor, flags)


def _posix_lock_exclusive_nonblocking_flags() -> int:
    """回傳 POSIX exclusive non-blocking lock flags。"""

    if _fcntl is None:
        raise RuntimeError("fcntl is not available on this platform")
    return int(getattr(_fcntl, "LOCK_EX")) | int(getattr(_fcntl, "LOCK_NB"))


def _posix_unlock_flags() -> int:
    """回傳 POSIX unlock flags。"""

    if _fcntl is None:
        raise RuntimeError("fcntl is not available on this platform")
    return int(getattr(_fcntl, "LOCK_UN"))
