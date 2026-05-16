# Packaging

本文件記錄 Windows EXE 打包方式、發佈包內容與 frozen smoke checklist。正式產品入口仍是 `facebook-monitor`，打包不得繞過 `src/facebook_monitor/launcher.py`。

## 目前設計

- PyInstaller spec：`packaging/pyinstaller/facebook_monitor.spec`。
- 目前只維護 Windows onedir build；輸出資料夾是 `dist\facebook-monitor\`。
- EXE 使用正式 launcher entrypoint，保留 instance lock、profile gate、runtime reset、loopback-only 與 CSRF 保護。
- Web templates/static、Playwright Python driver data 與 Chromium browser binary 都會收進 onedir。
- frozen app 會從 `_MEIPASS` 解析 bundled resources，並優先使用 bundled Chromium；`FACEBOOK_MONITOR_BROWSER_EXECUTABLE` 可覆寫 browser。
- frozen Windows EXE 使用 GUI subsystem，不顯示命令視窗；啟動與錯誤資訊寫入 `<data-dir>\logs\startup.log` 與 `<data-dir>\logs\app.log`。
- frozen Windows 預設啟用 system tray；source mode 預設不啟用 tray。
- Windows version metadata 目前對齊 `0.1.0-rc1`；code signing 本輪不做。

## 發佈內容

發佈時請發佈整個 portable zip，不要只發佈單一 EXE：

```text
dist\facebook-monitor-0.1.0-rc1-windows-portable.zip
```

zip 內預期包含：

- `facebook-monitor.exe`
- `_internal\browser\chrome.exe`
- `_internal\assets\facebook-monitor.ico`
- `_internal\assets\facebook-monitor-tray.ico`
- Web UI templates/static 與 Python runtime dependencies

`facebook-monitor.ico` 寫入 EXE resource；`facebook-monitor-tray.ico` 供右下角 system tray 使用。目前兩者可以共用同一份 icon bytes。Icon 製作細節看 `packaging/assets/README.md`。

## 打包指令

```powershell
.\scripts\uv.ps1 run python -m pip install pyinstaller
$env:FACEBOOK_MONITOR_BUILD_DATE = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$env:FACEBOOK_MONITOR_GIT_COMMIT = (git rev-parse --short=12 HEAD)
$env:FACEBOOK_MONITOR_PACKAGING_MODE = "pyinstaller-onedir-gui-tray"
.\scripts\uv.ps1 run python -m PyInstaller packaging\pyinstaller\facebook_monitor.spec --clean --noconfirm
```

重新輸出 portable zip：

```powershell
$zip = "dist\facebook-monitor-0.1.0-rc1-windows-portable.zip"
if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path "dist\facebook-monitor" -DestinationPath $zip -CompressionLevel Optimal
```

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

- `dist\facebook-monitor\facebook-monitor.exe` 存在，`FileVersion=0.1.0.1`、`ProductVersion=0.1.0-rc1`。
- `_internal\browser\chrome.exe` 存在。
- `_internal\assets\facebook-monitor.ico` 與 `_internal\assets\facebook-monitor-tray.ico` 存在。
- `Copying icon to EXE` 出現在 PyInstaller build log。
- portable zip 已重新輸出。
- 隔離 data-dir 啟動後 `/health`、首頁與 static assets 正常。
- `runtime\server.json`、`logs\startup.log`、`app.db` 寫入預期位置。
- 同時啟動第二個 EXE 會命中 instance lock，而不是另開同一組 DB / profile。
- 使用真實 automation profile 時不觸發不必要的 guided login。
- guided login 可在乾淨 profile 下建立 session，登入後進入 Web UI。
- posts/comments target 可跑 metadata refresh、start、scan-once、stop。
- desktop、ntfy、Discord 至少各用測試設定做 manual notification smoke。
- GUI subsystem 不顯示命令視窗；tray icon 的 open/exit 行為正常。

最近 frozen smoke 摘要看 `docs/TASK_BREAKDOWN.md#驗證`。逐次命令與歷史 debug 細節不保留在本文件；需要追溯時看 git history。

## 尚未封口

- Windows code signing 本輪不做；SmartScreen / Defender 提示需由 release note 說明。
- frozen smoke 尚未進 CI；目前仍需人工執行。
