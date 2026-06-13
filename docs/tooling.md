# 工具索引

本文件是 scripts / CLI 工具索引。README 只保留正式日常入口；架構語義看 `docs/ARCHITECTURE.md`；打包、release artifact、manifest 與 frozen smoke 細節看 `packaging/README.md`。

## 入口原則

- 正式日常入口只有 Web UI：`facebook-monitor`。
- profile 登入 / 檢查是正式維運入口：`facebook-monitor-login`。
- admin / debug / internal 工具不得描述成日常入口。
- admin / debug / internal 工具預設也走正式 runtime path resolver；若要操作特定資料根目錄，使用同一組 `--data-dir` / `--profile-name` / `--db-path` / `--profile-dir`。
- `--profile-dir` 只能指向 `<data-dir>/profiles/` 底下；外部測試 profile 必須使用 debug-only 的 `--unsafe-profile-dir`，且仍會拒絕常見 Chrome / Edge / Chromium 日常 profile。
- 新功能預設先接 Web UI + resident main 主路徑；debug / fallback 工具只有在有實際維護價值時才跟進。
- 不再新增 `phase_*` 命名 script。

## 工具清單

| 工具 | 路徑 | 角色 | 用途 | 正式入口 |
|---|---|---|---|---|
| Web UI | `facebook-monitor` | Start | 日常 target 管理、設定與背景掃描 | 是 |
| Setup Login | `facebook-monitor-login` | Start | 開啟專用 automation profile，供登入與檢查 session | 是，維運入口 |
| Admin Console | `scripts/admin/console.py` | Admin | 互動式管理 target、設定與一次性掃描 | 否 |
| Manage Targets | `scripts/admin/manage_targets.py` | Admin | 只編輯 target 設定與啟停狀態 | 否 |
| Release Validation | `scripts/admin/release_validation.py` | Admin | release tag 前執行可重現本機驗證流程 | 否 |
| Windows Release Builder | `scripts/admin/build_windows_release.py` | Admin packaging | 串接 Windows PyInstaller、release zip / `.sha256`、不含 manifest 的 artifact validation 與 release validation | 否 |
| macOS Release Builder | `scripts/admin/build_macos_release.py` | Admin packaging | 串接 macOS Apple Silicon PyInstaller、release zip / `.sha256`、不含 manifest 的 artifact validation 與 release validation；需在 macOS 上執行 | 否 |
| Release Zip Builder | `scripts/admin/create_release_zip.py` | Admin packaging | 從 `dist/facebook-monitor` 建立 Windows / macOS release zip 與同名 `.sha256`，並先檢查平台必要檔案與私密 runtime data | 否 |
| Release Manifest Builder | `scripts/admin/create_release_manifest.py` | Admin packaging | 從 release zip 建立 signed updater manifest JSON，記錄平台、檔名、size 與 SHA256 | 否 |
| Release Manifest Signer | `scripts/admin/sign_release_manifest.py` | Admin packaging | 使用本機或 CI secret 內的 Ed25519 私鑰輸出 manifest detached signature | 否 |
| Finalize Release Manifest | `scripts/admin/finalize_release_manifest.py` | Admin packaging | 依 `dist/` 內目前版本的正式平台 zip 建立唯一 signed manifest / `.sig`，並重驗 manifest 與 artifact metadata | 否 |
| Release Artifact Validation | `scripts/admin/release_artifact_validation.py` | Admin | 驗證 release zip、同名 `.sha256`、平台必要 onedir 檔案與私密 runtime data；加 `--require-manifest` 時驗 signed manifest / `.sig`；Windows 可選驗證 Authenticode signer | 否 |
| Complexity Report | `scripts/admin/complexity_report.py` | Admin review | 用 Lizard 產生 NLOC / CCN / token ranking，再加上 known-large / watchlist 摘要；只作人工審查輔助，不是 CI gate | 否 |
| Database Invariant Check | `scripts/admin/check_database_invariants.py` | Admin diagnostics | 唯讀檢查正式 SQLite DB 內 enum、boolean、range 與 runtime ownership invariant | 否 |
| Windows Version Resource Builder | `scripts/admin/windows_version_resource.py` | Admin packaging | 由 `APP_VERSION` 產生 Windows PyInstaller version resource；通常由 Windows spec 自動呼叫 | 否 |
| Frozen Updater Smoke | `scripts/admin/smoke_frozen_updater.py` | Admin smoke | 用已打包 onedir build 建立 fixture update zip，驗證獨立 updater 可替換 app files、保留 data 並清除 handoff 檔案 | 否 |
| macOS App Launcher Builder | `scripts/admin/create_macos_app_launcher.py` | Admin packaging | 在 macOS onedir 內建立 `Facebook Monitor.app` Finder / Dock native launcher，圖示來源為 `packaging/assets/facebook-monitor.png` | 否 |
| Relogin Flow Smoke | `scripts/admin/smoke_relogin_flow.py` | Admin smoke | 使用隔離暫存資料驗證重新登入警告與 launcher login gate | 否 |
| Capture Posts Target | `scripts/debug/capture_posts_target.py` | Debug | 開啟瀏覽器擷取目前社團頁作為 posts target | 否 |
| One-shot Scan | `scripts/debug/one_shot_scan.py` | Debug | 對已保存 target 執行一次 one-shot 掃描 | 否 |
| Worker Probe | `scripts/debug/worker_probe.py` | Debug | 使用專用 profile 執行背景掃描可行性 probe | 否 |
| Text Newline Probe | `scripts/debug/text_newline_probe.py` | Debug | 檢查可見 Facebook DOM 文字是否仍能取得換行資訊；不跑正式 scan pipeline | 否 |
| One-shot Scheduler | `scripts/internal/one_shot_scheduler.py` | Internal | 直接啟動 one-shot debug/fallback scheduler loop，不作正式主路徑保證 | 否 |
| Resident Main | `scripts/internal/resident_main.py` | Internal | 直接啟動正式 async resident main worker loop | 否 |
| uv wrapper | `scripts/uv.ps1` | 指令 wrapper | 固定從專案根目錄執行 uv，並使用工作區內 cache | 否，wrapper |

