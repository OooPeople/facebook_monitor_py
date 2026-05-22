"""Admin tool：使用 Ed25519 私鑰簽署 release manifest。"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


PRIVATE_KEY_ENV = "FACEBOOK_MONITOR_RELEASE_PRIVATE_KEY_B64"


def parse_args() -> argparse.Namespace:
    """解析 CLI 參數。"""

    parser = argparse.ArgumentParser(description="Sign updater manifest with Ed25519.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--private-key-file", type=Path)
    parser.add_argument("--private-key-b64", default="")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def sign_release_manifest(
    *,
    manifest_path: Path,
    private_key_b64: str = "",
    private_key_file: Path | None = None,
    output: Path | None = None,
    force: bool = False,
) -> Path:
    """簽署 manifest，輸出 base64 detached signature。"""

    if not manifest_path.is_file():
        raise ValueError(f"manifest_missing:{manifest_path}")
    signature_path = output or manifest_path.with_name(manifest_path.name + ".sig")
    if signature_path.exists() and not force:
        raise ValueError(f"manifest_signature_output_exists:{signature_path}")
    private_key = Ed25519PrivateKey.from_private_bytes(
        _read_private_key_bytes(
            private_key_b64=private_key_b64,
            private_key_file=private_key_file,
        )
    )
    signature = private_key.sign(manifest_path.read_bytes())
    signature_path.parent.mkdir(parents=True, exist_ok=True)
    signature_path.write_text(
        base64.b64encode(signature).decode("ascii") + "\n",
        encoding="ascii",
    )
    return signature_path


def _read_private_key_bytes(
    *,
    private_key_b64: str = "",
    private_key_file: Path | None = None,
) -> bytes:
    """從 CLI、檔案或環境變數讀取 base64 raw Ed25519 private key。"""

    value = private_key_b64.strip()
    if not value and private_key_file is not None:
        value = private_key_file.read_text(encoding="utf-8").strip()
    if not value:
        value = os.environ.get(PRIVATE_KEY_ENV, "").strip()
    if not value:
        raise ValueError("manifest_private_key_missing")
    try:
        key_bytes = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ValueError("manifest_private_key_invalid") from exc
    if len(key_bytes) != 32:
        raise ValueError("manifest_private_key_invalid")
    return key_bytes


def main() -> int:
    """CLI entrypoint。"""

    args = parse_args()
    try:
        path = sign_release_manifest(
            manifest_path=args.manifest.resolve(),
            private_key_b64=str(args.private_key_b64),
            private_key_file=args.private_key_file,
            output=args.output,
            force=bool(args.force),
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
