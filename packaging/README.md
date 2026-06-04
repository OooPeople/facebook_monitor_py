# Packaging

本文件只回答「怎麼打包、產物在哪、發佈前怎麼驗」。使用者操作看 `README.md` / `docs/USAGE.md`；工具角色看 `docs/tooling.md`；frozen updater 產品語義看 `docs/ARCHITECTURE.md#frozen-updater`。

版本來源只有 `pyproject.toml` 的 `[project].version`。升版時先改 `pyproject.toml`，不要手動改 zip 檔名。

## 打包指令

### Windows portable

在 Windows build machine 的 repo 根目錄執行：

```powershell
.\scripts\uv.ps1 run python scripts\admin\build_windows_release.py --force
```

輸出：

```text
dist\facebook-monitor\
dist\facebook-monitor-{version}-windows-portable.zip
dist\facebook-monitor-{version}-windows-portable.zip.sha256
```

### macOS Apple Silicon onedir

必須在 macOS build machine 上執行；目前只維護 Apple Silicon：

```bash
uv run python scripts/admin/build_macos_release.py --force
```

輸出：

```text
dist/facebook-monitor/
dist/facebook-monitor-{version}-macos-arm64-onedir.zip
dist/facebook-monitor-{version}-macos-arm64-onedir.zip.sha256
```

若要使用既有 Chromium bundle，可先設定 `FACEBOOK_MONITOR_BUNDLED_CHROMIUM_DIR` 指到含 `Chromium.app` 的資料夾。

### Signed manifest

正式 GitHub Release 還需要 manifest / detached signature。等要發佈的平台 zip 與同名 `.sha256` 都放在同一個 `dist/` 後執行：

```powershell
.\scripts\uv.ps1 run python scripts\admin\finalize_release_manifest.py --force
```

macOS 上可用：

```bash
uv run python scripts/admin/finalize_release_manifest.py --force
```

輸出：

```text
dist/facebook-monitor-{version}-manifest.json
dist/facebook-monitor-{version}-manifest.json.sig
```

私鑰來源優先順序：

1. `docs/local/release-signing/release-ed25519-2026q2.private-key.b64`
2. `FACEBOOK_MONITOR_RELEASE_PRIVATE_KEY_B64`

`manifest_private_key_missing` 代表缺 release manifest 私鑰，不是 PyInstaller 打包失敗。

## Release 流程

單一平台：

1. 更新 `pyproject.toml` 版本。
2. 執行對應平台 build script。
3. 執行 `finalize_release_manifest.py --force`。
4. 上傳平台 zip、同名 `.sha256`、manifest、`.sig` 到同一個 GitHub Release。

Windows + macOS 同時發佈：

1. 在 macOS 產出 macOS zip / `.sha256`。
2. 把 macOS zip / `.sha256` 複製到 Windows build machine 的 `dist/`。
3. 在 Windows 產出 Windows zip / `.sha256`。
4. 在同一個 `dist/` 執行 `finalize_release_manifest.py --force`。
5. 上傳兩個平台 zip / `.sha256`，以及共用的 manifest / `.sig`。

GitHub tag、`pyproject.toml` version、release zip 檔名與 manifest 版本必須對齊。正式 tag 使用 `v{version}`；rc 測試 build 要標成 GitHub pre-release，不要混進 stable release asset。

## 產物命名

一般使用者只下載平台 zip；`.sha256`、manifest 與 `.sig` 給 updater 與人工驗證使用。不要只發佈單一 EXE 或單一 updater binary。

單一平台 release 至少包含：

```text
facebook-monitor-{version}-{platform}.zip
facebook-monitor-{version}-{platform}.zip.sha256
facebook-monitor-{version}-manifest.json
facebook-monitor-{version}-manifest.json.sig
```

目前 `{platform}` 只有：

```text
windows-portable
macos-arm64-onedir
```

zip 解開後的 root 是 `facebook-monitor/`。macOS zip 會在 root 放 `README.md`，提醒首次從 GitHub 下載遇到 quarantine / Gatekeeper 阻擋時的手動處理；這只是使用者提示，不取代 Developer ID signing / notarization。release zip 不得包含 `data/`、profiles、cookies、tokens、session dumps、logs 或其他 runtime 私密資料；`create_release_zip.py` 與 artifact validation 會檢查。

## Build Script 做什麼

`build_windows_release.py` / `build_macos_release.py` 會依序執行：

1. 安裝 PyInstaller。
2. 安裝 Playwright Chromium。
3. 執行對應 PyInstaller spec。
4. 建立平台 zip 與同名 `.sha256`。
5. 跑不要求 manifest 的平台 artifact validation。
6. 跑不要求 manifest 的 release validation。

常用選項：

```powershell
.\scripts\uv.ps1 run python scripts\admin\build_windows_release.py --force --expected-tag v{version}
.\scripts\uv.ps1 run python scripts\admin\build_windows_release.py --force --skip-pyinstaller-install --skip-playwright-install
.\scripts\uv.ps1 run python scripts\admin\build_windows_release.py --force --expected-signer-subject "簽章憑證 subject 片段"
```

macOS build script 也支援 `--expected-tag`、`--skip-pyinstaller-install`、`--skip-playwright-install`、`--skip-release-validation`。

## 驗證

finalize 後重驗 release asset：

```powershell
.\scripts\uv.ps1 run python scripts\admin\release_artifact_validation.py --platform windows --require-manifest
```

```bash
uv run python scripts/admin/release_artifact_validation.py --platform macos-arm64 --require-manifest
```

非互動 updater smoke 需要 signed manifest 私鑰，因為 updater 會重驗 manifest：

```powershell
.\scripts\uv.ps1 run python scripts\admin\smoke_frozen_updater.py
```

macOS 已有打包產物時：

```bash
uv run python scripts/admin/smoke_frozen_updater.py --built-app dist/facebook-monitor
```

發佈前至少確認：

- zip、`.sha256`、manifest、`.sig` 都是目前版本，且 validation 通過。
- frozen app 用隔離 data dir 可啟動，`/health`、首頁與 static assets 正常。
- Windows zip 有 main EXE、updater EXE、bundled Chromium 與 tray icon asset。
- macOS zip 有 `Facebook Monitor.app`，主要 executable / updater / bundled browser 保留 arm64 Mach-O 與 executable bit。
- updater smoke 可替換 app files、保留 `data/` / profiles，並清除本次下載 zip / `.sha256` / manifest / pending handoff。

## 目前不做

- Windows Authenticode code signing 尚未導入；SmartScreen / Defender 提示需由 release note 說明。
- macOS Developer ID signing / notarization 尚未導入；Gatekeeper / quarantine 提示需由 release note 說明。
- Ed25519 signed manifest 是 updater 信任鏈，不等於 OS 發布者身分簽章。
