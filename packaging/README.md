# Packaging

本文件記錄 Windows EXE 打包方式、macOS Apple Silicon onedir 打包路徑、發佈包內容與 frozen smoke checklist。正式產品入口仍是 `facebook-monitor`，打包不得繞過 `src/facebook_monitor/launcher.py`。

## 目前設計

- Windows PyInstaller spec：`packaging/pyinstaller/facebook_monitor.spec`。
- macOS PyInstaller spec：`packaging/pyinstaller/facebook_monitor_macos.spec`。
- Windows 與 macOS onedir build 都輸出到 `dist/facebook-monitor/`（Windows 路徑顯示為 `dist\facebook-monitor\`）。
- EXE 使用正式 launcher entrypoint，保留 instance lock、profile gate、runtime reset、loopback-only 與 CSRF 保護。
- Web templates/static、Playwright Python driver data 與 Chromium browser binary 都會收進 onedir。
- frozen app 會從 `_MEIPASS` 解析 bundled resources，並優先使用 bundled Chromium；`FACEBOOK_MONITOR_BROWSER_EXECUTABLE` 可覆寫 browser。
- frozen Windows EXE 使用 GUI subsystem，不顯示命令視窗；啟動與錯誤資訊寫入 `<data-dir>\logs\startup.log` 與 `<data-dir>\logs\app.log`。
- frozen Windows 預設啟用 system tray；source mode 預設不啟用 tray。
- Windows version metadata 目前對齊 `0.3.1`；code signing 本輪不做。
- `facebook-monitor-updater.exe` 是 PyInstaller onedir app，從 Web UI 啟動 temp updater 時必須連同 `_internal\` 複製；不可只複製單一 updater exe。
- 每次輸出 portable zip 時，必須同時產生同名 `.sha256`，供 GitHub Release updater 驗證下載完整性。
- GitHub Release tag、app version、Windows version metadata、portable zip 檔名與 `.sha256` 內容必須互相對齊；updater 只接受精確版本檔名，不 fallback 到其他版本 zip。
- Release artifact validation 會檢查 zip、`.sha256`、zip 內 EXE version resource、PyInstaller version template、必要 onedir 檔案、可選 Git tag 與可選 Authenticode signer；正式發佈前應納入 release validation。
- macOS Apple Silicon 目前使用 PyInstaller onedir zip，內含 `Facebook Monitor.app` launcher 外殼，避免 Finder 啟動時跳出 Terminal，並讓程式執行時顯示在 Dock；尚未做 Developer ID signing / notarization。

## 發佈內容

### Windows

發佈時請發佈整個 portable zip，不要只發佈單一 EXE：

```text
dist\facebook-monitor-0.3.1-windows-portable.zip
dist\facebook-monitor-0.3.1-windows-portable.zip.sha256
```

zip 內預期包含：

- `facebook-monitor.exe`
- `facebook-monitor-updater.exe`
- `_internal\browser\chrome.exe`
- `_internal\assets\facebook-monitor.ico`
- `_internal\assets\facebook-monitor-tray.ico`
- Web UI templates/static 與 Python runtime dependencies

`facebook-monitor.ico` 寫入 EXE resource；`facebook-monitor-tray.ico` 供右下角 system tray 使用。目前兩者可以共用同一份 icon bytes。Icon 製作細節看 `packaging/assets/README.md`。

GitHub Release asset 命名約定：

```text
facebook-monitor-{version}-windows-portable.zip
facebook-monitor-{version}-windows-portable.zip.sha256
```

`.sha256` 內容格式：

```text
{sha256_lowercase_hex}  facebook-monitor-{version}-windows-portable.zip
```

若 tag 是 `v0.3.1`，zip 檔名也必須是 `facebook-monitor-0.3.1-windows-portable.zip`。不要把 rc 測試 build 上傳成正式 stable release asset。
rc release 應在 GitHub 勾選 `Set as a pre-release`，避免 GitHub stable release 清單語義混淆。

### macOS onedir

macOS 目前是初期 onedir artifact，需在 macOS build machine 上產出。發佈時同樣要附同名 `.sha256`：

```text
dist/facebook-monitor-0.3.1-macos-arm64-onedir.zip
dist/facebook-monitor-0.3.1-macos-arm64-onedir.zip.sha256
```

目前 macOS 打包範圍只包含 Apple Silicon，不維護 Intel Mac artifact。

macOS zip 內預期包含：

- `Facebook Monitor.app/Contents/MacOS/facebook-monitor-launcher`
- `Facebook Monitor.app/Contents/Resources/facebook-monitor.icns`
- `facebook-monitor`
- `facebook-monitor-updater`
- `browser/Chromium.app/Contents/MacOS/Chromium` 或等效 bundled Chromium path
- Web UI templates/static 與 Python runtime dependencies

`Facebook Monitor.app` 只是一個 Finder / Dock launcher 外殼，會啟動同一個 `facebook-monitor/` onedir 內真正的 `facebook-monitor` executable；updater 替換的 app base dir 仍是這個 onedir 根目錄，不改 Windows 或 macOS updater layout 語義。

目前 macOS frozen Web UI 可檢查、下載、驗證 SHA256，並啟動獨立 updater 在主程式退出後套用新版 onedir。若未做 Developer ID signing / notarization，使用者可能需要右鍵 Open、到系統設定允許，或自行處理 quarantine。

## 打包指令

### Windows

```powershell
.\scripts\uv.ps1 run python -m pip install pyinstaller
$env:FACEBOOK_MONITOR_BUILD_DATE = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$env:FACEBOOK_MONITOR_GIT_COMMIT = (git rev-parse --short=12 HEAD)
$env:FACEBOOK_MONITOR_PACKAGING_MODE = "pyinstaller-onedir-gui-tray"
.\scripts\uv.ps1 run python -m PyInstaller packaging\pyinstaller\facebook_monitor.spec --clean --noconfirm
```

重新輸出 portable zip：

```powershell
$zip = "dist\facebook-monitor-0.3.1-windows-portable.zip"
if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path "dist\facebook-monitor" -DestinationPath $zip -CompressionLevel Optimal
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
"$hash  $(Split-Path -Leaf $zip)" | Set-Content -LiteralPath "$zip.sha256" -Encoding ascii
```

驗證 artifact 一致性：

```powershell
.\scripts\uv.ps1 run python scripts\admin\release_artifact_validation.py
.\scripts\uv.ps1 run python scripts\admin\release_validation.py --include-artifacts
```

若要同時確認 GitHub tag 語義，可指定預期 tag：

```powershell
.\scripts\uv.ps1 run python scripts\admin\release_artifact_validation.py --expected-tag v0.3.1
```

若已取得正式 code signing 憑證並完成簽章，可加上 signer subject 檢查：

```powershell
.\scripts\uv.ps1 run python scripts\admin\release_artifact_validation.py --expected-signer-subject "Your Publisher Name"
```

### macOS onedir

macOS build 必須在 macOS 上執行。若要使用 bundled Chromium，先安裝 Playwright Chromium，或用 `FACEBOOK_MONITOR_BUNDLED_CHROMIUM_DIR` 指到含 `Chromium.app` 的資料夾：

```bash
uv run python -m pip install pyinstaller
uv run playwright install chromium
export FACEBOOK_MONITOR_BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export FACEBOOK_MONITOR_GIT_COMMIT="$(git rev-parse --short=12 HEAD)"
uv run python -m PyInstaller packaging/pyinstaller/facebook_monitor_macos.spec --clean --noconfirm
```

macOS spec 會在 `dist/facebook-monitor/` 內建立 `Facebook Monitor.app` launcher，圖示來自 `packaging/assets/facebook-monitor.png`。若需要手動重建 launcher，可執行：

```bash
uv run python scripts/admin/create_macos_app_launcher.py --app-root dist/facebook-monitor
```

重新輸出 macOS portable zip 時，要保留 executable bit。可用 Python 產生 zip 與 `.sha256`，避免一般 zip 工具遺失權限：

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
PY
```

