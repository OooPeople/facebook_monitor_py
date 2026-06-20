# 工具索引

本文件是 scripts / CLI 工具索引。README 只保留正式日常入口；
架構語義看 `docs/ARCHITECTURE.md`；打包、release artifact、
manifest 與 frozen smoke 細節看 `packaging/README.md`。

## 入口原則

- 正式日常入口只有 Web UI：`facebook-monitor`。
- profile 登入 / 檢查是正式維運入口：`facebook-monitor-login`。
- admin / debug / internal 工具不得描述成日常入口。
- admin / debug / internal 工具預設也走正式 runtime path resolver；
  若要操作特定資料根目錄，使用同一組 `--data-dir` / `--profile-name` /
  `--db-path` / `--profile-dir`。
- `--profile-dir` 只能指向 `<data-dir>/profiles/` 底下；外部測試 profile
  必須使用 debug-only 的 `--unsafe-profile-dir`，且仍會拒絕常見 Chrome /
  Edge / Chromium 日常 profile。
- 新功能預設先接 Web UI + resident main 主路徑；debug / fallback 工具只有在有實際維護價值時才跟進。
- One-shot debug / fallback scheduler 可能只會將通知寫入 notification outbox；
  除非同一 process 已啟動 outbox dispatcher，否則不保證掃描後立即送出外部
  notification。
- 正式 Web UI / resident main 路徑才提供背景 dispatcher drain 語義。
- 不再新增 `phase_*` 命名 script。

## 工具清單

| 工具 | 路徑 | 角色 | 用途 | 正式入口 |
|---|---|---|---|---|
| Web UI | `facebook-monitor` | Start | 日常 target 管理、設定與背景掃描 | 是 |
| Setup Login | `facebook-monitor-login` | Start | 開啟專用 automation profile，供登入與檢查 session | 是，維運入口 |
| Admin Console | `scripts/admin/console.py` | Admin | 互動式管理 target、設定與一次性掃描 | 否 |
| Manage Targets | `scripts/admin/manage_targets.py` | Admin | 只編輯 target 設定與啟停狀態 | 否 |
| Release Validation | `scripts/admin/release_validation.py` | Admin | 本機 release 驗證與 audit | 否 |
| Windows Release Builder | `scripts/admin/build_windows_release.py` | Admin packaging | Windows 平台 build pipeline | 否 |
| macOS Release Builder | `scripts/admin/build_macos_release.py` | Admin packaging | macOS Apple Silicon build pipeline | 否 |
| Release Zip Builder | `scripts/admin/create_release_zip.py` | Admin packaging | 建立平台 release zip 與 `.sha256` | 否 |
| Release Manifest Builder | `scripts/admin/create_release_manifest.py` | Admin packaging | 建立 signed updater manifest | 否 |
| Release Manifest Signer | `scripts/admin/sign_release_manifest.py` | Admin packaging | 使用 Ed25519 私鑰輸出 detached signature | 否 |
| Finalize Release Manifest | `scripts/admin/finalize_release_manifest.py` | Admin packaging | 建立唯一 signed manifest / `.sig` | 否 |
| Release Artifact Validation | `scripts/admin/release_artifact_validation.py` | Admin | 驗證 release artifact 與私密資料邊界 | 否 |
| Complexity Report | `scripts/admin/complexity_report.py` | Admin review | 產生 Lizard ranking 與 known-large / watchlist 摘要 | 否 |
| Database Invariant Check | `scripts/admin/check_database_invariants.py` | Admin diagnostics | 唯讀檢查正式 SQLite DB invariant | 否 |
| Windows Version Resource Builder | `scripts/admin/windows_version_resource.py` | Admin packaging | 產生 Windows version resource | 否 |
| Frozen Updater Smoke | `scripts/admin/smoke_frozen_updater.py` | Admin smoke | 驗證 frozen updater 替換 app files、保留 data 並清理 handoff | 否 |
| macOS App Launcher Builder | `scripts/admin/create_macos_app_launcher.py` | Admin packaging | 建立 macOS `.app` native launcher | 否 |
| Relogin Flow Smoke | `scripts/admin/smoke_relogin_flow.py` | Admin smoke | 使用隔離暫存資料驗證重新登入警告與 launcher login gate | 否 |
| Capture Posts Target | `scripts/debug/capture_posts_target.py` | Debug | 開啟瀏覽器擷取目前社團頁作為 posts target | 否 |
| One-shot Scan | `scripts/debug/one_shot_scan.py` | Debug | 對已保存 target 執行一次 one-shot 掃描 | 否 |
| Worker Probe | `scripts/debug/worker_probe.py` | Debug | DB-free headless extractor probe；不跑正式 scan pipeline | 否 |
| Text Newline Probe | `scripts/debug/text_newline_probe.py` | Debug | 檢查可見 Facebook DOM 文字是否仍能取得換行資訊；不跑正式 scan pipeline | 否 |
| One-shot Scheduler | `scripts/internal/one_shot_scheduler.py` | Internal | 直接啟動 one-shot debug/fallback scheduler loop，不作正式主路徑保證 | 否 |
| Resident Main | `scripts/internal/resident_main.py` | Internal | 直接啟動正式 async resident main worker loop | 否 |
| uv wrapper | `scripts/uv.ps1` | 指令 wrapper | 固定從專案根目錄執行 uv，並使用工作區內 cache | 否，wrapper |

Release / packaging scripts 的細節放在 `packaging/README.md`。本表只作工具索引；
是否可稱為 upload-ready 需以 `packaging/README.md#驗證` 的 release checklist 為準。

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
.\scripts\uv.ps1 run python .\scripts\admin\release_validation.py
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