## 常用指令

正式入口：

```powershell
.\scripts\uv.ps1 run facebook-monitor
.\scripts\uv.ps1 run facebook-monitor-login
.\scripts\uv.ps1 run facebook-monitor --data-dir "D:\fb_monitor_data"
.\scripts\uv.ps1 run facebook-monitor --data-dir "D:\fb_monitor_data" --port 4818 --no-open-browser
.\scripts\uv.ps1 run facebook-monitor --data-dir "D:\fb_monitor_data" --profile-name automation_alt
.\scripts\uv.ps1 run facebook-monitor-login --data-dir "D:\fb_monitor_data" --profile-name automation_alt
```

低頻工具：

```powershell
.\scripts\uv.ps1 run python .\scripts\admin\console.py
.\scripts\uv.ps1 run python .\scripts\admin\manage_targets.py
.\scripts\uv.ps1 run python .\scripts\admin\release_validation.py --skip-sync
.\scripts\uv.ps1 run python .\scripts\admin\build_windows_release.py --force
.\scripts\uv.ps1 run python .\scripts\admin\finalize_release_manifest.py --force
.\scripts\uv.ps1 run python .\scripts\admin\complexity_report.py --top 20 --format markdown
.\scripts\uv.ps1 run python .\scripts\admin\check_database_invariants.py
.\scripts\uv.ps1 run python .\scripts\admin\smoke_frozen_updater.py
.\scripts\uv.ps1 run python .\scripts\admin\smoke_relogin_flow.py --headed
.\scripts\uv.ps1 run python .\scripts\debug\one_shot_scan.py --group-id "<group_id>" --scroll-rounds 3
.\scripts\uv.ps1 run python .\scripts\debug\text_newline_probe.py "<facebook_url>" --mode auto
.\scripts\uv.ps1 run python .\scripts\internal\resident_main.py --max-cycles 2 --interval-seconds 1
.\scripts\uv.ps1 run python .\scripts\admin\console.py --data-dir "D:\fb_monitor_data"
```

驗證：

```powershell
.\scripts\uv.ps1 run pytest -q
.\scripts\uv.ps1 run pytest -q --cov=facebook_monitor --cov-report=term-missing --cov-fail-under=80
.\scripts\uv.ps1 run mypy
.\scripts\uv.ps1 run pytest tests\core --cov=facebook_monitor.core --cov-report=term-missing -q
.\scripts\uv.ps1 run python -m compileall -q src scripts tests
.\scripts\uv.ps1 run ruff check src scripts tests
.\scripts\uv.ps1 run pip-audit
Get-ChildItem -Path src\facebook_monitor\webapp\static -Filter *.js -Recurse | ForEach-Object { node --check $_.FullName }
git diff --check
```