驗證 macOS artifact：

```bash
uv run python scripts/admin/release_artifact_validation.py --platform macos-arm64
```

這會驗證 artifact 形狀、完整性與 macOS executable bit；runtime 套用能力仍需搭配 frozen updater smoke。

## Windows Tray

frozen Windows EXE 預設啟用 system tray：

- 左鍵點 tray icon：開啟 Web UI。
- 右鍵點 tray icon：顯示 `Open Facebook Monitor` / `Exit` 選單。
- `Exit` 會要求 uvicorn graceful shutdown，並釋放 instance lock。

source mode 的 `uv run facebook-monitor` 預設不啟用 tray，避免影響 macOS / 開發者既有流程。需要測試 tray 時可明確指定：

```powershell
.\dist\facebook-monitor\facebook-monitor.exe --windows-tray
```

若要暫時停用 tray：

```powershell
.\dist\facebook-monitor\facebook-monitor.exe --no-windows-tray
```

因為正式 EXE 是 GUI subsystem，`--help` 不再適合作為 frozen EXE 的可見 smoke output；CLI help 仍可在 source mode 用 `.\scripts\uv.ps1 run facebook-monitor --help` 檢查。

## Browser Strategy

Windows portable 採 onedir + bundled Chromium。一般使用者不需要另外安裝 Playwright Chromium，也不需要設定 browser env var。

