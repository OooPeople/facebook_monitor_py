# macOS Apple Silicon Packaging Handoff

本文件是可追蹤的 macOS Apple Silicon 打包接手文件，供 Mac 端 Codex 或人工測試使用。它記錄目前 macOS frozen artifact 的下一步，不代表 macOS 自動套用 updater 已完成。

## Scope

- 目前只支援 macOS Apple Silicon / arm64。
- Intel Mac 不列入目前打包與 updater 範圍。
- macOS Web UI 目前只支援「檢查、下載、SHA256 驗證」。
- 在真實 macOS frozen updater smoke 通過前，不得把 macOS `apply_supported` 改成 `True`，也不得宣稱 macOS 自動套用 updater 已完成。

## Current State

已完成程式碼層 groundwork：

- `packaging/pyinstaller/facebook_monitor_macos.spec`
- `src/facebook_monitor/updates/artifacts.py`
- `src/facebook_monitor/updates/platforms.py`
- macOS artifact validation：`scripts/admin/release_artifact_validation.py --platform macos-arm64`
- settings download-only UI
- macOS apply / launcher policy 單元測試

Mac 端已知狀態：

- 使用者已在 Mac 上跑過 `uv run playwright install chromium` 與 `uv run facebook-monitor`。
- Facebook login 視窗可開。
- PyInstaller build 曾失敗於：

```text
No Playwright macOS Chromium folder found. Run playwright install chromium or set FACEBOOK_MONITOR_BUNDLED_CHROMIUM_DIR.
```

推測原因：

- `facebook_monitor_macos.spec` 目前只找 `~/Library/Caches/ms-playwright/chromium-*/chrome-mac/Chromium.app`。
- Apple Silicon Playwright 可能使用 `chrome-mac-arm64/Chromium.app` 或其他資料夾。

## First Diagnostics On Mac

在 repo 根目錄先跑：

```bash
pwd
git branch --show-current
git status --short
find ~/Library/Caches/ms-playwright -path "*/Chromium.app/Contents/MacOS/Chromium" -type f
find ~/Library/Caches/ms-playwright -maxdepth 5 -name "Chromium.app" -print
```

## Suggested Fix

更新 `packaging/pyinstaller/facebook_monitor_macos.spec` 的 `bundled_chromium_dir()`：

- 除了 `chromium-*/chrome-mac`，也搜尋所有符合 `chromium-*/*/Chromium.app/Contents/MacOS/Chromium` 的候選。
- 或至少加入 `chrome-mac-arm64`。
- `FACEBOOK_MONITOR_BUNDLED_CHROMIUM_DIR` 覆寫仍應接受「包含 `Chromium.app` 的資料夾」，不要接受 executable 本身。

修完後先跑：

```bash
uv run python -m py_compile packaging/pyinstaller/facebook_monitor_macos.spec
uv run ruff check packaging/pyinstaller/facebook_monitor_macos.spec
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

version = "0.2.0"
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
ditto -x -k dist/facebook-monitor-0.2.0-macos-arm64-onedir.zip ~/dev/fb-monitor-frozen-test
cd ~/dev/fb-monitor-frozen-test/facebook-monitor
./facebook-monitor --data-dir ~/dev/fb-monitor-frozen-test/data
```

若 Gatekeeper / quarantine 擋住，測試用可先跑：

```bash
xattr -dr com.apple.quarantine ~/dev/fb-monitor-frozen-test/facebook-monitor
./facebook-monitor --data-dir ~/dev/fb-monitor-frozen-test/data
```

Smoke 至少確認：

- Web UI 可開，`/health` 正常。
- static assets 正常。
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
