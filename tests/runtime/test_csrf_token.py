"""Runtime CSRF token storage tests。"""

from __future__ import annotations

import os

import pytest

from facebook_monitor.runtime.csrf_token import CSRF_TOKEN_FILE_NAME
from facebook_monitor.runtime.csrf_token import load_or_create_csrf_token


def test_load_or_create_csrf_token_is_stable_per_runtime_dir(tmp_path) -> None:
    """同一 runtime dir 重啟後沿用 token，避免舊 dashboard 分頁首個 POST 失敗。"""

    runtime_dir = tmp_path / "runtime"

    first_token = load_or_create_csrf_token(runtime_dir)
    second_token = load_or_create_csrf_token(runtime_dir)

    assert len(first_token) >= 32
    assert second_token == first_token
    assert (runtime_dir / CSRF_TOKEN_FILE_NAME).read_text(encoding="utf-8").strip() == first_token


def test_load_or_create_csrf_token_replaces_invalid_short_token(tmp_path) -> None:
    """異常短 token 不沿用，避免手動破壞檔案後讓 CSRF 保護失效。"""

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / CSRF_TOKEN_FILE_NAME).write_text("short\n", encoding="utf-8")

    token = load_or_create_csrf_token(runtime_dir)

    assert token != "short"
    assert len(token) >= 32


def test_load_or_create_csrf_token_writes_private_file_when_supported(tmp_path) -> None:
    """POSIX 平台上新建 token file 應只允許 owner 讀寫。"""

    runtime_dir = tmp_path / "runtime"

    load_or_create_csrf_token(runtime_dir)

    if os.name != "nt":
        mode = (runtime_dir / CSRF_TOKEN_FILE_NAME).stat().st_mode & 0o777
        assert mode == 0o600


def test_load_or_create_csrf_token_rejects_token_file_symlink(tmp_path) -> None:
    """既有 token path 若是 symlink，不可跟隨讀取或覆寫。"""

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    target = tmp_path / "outside-token.txt"
    target.write_text("x" * 64, encoding="utf-8")
    token_path = runtime_dir / CSRF_TOKEN_FILE_NAME
    try:
        token_path.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink not supported: {exc}")

    with pytest.raises(ValueError, match="csrf_token_file_unsafe"):
        load_or_create_csrf_token(runtime_dir)
