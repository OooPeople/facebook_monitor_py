# 交接摘要

本文件只保存新對話或下一位 agent 接手所需的最小資訊。完整進度、下一步與不做事項只看 `docs/TASK_BREAKDOWN.md`；長期設計只看 `docs/facebook_python_migration_plan.md`。

## 接手順序

1. 讀 `AGENTS.md`，確認 JS 移植規則與協作規範。
2. 讀 `docs/TASK_BREAKDOWN.md`，取得目前狀態、下一步與風險。
3. 需要長期架構時讀 `docs/facebook_python_migration_plan.md`。
4. 若任務涉及 comments，先讀 `comments_phase_entry_checklist.md`，不可跳過 Gate 0 直接實作 comments helper。
5. 需要 Phase 0 probe 歷史時讀 `docs/PHASE0_SPIKE.md`。

## 啟動與使用

- Web UI 入口：`.\scripts\uv.ps1 run python .\scripts\phase_b_webui.py`
- automation profile：`data/profiles/phase0_default`
- Web UI 啟動時會先停止所有 target；本次 session 需由使用者手動按 target 卡片「開始」。
- Web UI 啟動時預設清除上一輪 runtime/debug data：`scan_runs`、`latest_scan_items`、`match_history`、`notification_events`、`seen_items`；target/group config/通知設定/profile 不會被清除。若要保留前次 runtime data，啟動時加 `--keep-runtime-data-on-startup`。
- 背景掃描服務隨 Web UI 啟動；主畫面不提供全域 scheduler 啟停按鈕。
- 新增 target 時只貼 Facebook URL，不需要手動選 posts/comments；社團首頁 URL 會建立 posts target，單篇社團貼文 URL 會建立 comments target。未填自訂名稱時會用 automation profile 解析社團名稱。
- target「開始」會清該 target seen scope、要求立即掃描並喚醒背景 scheduler；「停止」只暫停排程並保留 seen/history。
- 設定頁開啟 automation profile 或自動解析社團名稱時，Web UI 會內部暫停 / 恢復背景 scheduler，避免 profile lease 衝突。

## 目前主幹

- Python 3.13、uv、Playwright、SQLite、FastAPI Web UI。
- async resident worker 是正式產品主路徑；one-shot mode 與 sync resident worker 只作為 fallback / debug tooling。
- 新增功能預設只要求 async resident 主路徑完整接上；one-shot / sync resident 只有在該功能對 debug path 必要時才同步跟進。
- resident scheduler / executor 已改為 queue-based continuous executor：
  - `scheduler/planner.py`：target-level independent schedule planner。
  - `worker/async_resident.py`：Playwright persistent context lifecycle 與 scheduler tick 接線。
  - `worker/resident_queue.py`：TargetQueue 與 queued/running 去重。
  - `worker/resident_page_pool.py`：async page pool 與 page ownership diagnostics。
  - `worker/resident_executor.py`：ExecutorWorkerPool、scan guard、runtime/page diagnostics。
- Web UI topbar 會顯示 running / queued / slots / pages / opened / reused / closed / browser alive；queued target 會顯示 `queue_position`，scan diagnostics 會顯示 `last_page_reloaded_at`。
- Gate 0 已補自動測試證據：async resident concurrency / queued guard、同 target page reuse + reload、Web UI page counters、skip reason 顯示、posts auto load more diagnostics、通知預設值保存 / 測試 / 套用。
- 設定模型已收斂為 JS 語義：keyword / refresh / notification 是 group-scoped config；posts/comments target 的 seen、latest scan、history、runtime state 仍是 target-scoped。舊 `target_configs` 只作為既有 DB migration fallback，正式路徑只讀寫 `group_configs`。
- `target_configs` 已降級為 migration-only：新正式功能不得直接讀寫，repository 只保留 `*_legacy_*_for_migration` 方法給舊資料遷移與測試準備。
- `upsert_*` request 已能區分「省略欄位」與「明確 false / 空值 / None」：CLI capture 省略設定時保留既有 group config，Web UI 明確送出的關閉/清空可覆寫既有設定。
- Python 版預設值集中於 `core/defaults.py`；不要在 Web UI、service 或 worker 另寫一套 auto sort / notification / scan count 預設。
- 使用者已實測確認：右側最近掃描貼文面板不再撐長 target 卡片、queue/running 顯示正常、auto load more 可穩定取得 10 篇、通知預設與通知功能正常。
- tests 已整理成模組分類目錄，不再使用 Phase A/B/C 測試檔名；pytest 入口仍是 `.\scripts\uv.ps1 run pytest -q`。
- `scripts/` 尚未搬成子資料夾；保留 `phase_*` 檔名作為相容 CLI 入口。若要整理，先加 wrapper / common defaults，不直接破壞使用者啟動指令。

