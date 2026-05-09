# Tooling

本文件是 scripts / CLI 工具的唯一索引。README 只保留正式日常入口；本文件負責列出低頻管理、debug 與 internal 工具。

## 入口原則

- 正式日常入口只有 Web UI。
- profile 登入 / 檢查屬於正式維運入口。
- debug / admin / internal 工具不得被文件描述成日常入口。
- 新功能預設先接正式 Web UI + resident main 主路徑；debug / fallback 工具只有在有實際維護價值時才跟進。
- 不再新增 `phase_*` 命名的 script。

## 工具清單

| 工具 | 路徑 | 角色 | 用途 | 正式入口 |
|---|---|---|---|---|
| Web UI | `scripts/start/webui.py` | Start | 日常 target 管理、設定與背景掃描 | 是 |
| Setup Login | `scripts/start/setup_login.py` | Start | 開啟專用 automation profile，供登入與檢查 session | 是，維運入口 |
| Admin Console | `scripts/admin/console.py` | Admin | 互動式管理 target、設定與一次性掃描 | 否 |
| Manage Targets | `scripts/admin/manage_targets.py` | Admin | 只編輯 target 設定與啟停狀態 | 否 |
| Capture Posts Target | `scripts/debug/capture_posts_target.py` | Debug | 開啟瀏覽器擷取目前社團頁作為 posts target | 否 |
| One-shot Scan | `scripts/debug/one_shot_scan.py` | Debug | 對已保存 target 執行一次 one-shot 掃描 | 否 |
| Worker Probe | `scripts/debug/worker_probe.py` | Debug | 使用專用 profile 執行背景掃描可行性 probe | 否 |
| Extractors Probe Helper | `scripts/debug/extractors_probe.py` | Debug helper | 重新匯出 extractor probe 需要的正式 package API | 否 |
| Notifications Probe Helper | `scripts/debug/notifications_probe.py` | Debug helper | 重新匯出 ntfy probe 需要的正式通知 API | 否 |
| One-shot Scheduler | `scripts/internal/one_shot_scheduler.py` | Internal | 直接啟動 one-shot fallback scheduler loop | 否 |
| Resident Main | `scripts/internal/resident_main.py` | Internal | 直接啟動 resident main worker loop | 否 |
| uv wrapper | `scripts/uv.ps1` | Tooling | 固定從專案根目錄執行 uv，並使用工作區內 cache | 是，指令 wrapper |

## 常用指令

```powershell
.\scripts\uv.ps1 run python .\scripts\start\webui.py
.\scripts\uv.ps1 run python .\scripts\start\setup_login.py
.\scripts\uv.ps1 run python .\scripts\debug\one_shot_scan.py --group-id "<group_id>" --scroll-rounds 3
.\scripts\uv.ps1 run python .\scripts\internal\resident_main.py --max-cycles 2 --interval-seconds 1
```
