# Phase 0 Spike（封存）

> 文件狀態：  
> 本文件已封存，只保留 Phase 0 可行性驗證、probe 步驟與早期失敗分類。  
> 目前實作狀態、下一步與風險以 `../TASK_BREAKDOWN.md` 為準；穩定架構事實以 `../ARCHITECTURE.md` 為準。

## 目的

Phase 0 只回答一個問題：

Python + Playwright worker 能否在不需要前景 Facebook 視窗的情況下，於背景監視一個 Facebook 社團貼文 target？

如果答案是否定的，暫時不要建立完整 Python 架構。

## 測試形狀

這個 spike 必須刻意保持很小：

- 一個專用 automation profile。
- 一個 group posts target。
- headed setup 負責登入與手動切到目標頁面。
- headless worker probe 使用同一個 profile。
- 最小抽取內容：目前 URL、title、body size；加入 selector 後，再抽取少量可見且類似貼文的 items。
- 最小 log 輸出，放在 `logs/`。

在可行性被證明前，不要加入 comments monitoring、SQLite、管理 UI 或 multi-target scheduling。

## uv 初始指令

本專案使用 `uv` 管理環境。Windows PowerShell 請優先使用專案 wrapper：

```powershell
.\scripts\uv.ps1 sync
.\scripts\uv.ps1 run playwright install chromium
```

執行 headed setup：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase0_setup_login.py
```

登入並切到目標社團頁面後，在 terminal 按 Enter 關閉 setup。

執行 headless probe：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase0_worker_probe.py "https://www.facebook.com/groups/<group_id>"
```

可選參數：

- `--include "keyword"`：加入 include keyword，可重複指定，也可用逗號分隔。
- `--max-items 12`：限制本輪最多抽取的貼文候選數。
- `--scroll-rounds 3`：捲動 feed 並多輪收集貼文候選。
- `--scroll-wait-ms 2500`：每輪捲動後等待 DOM 載入的毫秒數。
- `--reset-seen`：重置 Phase 0 本機 seen key store。
- `--duration-minutes 120`：連續執行指定分鐘數，時間到自動停止。
- `--interval-seconds 300`：長測模式下每輪掃描間隔秒數。
- `--diagnostics`：輸出每輪捲動後的匿名 DOM 統計，用於調整 extractor。
- `--ntfy-topic phase0test`：指定 ntfy topic。
- `--notify-test`：只送一則 ntfy 測試通知後結束。
- `--notify-on-new`：偵測到新的 seen-item hash 時送出 ntfy 摘要通知。

Phase 0 seen key store 只保存 hash，位置在 `data/runtime/phase0_seen_keys.json`，此路徑不應提交到 git。

目前 Phase 0 script 邊界：

- `scripts/phase0_setup_login.py`：有視窗登入 / setup probe。
- `scripts/phase0_worker_probe.py`：無頭 worker orchestration、seen store、長測與通知觸發。
- `scripts/phase0_extractors.py`：貼文候選抽取、scroll 診斷、hash key 與 keyword match。
- `scripts/phase0_notifications.py`：ntfy HTTP 發送。

Phase B 起新增 `scripts/phase_b_console.py` 作為單一互動入口，內含新增 target、編輯設定與執行一次正式 worker scan。Phase B.5 新增 `scripts/phase_b_webui.py` 作為最小本機 FastAPI Web UI。細分的 `scripts/phase_b_capture_group_posts.py`、`scripts/phase_b_manage_targets.py`、`scripts/phase_b_worker_once.py` 保留供除錯與自動化。這不取代 Phase 0 probe；Phase 0 probe 仍用來驗證 headless 可行性與 extractor 行為。

ntfy 測試範例：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase0_worker_probe.py --ntfy-topic phase0test --notify-test
```

掃描通知測試範例：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase0_worker_probe.py "https://www.facebook.com/groups/<group_id>" --scroll-rounds 5 --max-items 20 --reset-seen --notify-on-new --ntfy-topic phase0test
```

長時間測試範例：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase0_worker_probe.py "https://www.facebook.com/groups/<group_id>" --scroll-rounds 3 --max-items 12 --duration-minutes 120 --interval-seconds 300
```

## 通過標準

Phase 0 通過條件：

- headless probe 可以重用 headed setup profile。
- setup 關閉後，target page 仍可被開啟。
- worker 重新啟動後，仍可開啟 target page。
- 後續版本的 probe 可以連續執行 2-4 小時，且不需要前景 Facebook 視窗。
- logs 提供足夠資訊，可用來診斷失敗原因。

## 失敗分類

每次執行失敗時，請分類為以下其中之一：

- `profile_missing`
- `profile_locked`
- `login_required`
- `checkpoint_or_verification`
- `headless_dom_mismatch`
- `page_load_timeout`
- `extractor_empty`
- `notification_failed`
- `unknown`

在主要失敗模式被理解前，不要進入完整架構實作。

## Phase 0 結果摘要

目前 Phase 0 已證明 headed setup profile 可被 headless worker 重用，並完成 2 小時背景測試：24 輪成功、0 輪失敗。ntfy 測試通知與 new-item 掃描摘要通知也已由使用者確認收到。

後續正式資料模型、SQLite persistence 與 target capture 已移到 Phase A/B，相關進度請看 `docs/TASK_BREAKDOWN.md`。
