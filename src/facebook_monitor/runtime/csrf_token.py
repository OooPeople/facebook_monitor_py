"""Web UI CSRF token runtime storage。

職責：讓同一份 data-dir 的本機 Web UI 重啟後沿用 CSRF token，
避免瀏覽器舊分頁第一次送出表單時被新 process 的隨機 token 誤擋。
"""

from __future__ import annotations

import stat
from pathlib import Path
from secrets import token_urlsafe
import uuid


CSRF_TOKEN_FILE_NAME = "csrf_token.txt"
MIN_CSRF_TOKEN_LENGTH = 32


def load_or_create_csrf_token(runtime_dir: Path) -> str:
    """讀取或建立此 runtime dir 專用的 Web UI CSRF token。"""

    runtime_dir.mkdir(parents=True, exist_ok=True)
    if _is_reparse_or_symlink(runtime_dir):
        raise ValueError("csrf_token_dir_unsafe")
    token_path = runtime_dir / CSRF_TOKEN_FILE_NAME
    existing_token = _read_existing_token(token_path)
    if existing_token:
        return existing_token
    token = token_urlsafe(32)
    _write_token_atomic(token_path, token)
    return token


def _read_existing_token(token_path: Path) -> str | None:
    """讀取已存在 token；空值或異常短 token 視為無效。"""

    try:
        if _is_reparse_or_symlink(token_path):
            raise ValueError("csrf_token_file_unsafe")
        token = token_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if len(token) < MIN_CSRF_TOKEN_LENGTH:
        return None
    return token


def _write_token_atomic(token_path: Path, token: str) -> None:
    """以同目錄 atomic replace 寫入 CSRF token，並盡量限制檔案權限。"""

    if _is_reparse_or_symlink(token_path.parent):
        raise ValueError("csrf_token_dir_unsafe")
    tmp_path = token_path.with_name(f".{token_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("x", encoding="utf-8") as file:
            file.write(token + "\n")
        try:
            tmp_path.chmod(0o600)
        except OSError:
            pass
        tmp_path.replace(token_path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _is_reparse_or_symlink(path: Path) -> bool:
    """判斷 path 本身是否為 symlink 或 Windows reparse point。"""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)