若要覆寫 browser，可用 `FACEBOOK_MONITOR_BROWSER_EXECUTABLE` 指向本機既有 Chrome / Chromium executable：

```powershell
$env:FACEBOOK_MONITOR_BROWSER_EXECUTABLE = "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

source mode 仍使用 Playwright 預設 browser cache；Windows frozen app 未設定 env var 時使用隨附 Chromium。

## Frozen Smoke Checklist

打包後至少檢查：

- `dist\facebook-monitor\facebook-monitor.exe` 存在，`FileVersion=0.3.1.0`、`ProductVersion=0.3.1`。
- `dist\facebook-monitor\facebook-monitor-updater.exe` 存在。
- `_internal\browser\chrome.exe` 存在。
- `_internal\assets\facebook-monitor.ico` 與 `_internal\assets\facebook-monitor-tray.ico` 存在。
- `Copying icon to EXE` 出現在 PyInstaller build log。
- portable zip 與同名 `.sha256` 已重新輸出。
- 隔離 data-dir 啟動後 `/health`、首頁與 static assets 正常。
- `runtime\server.json`、`logs\startup.log`、`app.db` 寫入預期位置。
- 同時啟動第二個 EXE 會命中 instance lock，而不是另開同一組 DB / profile。
- 使用真實 automation profile 時不觸發不必要的 guided login。
- guided login 可在乾淨 profile 下建立 session，登入後進入 Web UI。
- posts/comments target 可跑 metadata refresh、start、scan-once、stop。
- desktop、ntfy、Discord 至少各用測試設定做 manual notification smoke。
- GUI subsystem 不顯示命令視窗；tray icon 的 open/exit 行為正常。
- updater 非互動 smoke：temp updater 可套用 staged zip、替換 app files、保留 `data\app.db` / profiles、寫入 `logs\updater.log`。
- updater Web UI + tray smoke：由舊版或 rc 測試版檢查正式 release、按「下載新版並套用更新」、確認 modal 狀態會動、舊分頁短暫失效、新版頁面重新開啟、`updates\<version>` 下載 zip / `.sha256` 被清除。

macOS frozen smoke 另需確認：

- `dist/facebook-monitor/facebook-monitor` 與 `facebook-monitor-updater` 是 arm64 Mach-O，且有 executable bit。
- `dist/facebook-monitor/Facebook Monitor.app` 存在，Finder 開啟時不跳 Terminal，執行時會顯示在 Dock。
- `_internal/browser/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing` 或等效 bundled browser executable 存在且有 executable bit。
- 隔離 data dir 啟動後 `/health`、首頁與 static assets 正常。
- `scripts/admin/release_artifact_validation.py --platform macos-arm64` 通過。
- `scripts/admin/smoke_frozen_updater.py --built-app dist/facebook-monitor` 通過，且替換後 app、updater 與 bundled browser 仍保留 executable bit。

非互動 updater smoke 可用目前打包產物直接執行：

```powershell
.\scripts\uv.ps1 run python scripts\admin\smoke_frozen_updater.py
```

最近 frozen smoke 摘要屬於本機進度筆記或 release 記錄。逐次命令與歷史 debug 細節不保留在本文件；需要追溯時看 git history。

## Deferred

- Windows code signing 本輪不做；SmartScreen / Defender 提示需由 release note 說明。
- signed manifest / detached signature 尚未導入；目前只有 SHA256 完整性檢查與可選 Authenticode signer validation hook。
- frozen smoke 目前是本機 admin smoke script；尚未進 CI。