## 易誤判事項

- comments D1-D4 已完成程式接線：`TargetDescriptor.for_comments(...)`、`CreateCommentsTargetRequest`、`upsert_comments_target(...)`、單篇貼文 URL 解析、Web UI URL 自動判斷新增 comments、comments extractor、comment id / canonical permalink、文字清理、comments dedupe aliases、seen/history/latest scan/notification persistence、comment sort、comment nested scroll/load-more、comment-specific guard、mutation relevance helper，以及 Web UI 開始 / 停止與 async resident executor 派發。
- comments target 預設 paused；使用者按 target 卡片「開始」後會清該 comments scope seen、要求立即掃描並喚醒 scheduler。
- comments 已接上正式 async resident 主路徑，但尚未經使用者真實 Facebook DOM 實測；不可在實測前宣稱已通過 comments end-to-end 驗收。
- 後續 comments phase 仍需維持 JS 語義：group-scoped config / target-scoped seen；目前 D1-D4 已打通 target-scoped `scope_id` 與 group config 共用。
- `auto_load_more` posts 目前只完成 scroll 模式，`loadMoreMode=wheel` 仍 deferred；comments nested scroll/load-more 已完成程式接線，但仍待真實 DOM 驗收。
- 不要直接移植 JS 版「第一則貼文與前次相同就跳過深度掃描」最佳化；置頂 / 管理員貼文會讓 top item 不等於最新貼文，目前明確暫不實作。
- `phase_offset_sec` 暫不實作；未來 target 數量增加或同時 due 造成壓力時，優先做系統自動分散，不先做使用者手動欄位。
- D3 已補 comment-specific load-more guard；D4 已把 comments latest scan / diagnostics 顯示接入 UI，下一步需用真實 comments target 驗證 guard / sort / nested scroll 診斷是否足夠。
- Python 版刻意保留部分預設值與 JS 不同：`auto_adjust_sort=False`、`enable_desktop_notification=False` 是 deliberate divergence，原因是避免新增 target 後立即改 Facebook 排序或跳桌面通知；使用者可在 Web UI 啟用。
- `_create_*` target service 只保留為 internal helper；正式互動入口一律走 `upsert_*`，不得新增正式 call site 使用 `_create_*`。
- Deferred 項目不可半補：`loadMoreMode=wheel`、`phase_offset_sec`、one-shot scheduler queue 化、fallback/debug comments parity、DOM helper 拆分若要開工，都必須作為完整子任務處理，不能只補 UI 欄位或單一 helper。
- `persistence/sqlite.py` 與 `TargetApplicationService` 是下一階段 P1 架構風險；新增 comments schema、richer diagnostics 或更多 use case 前，先考慮拆 schema / repositories 與 runtime / notification / scan recording services。
- 若遇到作者 unknown、文字空白、誤抓留言或未取得連結，先請使用者貼單筆貼文除錯與整輪掃描診斷，再小步修改 `feed_dom.py`。
- 不要啟動 Web UI、background worker 或 browser 實測，除非使用者明確同意。

## 驗證

```powershell
.\scripts\uv.ps1 run pytest -q
.\scripts\uv.ps1 run python -m compileall -q src scripts
```
