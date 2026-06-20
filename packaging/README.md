# Packaging

本文件只回答「怎麼打包、產物在哪、發佈前怎麼驗」。使用者操作看 `README.md` / `docs/USAGE.md`；工具角色看 `docs/tooling.md`；frozen updater 產品語義看 `docs/ARCHITECTURE.md#frozen-updater`。

版本來源只有 `pyproject.toml` 的 `[project].version`。升版時先改 `pyproject.toml`，不要手動改 zip 檔名。

## Dependency / Toolchain Policy

Release build 與 CI 的 Python dependency set 以 `uv.lock` 為準，必須用
`uv sync --locked` 或等價的 locked sync 建環境。變更套件版本時，應在同一個
變更中明確更新 `uv.lock` 並跑 release validation；不要讓 release build 自行重新
resolve dependency。

GitHub Actions 目前固定安裝 `uv==0.9.0`，避免 CI 因 uv installer / resolver
行為變動而漂移。這不限制本機 source-mode 開發使用新版 uv；若要升級 CI /
release 使用的 uv，應用一個明確 commit 更新 workflow 與本段紀錄，並確認
`uv sync --locked --all-extras --dev` 通過。

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

1. `--private-key-b64 <base64>`
2. `--private-key-file <path>`
3. `FACEBOOK_MONITOR_RELEASE_PRIVATE_KEY_B64`
4. repo 外本機預設：`~/.facebook-monitor/release-signing/release-ed25519-2026q2.private-key.b64`

舊的 `docs/local/release-signing/...` checkout 內位置不再被 tooling 自動採用；
若本機仍有舊檔，請移到 repo 外預設路徑，或短期以 `--private-key-file`
明確指定。不要把長期 release signing key 放在 repo tree 內，即使該路徑已被
`.gitignore` 忽略。

`manifest_private_key_missing` 代表缺 release manifest 私鑰，不是 PyInstaller 打包失敗。

#### Release manifest key lifecycle

目前正式 signing key id 是 `release-ed25519-2026q2`，runtime trust root 在
`src/facebook_monitor/updates/trust.py`。`finalize_release_manifest.py` 的預設
`--key-id` 必須存在於這個 trust root；測試會防止預設 key id 與 runtime
信任清單漂移。

日常 release 不需要、也不應該定期輪替 key。輪替只在下列情境進行：

- 私鑰疑似外洩或遺失。
- Ed25519 / trust policy 需要升級。
- 準備公開大量發佈前，想把新 key 納入正式信任鏈。

輪替流程必須先發佈一個 bridge release：新版程式同時信任舊 key 與新 key，
但 release manifest 仍可由舊 key 簽署，讓既有安裝能安全升級。
等 bridge release 已成為最低支援版本後，後續 release 才改用新 `--key-id`
簽署；舊 key 退休時要在 release note 與本文件記錄最低可自動更新版本。

若私鑰外洩，停止使用該 key 簽署新 release，從 runtime trust root 移除外洩 key，使用未外洩 key 產生修復版；若既有安裝只信任外洩 key，必須改走人工下載安裝，不可用同一把外洩 key 發佈「修復」自動更新。

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

zip 解開後的 root 是 `facebook-monitor/`。macOS zip 會在 root 放 `README.txt`，
提醒首次從 GitHub 下載遇到 quarantine / Gatekeeper 阻擋時的手動處理；
這只是使用者提示，不取代 Developer ID signing / notarization。
release zip 不得包含 `data/`、profiles、cookies、tokens、session dumps、logs
或其他 runtime 私密資料；`create_release_zip.py` 與 artifact validation 會檢查。

## Build Script 做什麼

`build_windows_release.py` / `build_macos_release.py` 會依序執行：

1. 安裝 scripts 內固定版本的 PyInstaller，並驗證目前環境版本。
2. 安裝 Playwright Chromium。
3. 執行對應 PyInstaller spec。
4. 建立平台 zip 與同名 `.sha256`。
5. 跑不要求 manifest 的平台 artifact validation。
6. 跑不要求 manifest 的 pre-finalize release validation。

常用選項：

```powershell
.\scripts\uv.ps1 run python scripts\admin\build_windows_release.py --force --expected-tag v{version}
.\scripts\uv.ps1 run python scripts\admin\build_windows_release.py --force --skip-pyinstaller-install --skip-playwright-install
.\scripts\uv.ps1 run python scripts\admin\build_windows_release.py --force --expected-signer-subject "簽章憑證 subject 片段"
```

macOS build script 也支援 `--expected-tag`、`--skip-pyinstaller-install`、
`--skip-playwright-install`、`--skip-release-validation`。`--skip-pyinstaller-install`
只略過安裝，仍會驗證 PyInstaller 版本。

## 驗證

正式上傳 GitHub Release asset 前，只有在平台 build、signed manifest finalize、
`release_validation.py --include-artifacts`、對應平台
`release_artifact_validation.py --require-manifest`、frozen updater smoke，以及必要
人工 smoke 都完成後，才可回報「上傳前完整檢查通過」。
平台 build script 內建的不要求 manifest validation 只能回報為
「平台 build / pre-finalize validation 通過」，不可視為 release upload-ready。

驗證關卡：

