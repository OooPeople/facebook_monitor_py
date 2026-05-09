# Facebook 監視器 Python 版

本專案把既有 Facebook userscript 監視行為，逐步搬遷成 Python + Playwright 版本，目標是讓社團貼文監視能在背景運作，不需要維持前景 Facebook 視窗。

`reference/` 保存 JS userscript 參考；重要功能實作前應先對照 `reference/src/facebook_group_refresh.user.js`。目前進度與下一步以 `docs/TASK_BREAKDOWN.md` 為準，穩定架構事實以 `docs/ARCHITECTURE.md` 為準。

## 快速開始

本專案使用 `uv` 管理環境。Windows PowerShell 請優先使用專案 wrapper：

```powershell
.\scripts\uv.ps1 sync
.\scripts\uv.ps1 run playwright install chromium
```

啟動本機 Web UI：

```powershell
.\scripts\uv.ps1 run python .\scripts\start\webui.py
```

若要把 SQLite DB 與 Playwright profile 放到 SSD 或其他資料碟，可直接指定路徑：

```powershell
.\scripts\uv.ps1 run python .\scripts\start\webui.py --db-path "D:\fb_monitor_data\app.db" --profile-dir "D:\fb_monitor_data\profiles\automation_default"
```

開啟 `http://127.0.0.1:8765` 後，可新增 / 刪除 posts 或 comments target、編輯關鍵字與通知設定，並用每個 target 卡片的「開始 / 停止」控制監視。新增 target 時只要貼上社團首頁或單篇貼文 URL，系統會自動判斷是社團貼文監視或留言監視。關鍵字、掃描間隔與通知設定是社團層級設定；同一社團的 posts/comments target 會共用同一份設定，但 seen、最近掃描、歷史與 runtime state 仍各 target 獨立。背景掃描服務會隨 Web UI 啟動，主畫面不再提供全域 scheduler 啟停開關。

Web UI 啟動時預設會清除上一輪可重建的 runtime / debug data：`scan_runs`、`latest_scan_items`、`match_history`、`notification_events`、`seen_items`。target、社團層級設定、通知設定與 profile 不會被清除。本次執行期間仍會保留資料供 debug；若某次啟動想保留前次 runtime data，可加 `--keep-runtime-data-on-startup`。

右上角「設定」頁可管理 automation profile，也可保存通知預設值、發送測試通知，並把通知預設值套用到所有社團設定。

`one-shot` 只作為 fallback / debug tooling，不能從 Web UI 日常啟動參數切換；正式產品語義以 resident main 背景掃描為準，新功能預設不要求 fallback/debug path parity。

## 維運與除錯

初次登入 / 檢查 automation profile：

```powershell
.\scripts\uv.ps1 run python .\scripts\start\setup_login.py
```

其他低頻管理與除錯工具請看 `docs/tooling.md`。常用範例：

```powershell
.\scripts\uv.ps1 run python .\scripts\admin\console.py
.\scripts\uv.ps1 run python .\scripts\debug\one_shot_scan.py --group-id "<group_id>" --scroll-rounds 3
```

## 文件職責

- `AGENTS.md`：協作規則與 JS 移植規範。
- `docs/TASK_BREAKDOWN.md`：唯一的目前進度、下一步、風險與不做事項來源。
- `docs/ARCHITECTURE.md`：目前穩定架構事實、正式主路徑、模組職責與 deferred 邊界。
- `docs/tooling.md`：scripts / CLI 工具角色、路徑與是否為正式入口。
- `docs/HANDOFF.md`：新對話 / 下一位 agent 接手時的最小摘要，不重複完整進度。
- `docs/REFERENCE_MAP.md`：`reference/` 內 JS 參考資料對照。
- `docs/archive/`：早期遷移計畫與可行性 spike 等歷史文件；非日常閱讀路徑。

## 重要路徑

- `src/facebook_monitor/core/`：純資料模型、keyword、dedupe、refresh policy。
- `src/facebook_monitor/application/`：application service 與 context wiring。
- `src/facebook_monitor/persistence/`：SQLite schema、repositories 與 runtime data maintenance。
- `src/facebook_monitor/facebook/`：Facebook route、DOM extractor、permalink、sort / scroll helpers。
- `src/facebook_monitor/worker/`：resident main、fallback/debug workers、posts/comments pipelines 與 shared scan finalize。
- `src/facebook_monitor/scheduler/`：target planner、runtime recovery 與 one-shot fallback scheduler。
- `src/facebook_monitor/notifications/`：desktop / ntfy / Discord sender、channel dispatch、outbox service 與 manual test。
- `src/facebook_monitor/webapp/`：FastAPI Web UI、routes、templates、read model 與 presenters。
- `scripts/start/`：正式日常入口與 profile setup。
- `scripts/admin/`：低頻管理工具。
- `scripts/debug/`：probe、capture、one-shot 等除錯工具。
- `scripts/internal/`：直接啟動 scheduler / resident main 的內部工具。
- `tests/`：依正式模組分類的 pytest 測試，不再使用歷史階段命名。
- `data/profiles/`：專用 automation profile，不可 commit 真實資料。
- `logs/`：本機 runtime log，不可 commit 私人資料。

## 本地驗證

```powershell
.\scripts\uv.ps1 run pytest -q
.\scripts\uv.ps1 run python -m compileall -q src scripts
```

## 安全規則

- 不使用使用者日常 Chrome profile。
- 不 commit cookies、tokens、session dumps、真實 profile data 或私人 logs。
- 新功能若牽涉 JS 已成熟行為，先對照 JS helper chain，再實作 Python 版。
