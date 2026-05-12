# Tooling

本文件是 scripts / CLI 工具索引。README 只保留正式日常入口；架構語義看 `docs/ARCHITECTURE.md`。

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
| Capture Posts Target | `scripts/debug/capture_posts_target.py` | Debug | 開啟瀏覽器擷取目前社團頁作為 posts target | 否 |
| One-shot Scan | `scripts/debug/one_shot_scan.py` | Debug | 對已保存 target 執行一次 one-shot 掃描 | 否 |
| Worker Probe | `scripts/debug/worker_probe.py` | Debug | 使用專用 profile 執行背景掃描可行性 probe | 否 |
| Extractors Probe Helper | `scripts/debug/extractors_probe.py` | Debug helper | 重新匯出 extractor probe 需要的正式 package API | 否 |
| Notifications Probe Helper | `scripts/debug/notifications_probe.py` | Debug helper | 重新匯出 ntfy probe 需要的正式通知 API | 否 |
| One-shot Scheduler | `scripts/internal/one_shot_scheduler.py` | Internal | 直接啟動 one-shot debug/fallback scheduler loop，不追求正式主路徑 parity | 否 |
| Resident Main | `scripts/internal/resident_main.py` | Internal | 直接啟動正式 async resident main worker loop | 否 |
| uv wrapper | `scripts/uv.ps1` | Tooling | 固定從專案根目錄執行 uv，並使用工作區內 cache | 是，指令 wrapper |

## 常用指令

正式入口：

```powershell
.\scripts\uv.ps1 run facebook-monitor
.\scripts\uv.ps1 run facebook-monitor-login
.\scripts\uv.ps1 run facebook-monitor --data-dir "D:\fb_monitor_data"
.\scripts\uv.ps1 run facebook-monitor --data-dir "D:\fb_monitor_data" --port 4818 --no-open-browser
.\scripts\uv.ps1 run facebook-monitor --data-dir "D:\fb_monitor_data" --profile-name phase0_default
.\scripts\uv.ps1 run facebook-monitor-login --data-dir "D:\fb_monitor_data" --profile-name phase0_default
```

低頻工具：

```powershell
.\scripts\uv.ps1 run python .\scripts\admin\console.py
.\scripts\uv.ps1 run python .\scripts\admin\manage_targets.py
.\scripts\uv.ps1 run python .\scripts\debug\one_shot_scan.py --group-id "<group_id>" --scroll-rounds 3
.\scripts\uv.ps1 run python .\scripts\internal\resident_main.py --max-cycles 2 --interval-seconds 1
.\scripts\uv.ps1 run python .\scripts\admin\console.py --data-dir "D:\fb_monitor_data"
```

驗證：

```powershell
.\scripts\uv.ps1 run pytest -q
.\scripts\uv.ps1 run mypy
.\scripts\uv.ps1 run pytest tests\core --cov=facebook_monitor.core --cov-report=term-missing -q
.\scripts\uv.ps1 run python -m compileall -q src scripts tests
.\scripts\uv.ps1 run ruff check src scripts tests
.\scripts\uv.ps1 run pip-audit
git diff --check
```

## 啟動診斷位置

- runtime info：`<data-dir>\runtime\server.json`
- startup diagnostics：`<data-dir>\logs\startup.log`
- app log：`<data-dir>\logs\app.log`
- error log：`<data-dir>\logs\error.log`

詳細 launcher / resource lock / startup semantics 看 `docs/ARCHITECTURE.md#正式入口`。