### 驗證分級與回報用語

| 層級 | 何時使用 | 標準命令 / 來源 | 可回報用語 | 不可回報成 |
|---|---|---|---|---|
| 快速 / 聚焦檢查 | 一般開發、文件整理、窄範圍修正 | 受影響 pytest、ruff / mypy、JS syntax、compileall 或 probe | 「快速 / 聚焦檢查通過」，並列出實際命令 | 上傳完整檢查、CI 通過 |
| 本機上傳前完整檢查 | 上傳前或使用者要求完整/CI 對齊檢查 | `release_validation.py`；符合條件時才可加 `--skip-sync` | 「本機上傳前完整檢查通過」 | GitHub CI 通過 |
| GitHub CI | GitHub Actions 對該 commit / PR 實際完成且綠燈 | `.github/workflows/ci.yml` | 「GitHub CI 通過」 | 只靠本機結果宣稱 CI 通過 |
| Release validation | release 前本機檢查，預設含 audit | `scripts/admin/release_validation.py` | 「通過」；skip flags 需明列 | 可上傳 artifact 檢查 |
| 上傳前 release asset 檢查 | Release asset 上傳前 | manifest、artifact、frozen / 人工 smoke | 「上傳前 release asset 檢查通過」 | pre-finalize validation |

| 略過旗標 / 狀態 | 允許用途 | 回報限制 |
|---|---|---|
| `--skip-sync` | 環境已同步，且 dependency / `uv.lock` / workflow / 驗證腳本未變更 | 必須明講已略過 sync |
| `--skip-audit` | 離線或臨時非 audit 重現 | 只能稱快速 / 臨時檢查 |
| `--skip-release-validation` | build 中間階段快速產物驗證 | 不可稱可上傳 |
| `--skip-artifact-manifest` | manifest finalize 前的 pre-finalize artifact 檢查 | 不可稱可上傳 |
| 未做人工 smoke | Facebook login / metadata resolver / posts-comments scan / notification 尚未人工驗 | 不可宣稱完整 release smoke 完成 |

常用驗證命令：

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

CI 使用固定的 `uv==0.9.0` 搭配 locked sync，並維持 report-only complexity
summary、Playwright Chromium 安裝、lint、type check、static JS syntax check、
pytest coverage 與 dependency audit。
完整順序與命令以 `.github/workflows/ci.yml` 為準，避免文件複製 workflow 細節後
drift。
80% coverage 是目前完整測試可通過的 baseline，只防止覆蓋率意外大幅倒退；
後續新增測試後再逐步提高，不用為了既有大型模組一次重寫測試。
本機開發可使用較新的 uv；固定 CI 版本只是讓 GitHub Actions 的 resolver /
installer 行為可重現。回報驗證時，若沒有實際跑 GitHub Actions，只能寫
「本機上傳前完整檢查」或「快速 / 聚焦檢查」。

Complexity Report 是人工審查前置資料，不是 release validation 必跑項，
也不會因 CCN / NLOC 排名造成失敗。CI 只會用 report-only step 把 Markdown
摘要寫入 GitHub Actions summary，供 review 時判斷本次變更是否需要拆分；
它不是 hard gate。
預設會使用 `docs/maintainability_annotations.json` 將已人工確認合理的大型檔案
列到 known-large section，避免它們長期佔住主排行；known-large 不是永久豁免，
若相關檔案被修改仍應重新 review。
若要查看純排名可加 `--no-annotations`，若要把 known-large 放回主排行可加
`--include-known-large`。

Release tag 前建議執行：

```powershell
.\scripts\uv.ps1 run python scripts\admin\release_validation.py
.\scripts\uv.ps1 run python scripts\admin\release_validation.py --include-artifacts
.\scripts\uv.ps1 run python scripts\admin\release_validation.py --include-artifacts --artifact-platform macos-arm64
```

腳本會輸出 OS、Python、uv、git commit 與每個驗證 command 結果。
只有在環境已同步，且 dependency、`uv.lock`、workflow 或驗證腳本沒有變更時，
才可加 `--skip-sync`；預設會跑 dependency advisory 檢查，只有離線或刻意重現
非 audit 檢查時才加 `--skip-audit`。
artifact 參數、manifest 驗證、platform zip 檢查與 frozen updater smoke 細節集中在
`packaging/README.md#驗證`。

版本與 Web asset cache key 的來源語義看 `docs/ARCHITECTURE.md#frozen-updater`；
release validation 會印出目前 app / asset version，正式發佈時以升版作為
瀏覽器 cache busting 來源。

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
$update_webapp_routes = @(
  "src\facebook_monitor\webapp\routes\settings.py",
  "src\facebook_monitor\webapp\routes\settings_diagnostics_routes.py",
  "src\facebook_monitor\webapp\routes\settings_preferences_routes.py",
  "src\facebook_monitor\webapp\routes\settings_profile_routes.py"
)
.\scripts\uv.ps1 run mypy src\facebook_monitor\updates src\facebook_monitor\updater.py $update_webapp_routes
.\scripts\uv.ps1 run ruff check `
  src\facebook_monitor\updates `
  src\facebook_monitor\updater.py `
  $update_webapp_routes `
  tests\updates `
  tests\webapp\test_app.py
Get-ChildItem -Path src\facebook_monitor\webapp\static -Filter *.js -Recurse | ForEach-Object { node --check $_.FullName }
```

打包後的非互動 updater smoke 與人工 Web UI / tray checklist 放在 `packaging/README.md#驗證`。
