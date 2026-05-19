# macOS Apple Silicon Packaging Handoff

本文件是可追蹤的 macOS Apple Silicon 打包接手文件，供 Mac 端 Codex 或人工測試使用。它記錄目前 macOS frozen artifact 的狀態與下一步。

## Scope

- 目前只支援 macOS Apple Silicon / arm64。
- Intel Mac 不列入目前打包與 updater 範圍。
- onedir 內包含 `Facebook Monitor.app` Finder / Dock native launcher；使用者應從這個 `.app` 啟動，避免 Finder 直接執行 Unix executable 時跳出 Terminal，並讓 Dock item 在主程式執行期間持續存在。
- macOS Web UI 支援「檢查、下載、SHA256 驗證、handoff、temp updater 套用」。
- 尚未做 Developer ID signing / notarization。

## Current State

已完成：

- `packaging/pyinstaller/facebook_monitor_macos.spec`
- `src/facebook_monitor/updates/artifacts.py`
- `src/facebook_monitor/updates/platforms.py`
- macOS artifact validation：`scripts/admin/release_artifact_validation.py --platform macos-arm64`
- settings macOS download-and-apply UI
- macOS apply / launcher policy 單元測試
- macOS PyInstaller build 可收進 Playwright Apple Silicon `Google Chrome for Testing.app`
- PyInstaller macOS build 會從 `packaging/assets/facebook-monitor.png` 產生 `.app` Dock icon，並編譯 native launcher 作為 Dock 母程序
- frozen updater smoke 可替換 app files、保留 data/profile、清除 handoff/zip，並保留 executable bit

Mac 端已知狀態：

- 使用者已在 Mac 上跑過 `uv run playwright install chromium` 與 `uv run facebook-monitor`。
- Facebook login 視窗可開。
- 目前 Mac 實測 Playwright cache 形狀是 `chromium-*/chrome-mac-arm64/Google Chrome for Testing.app`。
- PyInstaller build 已通過，產物在 `dist/facebook-monitor`。
- release zip 與 `.sha256` 已可由 validation 通過。

## Diagnostics On Mac

在 repo 根目錄先跑：

```bash
pwd
git branch --show-current
git status --short
find ~/Library/Caches/ms-playwright -path "*/Chromium.app/Contents/MacOS/Chromium" -type f
find ~/Library/Caches/ms-playwright -path "*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing" -type f
find ~/Library/Caches/ms-playwright -maxdepth 5 -name "Chromium.app" -print
find ~/Library/Caches/ms-playwright -maxdepth 5 -name "Google Chrome for Testing.app" -print
```

## Focused Verification

```bash
uv run python -m py_compile packaging/pyinstaller/facebook_monitor_macos.spec
uv run ruff check packaging/pyinstaller/facebook_monitor_macos.spec
uv run pytest tests/automation/test_browser_runtime.py tests/updates/test_apply.py tests/admin/test_release_artifact_validation.py tests/admin/test_smoke_frozen_updater.py tests/webapp/test_app.py -q
uv run python scripts/admin/smoke_frozen_updater.py --built-app dist/facebook-monitor
```

## Build Commands

```bash
uv sync
uv run mypy
uv run pytest -q
uv run ruff check src scripts tests
uv run python -m compileall -q src scripts tests
uv run python -m pip install pyinstaller
uv run playwright install chromium
export FACEBOOK_MONITOR_BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export FACEBOOK_MONITOR_GIT_COMMIT="$(git rev-parse --short=12 HEAD)"
uv run python -m PyInstaller packaging/pyinstaller/facebook_monitor_macos.spec --clean --noconfirm
```

## Create ZIP And SHA256

```bash
python - <<'PY'
from pathlib import Path
import hashlib
import zipfile

version = "0.3.1"
arch = "arm64"
dist = Path("dist")
source = dist / "facebook-monitor"
zip_path = dist / f"facebook-monitor-{version}-macos-{arch}-onedir.zip"

if zip_path.exists():
    zip_path.unlink()

with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in source.rglob("*"):
        arcname = path.relative_to(dist).as_posix()
        info = zipfile.ZipInfo.from_file(path, arcname)
        if path.is_file():
            info.compress_type = zipfile.ZIP_DEFLATED
            with path.open("rb") as file:
                archive.writestr(info, file.read())
        else:
            archive.writestr(info, b"")

digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
zip_path.with_name(zip_path.name + ".sha256").write_text(
    f"{digest}  {zip_path.name}\n",
    encoding="ascii",
)
print(zip_path)
print(digest)
PY
```

## Validate Artifact

```bash
uv run python scripts/admin/release_artifact_validation.py --platform macos-arm64
```

## Frozen Web UI Smoke

```bash
rm -rf ~/dev/fb-monitor-frozen-test
mkdir -p ~/dev/fb-monitor-frozen-test
ditto -x -k dist/facebook-monitor-0.3.1-macos-arm64-onedir.zip ~/dev/fb-monitor-frozen-test
cd ~/dev/fb-monitor-frozen-test/facebook-monitor
open "Facebook Monitor.app" --args --data-dir ~/dev/fb-monitor-frozen-test/data
```

若 Gatekeeper / quarantine 擋住，測試用可先跑：

```bash
xattr -dr com.apple.quarantine ~/dev/fb-monitor-frozen-test/facebook-monitor
open "Facebook Monitor.app" --args --data-dir ~/dev/fb-monitor-frozen-test/data
```

Smoke 至少確認：

- Web UI 可開，`/health` 正常。
- static assets 正常。
- Finder 開啟 `Facebook Monitor.app` 不跳 Terminal，執行期間持續顯示在 Dock，從 Dock Quit 可關閉主程式。
- `~/dev/fb-monitor-frozen-test/data/logs/startup.log` 與 `app.log` 無 fatal error。
- bundled Chromium 可開 Facebook login/profile 視窗。
- posts/comments target 至少做一個基本 metadata refresh 或 scan smoke。
- `scripts/admin/release_artifact_validation.py --platform macos-arm64` 通過。

如果失敗，回報：

```bash
cat ~/dev/fb-monitor-frozen-test/data/logs/startup.log
cat ~/dev/fb-monitor-frozen-test/data/logs/app.log
```

同時附上 PyInstaller build log 末段與 `find ~/Library/Caches/ms-playwright ...` 的輸出。
