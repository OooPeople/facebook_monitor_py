# Facebook 監視器 Python 版

本專案把既有 Facebook userscript 監視行為，逐步搬遷成 Python + Playwright 版本，目標是讓社團貼文監視能在背景運作，不需要維持前景 Facebook 視窗。

`reference/` 保存 JS userscript 參考；重要功能實作前應先對照 `reference/src/facebook_group_refresh.user.js`。目前進度與下一步以 `docs/TASK_BREAKDOWN.md` 為準。

## 快速開始

本專案使用 `uv` 管理環境。Windows PowerShell 請優先使用專案 wrapper：

```powershell
.\scripts\uv.ps1 sync
.\scripts\uv.ps1 run playwright install chromium
```

啟動本機 Web UI：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase_b_webui.py
```

若要把 SQLite DB 與 Playwright profile 放到 SSD 或其他資料碟，可直接指定路徑：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase_b_webui.py --db-path "D:\fb_monitor_data\app.db" --profile-dir "D:\fb_monitor_data\profiles\phase0_default"
```

開啟 `http://127.0.0.1:8765` 後，可新增 / 刪除 posts 或 comments target、編輯關鍵字與通知設定，並用每個 target 卡片的「開始 / 停止」控制監視。新增 target 時只要貼上社團首頁或單篇貼文 URL，系統會自動判斷是社團貼文監視或留言監視。關鍵字、掃描間隔與通知設定是社團層級設定；同一社團的 posts/comments target 會共用同一份設定，但 seen、最近掃描、歷史與 runtime state 仍各 target 獨立。背景掃描服務會隨 Web UI 啟動，主畫面不再提供全域 scheduler 啟停開關。

Web UI 啟動時預設會清除上一輪可重建的 runtime / debug data：`scan_runs`、`latest_scan_items`、`match_history`、`notification_events`、`seen_items`。target、社團層級設定、通知設定與 profile 不會被清除。本次執行期間仍會保留資料供 debug；若某次啟動想保留前次 runtime data，可加 `--keep-runtime-data-on-startup`。

右上角「設定」頁可管理 automation profile，也可保存通知預設值、發送測試通知，並把通知預設值套用到所有社團設定。

若要改用一次性掃描 fallback：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase_b_webui.py --auto-scan-mode one-shot
```

`one-shot` 只作為 fallback / debug tooling；正式產品語義以 async resident 背景掃描為準，新功能預設不要求 fallback/debug path parity。

## 常用指令

初次登入 / 檢查 automation profile：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase0_setup_login.py
```

互動式 console 入口：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase_b_console.py
```

單輪 worker 掃描：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase_b_worker_once.py --group-id "<group_id>" --scroll-rounds 3
```

獨立 scheduler / resident worker 除錯：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase_c_scheduler.py --max-cycles 1
.\scripts\uv.ps1 run python .\scripts\phase_c_resident_worker.py --max-cycles 2 --interval-seconds 1
```

## 文件職責

- `AGENTS.md`：協作規則與 JS 移植規範。
- `docs/TASK_BREAKDOWN.md`：唯一的目前進度、下一步、風險與不做事項來源。
- `docs/HANDOFF.md`：新對話 / 下一位 agent 接手時的最小摘要，不重複完整進度。
- `docs/facebook_python_migration_plan.md`：長期遷移與架構計劃，不追逐每日進度。
- `comments_phase_entry_checklist.md`：comments phase 開工 gate、D1-D4 phase 規格與驗收標準。
- `docs/PHASE0_SPIKE.md`：Phase 0 probe 歷史、步驟與失敗分類。
- `docs/REFERENCE_MAP.md`：`reference/` 內 JS 參考資料對照。

## 重要路徑

- `src/facebook_monitor/core/`：純資料模型、keyword、dedupe、refresh policy。
- `src/facebook_monitor/application/`：application service 與 context wiring。
- `src/facebook_monitor/persistence/`：SQLite schema、repositories 與 runtime data maintenance。
- `src/facebook_monitor/facebook/`：Facebook route、DOM extractor、permalink、sort / scroll helpers。
- `src/facebook_monitor/worker/`：one-shot 與 resident worker。
- `src/facebook_monitor/scheduler/`：多 target 排程 loop。
- `src/facebook_monitor/notifications/`：desktop / ntfy / Discord sender 與 dispatcher。
- `src/facebook_monitor/webapp/`：FastAPI Web UI、templates、read model。
- `scripts/`：probe、manual tools、CLI runner。
- `tests/`：依正式模組分類的 pytest 測試，不再以 Phase A/B/C 命名。
- `data/profiles/`：專用 automation profile，不可 commit 真實資料。
- `logs/`：本機 runtime log，不可 commit 私人資料。

## 本地驗證

```powershell
.\scripts\uv.ps1 run pytest -q
.\scripts\uv.ps1 run python -m py_compile .\scripts\phase0_setup_login.py .\scripts\phase0_worker_probe.py .\scripts\phase0_extractors.py .\scripts\phase0_notifications.py .\scripts\phase_b_console.py .\scripts\phase_b_capture_group_posts.py .\scripts\phase_b_manage_targets.py .\scripts\phase_b_worker_once.py .\scripts\phase_b_webui.py .\scripts\phase_c_scheduler.py .\scripts\phase_c_resident_worker.py .\src\facebook_monitor\core\models.py .\src\facebook_monitor\persistence\sqlite.py .\src\facebook_monitor\persistence\maintenance.py .\src\facebook_monitor\application\services.py .\src\facebook_monitor\application\context.py .\src\facebook_monitor\automation\profile_lease.py .\src\facebook_monitor\facebook\route_detection.py .\src\facebook_monitor\facebook\collection_policy.py .\src\facebook_monitor\facebook\feed_extractor.py .\src\facebook_monitor\facebook\feed_dom.py .\src\facebook_monitor\facebook\permalink.py .\src\facebook_monitor\facebook\group_metadata.py .\src\facebook_monitor\facebook\sort_controls.py .\src\facebook_monitor\facebook\scroll_controls.py .\src\facebook_monitor\notifications\desktop.py .\src\facebook_monitor\notifications\discord.py .\src\facebook_monitor\notifications\dispatcher.py .\src\facebook_monitor\notifications\ntfy.py .\src\facebook_monitor\worker\async_resident.py .\src\facebook_monitor\worker\resident_queue.py .\src\facebook_monitor\worker\resident_page_pool.py .\src\facebook_monitor\worker\resident_executor.py .\src\facebook_monitor\worker\group_posts.py .\src\facebook_monitor\worker\runner.py .\src\facebook_monitor\worker\resident.py .\src\facebook_monitor\scheduler\loop.py .\src\facebook_monitor\webapp\app.py .\src\facebook_monitor\webapp\query_service.py .\src\facebook_monitor\webapp\schemas.py .\src\facebook_monitor\webapp\profile_session.py .\src\facebook_monitor\webapp\scheduler_session.py
```

## 安全規則

- 不使用使用者日常 Chrome profile。
- 不 commit cookies、tokens、session dumps、真實 profile data 或私人 logs。
- 新功能若牽涉 JS 已成熟行為，先對照 JS helper chain，再實作 Python 版。
