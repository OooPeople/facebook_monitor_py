"""Release/update checksum 共用 helper。

職責：集中 SHA256 計算、`.sha256` sidecar 內容產生與解析規則，讓下載、
打包與 release validation 使用同一套格式。
"""

from __future__ import annotations

from pathlib import Path
import re


HASH_CHUNK_SIZE = 1024 * 1024


def calculate_sha256(path: Path, *, chunk_size: int = HASH_CHUNK_SIZE) -> str:
    """計算檔案 SHA256。"""

    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_sha256_sidecar(digest: str, filename: str) -> str:
    """產生 updater 與 GitHub Release 使用的 `.sha256` 內容。"""

    normalized_digest = digest.strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized_digest):
        raise ValueError("sha256_digest_invalid")
    if Path(filename).name != filename or not filename:
        raise ValueError("sha256_filename_invalid")
    return f"{normalized_digest}  {filename}\n"


def read_sha256_sidecar(path: Path, *, expected_filename: str) -> str:
    """讀取 `.sha256` 檔案，支援常見 `hash  filename` 格式。"""

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("sha256_file_empty")
    first_line = text.splitlines()[0].strip()
    candidate = first_line.split()[0].casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", candidate):
        raise ValueError("sha256_file_invalid")
    parts = first_line.split(maxsplit=1)
    if len(parts) == 2:
        filename = parts[1].lstrip("*").strip()
        if filename and Path(filename).name != expected_filename:
            raise ValueError("sha256_filename_mismatch")
    return candidate
