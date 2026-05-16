"""Web UI CSRF token runtime storage。

職責：讓同一份 data-dir 的本機 Web UI 重啟後沿用 CSRF token，
避免瀏覽器舊分頁第一次送出表單時被新 process 的隨機 token 誤擋。
"""

from __future__ import annotations

from pathlib import Path
from secrets import token_urlsafe


CSRF_TOKEN_FILE_NAME = "csrf_token.txt"
MIN_CSRF_TOKEN_LENGTH = 32


def load_or_create_csrf_token(runtime_dir: Path) -> str:
    """讀取或建立此 runtime dir 專用的 Web UI CSRF token。"""

    runtime_dir.mkdir(parents=True, exist_ok=True)
    token_path = runtime_dir / CSRF_TOKEN_FILE_NAME
    existing_token = _read_existing_token(token_path)
    if existing_token:
        return existing_token
    token = token_urlsafe(32)
    token_path.write_text(token + "\n", encoding="utf-8")
    return token


def _read_existing_token(token_path: Path) -> str | None:
    """讀取已存在 token；空值或異常短 token 視為無效。"""

    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if len(token) < MIN_CSRF_TOKEN_LENGTH:
        return None
    return token