CI 使用固定的 `uv==0.9.0` 執行 `uv sync --locked --all-extras --dev`，再用 Node 24 對 `src/facebook_monitor/webapp/static/**/*.js` 執行 syntax check，並以目前完整測試可通過的 80% coverage 作為 baseline。這個門檻只防止覆蓋率意外大幅倒退；後續新增測試後再逐步提高，不用為了既有大型模組一次重寫測試。本機開發可使用較新的 uv；固定 CI 版本只是讓 GitHub Actions 的 resolver / installer 行為可重現。

Complexity Report 是人工審查前置資料，不是 release validation 必跑項，也不會因 CCN / NLOC 排名造成失敗。CI 只會用 report-only step 把 Markdown 摘要寫入 GitHub Actions summary，供 review 時判斷本次變更是否需要拆分；它不是 hard gate。預設會使用 `docs/maintainability_annotations.json` 將已人工確認合理的大型檔案列到 known-large section，避免它們長期佔住主排行；known-large 不是永久豁免，若相關檔案被修改仍應重新 review。若要查看純排名可加 `--no-annotations`，若要把 known-large 放回主排行可加 `--include-known-large`。

Release tag 前建議執行：

```powershell
.\scripts\uv.ps1 run python scripts\admin\release_validation.py
.\scripts\uv.ps1 run python scripts\admin\release_validation.py --include-artifacts
.\scripts\uv.ps1 run python scripts\admin\release_validation.py --include-artifacts --artifact-platform macos-arm64
```

腳本會輸出 OS、Python、uv、git commit 與每個驗證 command 結果。環境已同步時可加 `--skip-sync`；需要 dependency advisory 檢查時可加 `--include-audit`。artifact 參數、manifest 驗證、platform zip 檢查與 frozen updater smoke 細節集中在 `packaging/README.md#驗證`。

版本與 Web asset cache key 的來源語義看 `docs/ARCHITECTURE.md#frozen-updater`；release validation 會印出目前 app / asset version，正式發佈時以升版作為瀏覽器 cache busting 來源。

## 啟動診斷位置

- runtime info：`<data-dir>\runtime\server.json`
- startup diagnostics：`<data-dir>\logs\startup.log`
- app log：`<data-dir>\logs\app.log`
- error log：`<data-dir>\logs\error.log`

詳細 launcher / resource lock / startup semantics 看 `docs/ARCHITECTURE.md#正式入口`。

## Browser-level Manual QA

Sidebar 調整順序 / grouping 不能只靠 unit test 判斷；release 前至少用一般 Chrome 視窗檢查一次：

- 建立 4 個以上 targets，包含 posts、comments 與未分組 target。
- 進入「調整順序」模式，拖曳 target，確認 sidebar 與中央卡片順序一致，重新整理後仍保留。
- 取消調整順序時，不保存暫時拖曳結果。
- 建立、重新命名、刪除空群組；嘗試刪除非空群組時 target 不被移出。
- 將 target 拖入群組與移回未分組，重新整理後仍正確。
- 套用群組設定模板時，確認影響 target 數、套用區段、覆蓋提示與不可自動復原提示都有顯示。
- 等待 dashboard partial update，確認 sidebar 狀態更新後排序、分組、收合狀態不被打亂。
- 開第二個瀏覽器視窗觀察同一 Web UI；重新整理後不得出現錯誤順序或 JS error。

## Packaging

PyInstaller、platform zip、signed manifest、artifact validation 與 frozen smoke checklist 看 `packaging/README.md`。

## Updater 開發驗證

未打包前可先針對 updater 相關模組跑聚焦驗證：

```powershell
.\scripts\uv.ps1 run pytest tests\updates tests\webapp\test_app.py -q
.\scripts\uv.ps1 run mypy src\facebook_monitor\updates src\facebook_monitor\updater.py src\facebook_monitor\webapp\routes\settings.py
.\scripts\uv.ps1 run ruff check src\facebook_monitor\updates src\facebook_monitor\updater.py src\facebook_monitor\webapp\routes\settings.py tests\updates tests\webapp\test_app.py
Get-ChildItem -Path src\facebook_monitor\webapp\static -Filter *.js -Recurse | ForEach-Object { node --check $_.FullName }
```

打包後的非互動 updater smoke 與人工 Web UI / tray checklist 放在 `packaging/README.md#驗證`。
