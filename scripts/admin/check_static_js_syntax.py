"""檢查 Web UI static JavaScript 語法。

職責：提供 release validation 可跨平台呼叫的 Node syntax check，
避免 Windows PowerShell pipeline 成為唯一可重現驗證方式。
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = ROOT / "src" / "facebook_monitor" / "webapp" / "static"


def iter_static_js_files() -> list[Path]:
    """列出正式 Web UI static JavaScript 檔案。"""

    return sorted(STATIC_ROOT.rglob("*.js"))


def main() -> int:
    """逐一執行 node --check，回傳第一個失敗狀態。"""

    node = shutil.which("node")
    if node is None:
        print("node executable not found; static JS syntax check cannot run", file=sys.stderr)
        return 1

    for path in iter_static_js_files():
        completed = subprocess.run(
            [node, "--check", str(path)],
            cwd=ROOT,
            check=False,
        )
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
