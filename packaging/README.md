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
- Windows PyInstaller spec 會從 `APP_VERSION` 分別產生主程式與 updater 的 version resource；code signing 本輪不做。
- `facebook-monitor-updater.exe` 是 PyInstaller onedir app，從 Web UI 啟動 temp updater 時必須連同 `_internal\` 複製；不可只複製單一 updater exe。
- 每次輸出 release zip 時，必須同時產生同名 `.sha256`。同一個 GitHub Release 的 signed release manifest / `.sig` 只在最後 finalize 階段建立一次，內容依 `dist/` 內目前版本的正式平台 zip 決定。runtime updater 以 Ed25519 signed manifest 作為信任根，`.sha256` 只作相容與交叉檢查；正式壓縮入口是 `scripts/admin/create_release_zip.py`。
- GitHub Release tag、app version、Windows version metadata、macOS Info.plist version、release zip 檔名與 `.sha256` 內容必須互相對齊；updater 只接受精確版本檔名，不 fallback 到其他版本 zip。
- Release artifact validation 會檢查 zip、`.sha256`、必要 onedir 檔案、私密 runtime data 是否誤入包、Windows EXE version resource、generated Windows version resource、macOS `.app` Info.plist version、macOS app / updater / bundled browser / `.app` launcher 的 arm64 Mach-O 與 executable bit、可選 Git tag 與可選 Windows Authenticode signer；finalize 後還會檢查 signed manifest / `.sig` 是否與各平台 zip metadata 對齊。正式發佈前應納入 release validation。
- macOS Apple Silicon 目前使用 PyInstaller onedir zip，內含 `Facebook Monitor.app` native launcher 外殼，避免 Finder 啟動時跳出 Terminal，並讓程式執行期間持續顯示在 Dock；尚未做 Developer ID signing / notarization。

## 發佈內容

GitHub Release 上傳檔案分成兩層：使用者下載的 app zip，以及 updater / 人工驗證用的 sidecar 檔。一般使用者只需要下載平台 zip；`.sha256`、manifest 與 `.sig` 是自動更新與完整性驗證用。

單一平台 release 至少包含：

```text
facebook-monitor-{version}-{platform}.zip
facebook-monitor-{version}-{platform}.zip.sha256
facebook-monitor-{version}-manifest.json
facebook-monitor-{version}-manifest.json.sig
```

其中 `{platform}` 目前是 `windows-portable` 或 `macos-arm64-onedir`。若同一個 GitHub Release 同時發佈 Windows 與 macOS，兩個平台各有自己的 zip / `.sha256`，但共用同一份 manifest / `.sig`；manifest 內需列出兩個平台 asset。

### Windows

使用者下載與 updater 套用的主檔是整個 portable zip，不要只發佈單一 EXE：

```text
dist\facebook-monitor-{version}-windows-portable.zip
dist\facebook-monitor-{version}-windows-portable.zip.sha256
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
facebook-monitor-{version}-manifest.json
facebook-monitor-{version}-manifest.json.sig
```

Windows `.sha256` 內容格式：

```text
{sha256_lowercase_hex}  facebook-monitor-{version}-windows-portable.zip
```

若 tag 是 `v{version}`，zip 檔名也必須是 `facebook-monitor-{version}-windows-portable.zip`。不要把 rc 測試 build 上傳成正式 stable release asset。
rc release 應在 GitHub 勾選 `Set as a pre-release`，避免 GitHub stable release 清單語義混淆。

### macOS onedir

macOS onedir artifact 需在 macOS build machine 上產出。使用者下載與 updater 套用的主檔同樣要附同名 `.sha256`：

```text
dist/facebook-monitor-{version}-macos-arm64-onedir.zip
dist/facebook-monitor-{version}-macos-arm64-onedir.zip.sha256
```

目前 macOS 打包範圍只包含 Apple Silicon，不維護 Intel Mac artifact。

macOS zip 內預期包含：

- `Facebook Monitor.app/Contents/MacOS/facebook-monitor-launcher`
- `Facebook Monitor.app/Contents/Resources/facebook-monitor.icns`
- `facebook-monitor`
- `facebook-monitor-updater`
- `browser/Chromium.app/Contents/MacOS/Chromium` 或等效 bundled Chromium path
- Web UI templates/static 與 Python runtime dependencies

`Facebook Monitor.app` 只是一個 Finder / Dock native launcher 外殼，會啟動同一個 `facebook-monitor/` onedir 內真正的 `facebook-monitor` executable；launcher 會留在 Dock 當母程序，使用者從 Dock Quit 時會終止子程序。若舊版 updater 或 Finder 直接啟動 root `facebook-monitor` binary，新版 frozen launcher 會轉交給 `.app` native launcher，避免 Dock item 消失。updater 替換的 app base dir 仍是這個 onedir 根目錄，不改 Windows 或 macOS updater layout 語義。

目前 macOS frozen Web UI 可檢查、下載、驗證 signed manifest / SHA256，並啟動獨立 updater 在主程式退出後套用新版 onedir。若未做 Developer ID signing / notarization，使用者可能需要右鍵 Open、到系統設定允許，或自行處理 quarantine。

## 打包與 Release 指令

以下腳本都會透過 `facebook_monitor.version.APP_VERSION` 讀取 `pyproject.toml` 的 `[project].version`。升版時只要更新 `pyproject.toml`，不需要在打包指令中手動改 zip 檔名。

平台 release build 腳本會依序安裝 PyInstaller、安裝 Playwright Chromium、執行 PyInstaller、建立 release zip / `.sha256`、執行不含 manifest 的 artifact validation，最後跑完整 release validation。平台 build 階段不產生 manifest / `.sig`，也不需要 release 簽章私鑰；只有最後執行 `scripts/admin/finalize_release_manifest.py` 時才會建立 signed manifest / `.sig`。

finalize 腳本會優先使用 `docs/local/release-signing/release-ed25519-2026q2.private-key.b64`；若該檔不存在，`sign_release_manifest.py` 會改讀 `FACEBOOK_MONITOR_RELEASE_PRIVATE_KEY_B64` 環境變數。若直接手動執行 `sign_release_manifest.py`，需明確傳 `--private-key-file docs/local/release-signing/release-ed25519-2026q2.private-key.b64`，或設定上述環境變數。
若看到 `manifest_private_key_missing`，代表上述檔案與環境變數都不存在；這是私鑰缺失，不是 PyInstaller 或 macOS packaging 失敗。私鑰必須對應 `src/facebook_monitor/updates/trust.py` 內建 trusted public key，否則後續 manifest validation 仍會失敗。

### Windows

Windows release 在 Windows build machine 上執行：

```powershell
.\scripts\uv.ps1 run python scripts\admin\build_windows_release.py --force
```

### macOS onedir

macOS release 必須在 macOS build machine 上執行。若要使用 bundled Chromium，先安裝 Playwright Chromium，或用 `FACEBOOK_MONITOR_BUNDLED_CHROMIUM_DIR` 指到含 `Chromium.app` 的資料夾。macOS spec 只能在 macOS 上 build；artifact validation 也需要 macOS 產出的 arm64 Mach-O、POSIX executable bit 與 `.app` layout，Windows 上不能完整驗證這條路徑。

```bash
uv run python scripts/admin/build_macos_release.py --force
```

macOS spec 會在 `dist/facebook-monitor/` 內建立 `Facebook Monitor.app` native launcher，圖示來自 `packaging/assets/facebook-monitor.png`。若需要手動重建 launcher，可執行 `uv run python scripts/admin/create_macos_app_launcher.py --app-root dist/facebook-monitor`。

若只是要在本機確認 unsigned macOS zip 內容，不要使用上面的正式 release build 腳本；改跑底層步驟並略過 signed manifest：

```bash
uv run python -m playwright install chromium
uv run python -m PyInstaller packaging/pyinstaller/facebook_monitor_macos.spec --clean --noconfirm
uv run python scripts/admin/create_release_zip.py --platform macos-arm64 --force
uv run python scripts/admin/release_artifact_validation.py --platform macos-arm64
```

### Finalize signed manifest

平台 zip / `.sha256` 都放進同一個 `dist/` 後，最後產生唯一 signed manifest / `.sig`：

```powershell
.\scripts\uv.ps1 run python scripts\admin\finalize_release_manifest.py --force
```

finalize 腳本只接受目前 `APP_VERSION` 的正式平台 zip 命名；若 `dist/` 內只有 Windows 或只有 macOS，manifest 只列出該平台；若兩個平台 zip 都存在，manifest 會列出兩個平台 asset。腳本會先檢查每個 zip 的同名 `.sha256`，再簽署 manifest，最後對存在的平台逐一執行 signed manifest artifact validation。

若同一個 release 同時發佈 Windows 與 macOS，建議先在 macOS build machine 產出 macOS zip / `.sha256`，複製到 Windows build machine 的 `dist/`，再產 Windows zip / `.sha256`，最後在 Windows 端執行 finalize。Windows / macOS artifact 仍應各自在對應平台完成平台 build 階段的 artifact validation；finalize 後的 signed manifest validation 才代表 release asset set 完整。

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

- `dist\facebook-monitor\facebook-monitor.exe` 存在，`FileVersion` / `ProductVersion` 與 `APP_VERSION` 對齊，`OriginalFilename` 是 `facebook-monitor.exe`。
- `dist\facebook-monitor\facebook-monitor-updater.exe` 存在，`FileVersion` / `ProductVersion` 與 `APP_VERSION` 對齊，`OriginalFilename` 是 `facebook-monitor-updater.exe`。
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
- `dist/facebook-monitor/Facebook Monitor.app` 存在，Finder 開啟時不跳 Terminal，執行期間持續顯示在 Dock，從 Dock Quit 可關閉主程式。
- `_internal/browser/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing` 或等效 bundled browser executable 存在且有 executable bit。
- 隔離 data dir 啟動後 `/health`、首頁與 static assets 正常。
- `scripts/admin/release_artifact_validation.py --platform macos-arm64 --require-manifest` 通過。
- 有 release 簽章私鑰時，`scripts/admin/smoke_frozen_updater.py --built-app dist/facebook-monitor` 通過，且替換後 app、updater 與 bundled browser 仍保留 executable bit。

非互動 updater smoke 可用目前打包產物直接執行；因為正式 updater 會重驗 signed manifest，此 smoke 也需要 `docs/local/release-signing/release-ed25519-2026q2.private-key.b64` 或 `FACEBOOK_MONITOR_RELEASE_PRIVATE_KEY_B64`：

```powershell
.\scripts\uv.ps1 run python scripts\admin\smoke_frozen_updater.py
```

最近 frozen smoke 摘要屬於本機進度筆記或 release 記錄。逐次命令與歷史 debug 細節不保留在本文件；需要追溯時看 git history。

## Deferred

- Windows code signing 本輪不做；SmartScreen / Defender 提示需由 release note 說明。
- macOS Developer ID signing / notarization 本輪不做；Gatekeeper / quarantine 提示需由 release note 說明。
- signed manifest / detached signature 已作為 updater 免費信任鏈；OS 發布者身分驗證仍依上面兩項另行處理。
- frozen smoke 目前是本機 admin smoke script；尚未進 CI。