- 平台建置腳本：
  - 必要輸入：source tree、locked env、platform build machine。
  - 主要檢查：PyInstaller、release zip、`.sha256`、pre-finalize validation。
  - 可回報用語：平台 build / pre-finalize validation 通過。
  - 不可替代：signed manifest、可上傳 artifact 檢查。
- Manifest finalize：
  - 必要輸入：目前 version 的平台 zip / `.sha256`、release private key。
  - 主要檢查：manifest JSON、detached signature、artifact metadata 對齊。
  - 可回報用語：signed manifest 已完成。
  - 不可替代：OS code signing / notarization。
- 含 artifact 的 release validation：
  - 必要輸入：finalized manifest / `.sig` 與平台 zip。
  - 主要檢查：pytest/coverage、mypy、ruff、audit、artifact metadata 與 layout。
  - 可回報用語：含 artifact 的 release validation 通過。
  - 不可替代：frozen updater smoke、人工 Facebook smoke。
- Artifact validation `--require-manifest`：
  - 必要輸入：GitHub Release 將上傳的 zip、`.sha256`、manifest、`.sig`。
  - 主要檢查：signed manifest、SHA256、platform contents、私密資料邊界。
  - 可回報用語：artifact validation 通過。
  - 不可替代：full release validation。
- Frozen updater smoke：
  - 必要輸入：已打包 app、signed test update fixture。
  - 主要檢查：updater 替換 app files、保留 data、清理 atomic download set。
  - 可回報用語：frozen updater smoke 通過。
  - 不可替代：真實 app restart / Facebook runtime smoke。
- 人工 smoke：
  - 必要輸入：隔離 data dir 與實際 UI。
  - 主要檢查：login、metadata resolver、posts/comments scan、notifications。
  - 可回報用語：人工 smoke 完成。
  - 不可替代：automated unit/release validation。

一般 release validation：

```powershell
.\scripts\uv.ps1 run python scripts\admin\release_validation.py
```

只有在環境已同步，且 dependency、`uv.lock`、workflow 或驗證腳本沒有變更時，
才可加 `--skip-sync`；預設會執行 `pip-audit` 以對齊 CI dependency audit，
只有離線或刻意重現非 audit 檢查時才加 `--skip-audit`。
若使用 `--skip-release-validation`、`--skip-artifact-manifest`、`--skip-audit`，
或尚未做人工 Facebook login / metadata resolver / posts-comments scan /
notification smoke，必須在 release note、handoff 或任務狀態中列為未完成驗證。
非 Git checkout（例如 source zip）會跳過 `git diff --check` 並明確提示；
Git checkout 內仍會執行且遇到 whitespace 或 conflict marker 時 fail。

需要連 artifact 一起驗時：

```powershell
.\scripts\uv.ps1 run python scripts\admin\release_validation.py --include-artifacts
.\scripts\uv.ps1 run python scripts\admin\release_validation.py --include-artifacts --artifact-platform macos-arm64
```

`--include-artifacts` 預設檢查目前 version 的 Windows portable zip、同名
`.sha256`、signed manifest / `.sig`、zip 內 EXE version resource、generated
Windows version resource、必要 onedir 檔案與私密 runtime data。
若平台 build 階段尚未 finalize manifest，可加 `--skip-artifact-manifest` 只驗
zip / `.sha256` / 平台內容。
若要驗 macOS Apple Silicon onedir zip，加 `--artifact-platform macos-arm64`，
會檢查 `.app` Info.plist version、首次開啟 `README.txt`、主要 executable /
updater / bundled browser / `.app` launcher 的 arm64 Mach-O 與 executable bit。
若已有正式 Windows code signing 憑證，可加 `--expected-signer-subject "<subject>"`；
若要確認 tag 語義，可加 `--expected-tag vX.Y.Z`。

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

Updater 程式碼的聚焦開發驗證指令看 `docs/tooling.md#updater-開發驗證`。

發佈前至少確認：

- zip、`.sha256`、manifest、`.sig` 都是目前版本，且 validation 通過。
- frozen app 用隔離 data dir 可啟動，`/health`、首頁與 static assets 正常。
- Windows zip 有 main EXE、updater EXE、bundled Chromium 與 tray icon asset。
- macOS zip 有 `Facebook Monitor.app` 與首次開啟 `README.txt`，主要 executable /
  updater / bundled browser / launcher 保留 arm64 Mach-O 與 executable bit，
  且 `.app` bundle 保留 app icon、Info.plist identity 與 ad-hoc signature metadata。
- updater smoke 可替換 app files、保留 `data/` / profiles，並清除本次 atomic download set 與 pending handoff；舊 app 等待與重啟新版 app 需另以手動 smoke 驗證。
- 正式 tag 前保留完整輸出紀錄，包含 Facebook login、metadata resolver、posts/comments scan 與 notification smoke 結果。

## 目前不做

- Windows Authenticode code signing 尚未導入；SmartScreen / Defender 提示需由 release note 說明。
- macOS Developer ID signing / notarization 尚未導入；Gatekeeper / quarantine 提示需由 release note 說明。
- Ed25519 signed manifest 是 updater 信任鏈，不等於 OS 發布者身分簽章。
