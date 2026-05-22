"""Admin tool：建立 signed updater manifest 的 JSON payload。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.updates.artifacts import sanitize_release_asset_name
from facebook_monitor.updates.checksum import calculate_sha256
from facebook_monitor.updates.manifest import release_manifest_asset_name
from facebook_monitor.updates.release_check import DEFAULT_UPDATE_REPOSITORY
from facebook_monitor.version import APP_VERSION


def parse_args() -> argparse.Namespace:
    """解析 CLI 參數。"""

    parser = argparse.ArgumentParser(description="Create a signed updater manifest JSON.")
    parser.add_argument("--version", default=APP_VERSION)
    parser.add_argument("--repository", default=DEFAULT_UPDATE_REPOSITORY)
    parser.add_argument("--key-id", required=True)
    parser.add_argument(
        "--asset",
        action="append",
        required=True,
        metavar="PLATFORM=PATH",
        help="Release asset mapping, for example windows=dist/app.zip.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def create_release_manifest(
    *,
    version: str,
    repository: str,
    key_id: str,
    asset_specs: list[str],
    output: Path | None = None,
    force: bool = False,
) -> Path:
    """建立 release manifest JSON 並回傳輸出路徑。"""

    manifest_path = output or Path.cwd() / release_manifest_asset_name(version)
    if manifest_path.exists() and not force:
        raise ValueError(f"manifest_output_exists:{manifest_path}")
    assets = [_manifest_asset_from_spec(spec) for spec in asset_specs]
    payload = {
        "schema_version": 1,
        "version": version,
        "repository": repository,
        "key_id": key_id,
        "assets": assets,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _manifest_asset_from_spec(spec: str) -> dict[str, object]:
    """解析 `platform=path` manifest asset spec。"""

    platform, separator, raw_path = spec.partition("=")
    if not separator or not platform.strip() or not raw_path.strip():
        raise ValueError("manifest_asset_spec_invalid")
    asset_path = Path(raw_path).resolve()
    if not asset_path.is_file():
        raise ValueError(f"manifest_asset_missing:{asset_path}")
    return {
        "name": sanitize_release_asset_name(asset_path.name),
        "platform": platform.strip(),
        "sha256": calculate_sha256(asset_path),
        "size": asset_path.stat().st_size,
    }


def main() -> int:
    """CLI entrypoint。"""

    args = parse_args()
    try:
        path = create_release_manifest(
            version=str(args.version),
            repository=str(args.repository),
            key_id=str(args.key_id),
            asset_specs=list(args.asset),
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
