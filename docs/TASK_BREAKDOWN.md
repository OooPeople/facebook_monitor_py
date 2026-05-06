# 任務拆解

本文件是目前進度、下一步、風險與不做事項的唯一來源。交接摘要看 `docs/HANDOFF.md`；長期設計看 `docs/facebook_python_migration_plan.md`。

## 目前狀態

### 已完成

- Phase 0：專用 automation profile 可被 worker 重用，並完成長時間背景測試與 ntfy 測試。
- Phase A：正式 package、domain models、SQLite persistence、application service/context 與 pytest 測試。
- Phase B：Facebook group posts target capture、console 管理入口、one-shot worker 與 route detection。
- Phase B.5：FastAPI Web UI，可新增 / 刪除 target、編輯 target 設定、顯示最近掃描貼文與診斷，主操作為每個 target 卡片的「開始 / 停止」。
- Phase C：Web UI 內建背景掃描服務、async resident 正式主路徑、one-shot / sync resident fallback。
- Scheduler rewrite C1-C3：resident 主路徑已改為 queue-based continuous executor；使用者已實測 `max_concurrent_scans=2` 時兩個 target `running`、第三個 target `queued`。

### 已對齊的核心語義

- JS userscript 是功能語義基準；重要功能需先對照 `reference/src/facebook_group_refresh.user.js`。
- keyword rule：分號 OR、空白 AND、空 include 代表 include-all，exclude 命中時排除。
- permalink normalization：支援 group posts、permalink、`permalink.php`、`multi_permalinks`、`story_fbid`、`posts/pcb` 與 photo `set=gm.*`。
- post key / dedupe aliases：集中於 `core/dedupe.py`，seen repository 可一次保存多個 aliases。
- config 邊界：已對齊 JS 成熟版語義，keyword / refresh / notification 等監視設定是 group-scoped；posts/comments target 的 seen、baseline、latest scan、history 與 runtime state 仍是 target-scoped。
- config upsert：`CreateGroupPostsTargetRequest` / `CreateCommentsTargetRequest` 已區分「未提供欄位」與「明確 false / 空值 / None」；capture 類工具省略設定時保留既有 group config，新 target 才套用 `core/defaults.py`，Web UI 明確送出的 false / 空值可正常覆寫既有設定。
- notification：desktop / ntfy / Discord 由 `notifications/dispatcher.py` 集中分發。
- profile ownership：集中於 `automation/profile_lease.py`；主要 worker、設定視窗、metadata resolver 與 capture/probe scripts 都會先取得 lease。
- Web UI 啟動：啟動時先停止所有 target；使用者按 target「開始」才清 seen scope、要求立即掃描並喚醒 scheduler。
- Web UI 設定頁：提供通知預設值、測試通知、批次套用到所有社團設定；需要 profile 時會內部暫停 / 恢復背景 scheduler。
- Web UI 新增 target：使用者只貼 URL，系統依社團首頁或單篇貼文 URL 自動判斷 posts/comments；未填自訂名稱時 posts/comments 都會解析社團名稱作為 target 顯示名稱。
- Web UI 啟動資料清理：預設清除上一輪可重建 runtime/debug data，包含 `scan_runs`、`latest_scan_items`、`match_history`、`notification_events`、`seen_items`；保留 target、group config、global notification settings 與 automation profile。若需要保留前次 runtime data，可用 `--keep-runtime-data-on-startup`。
- resident executor：scheduler tick 只 enqueue due targets，`TargetQueue` 防止同 target 重複 queued/running，`ExecutorWorkerPool` 以固定 worker slots 執行掃描；runtime state 支援 `idle / queued / running / paused / error` 與 active worker/page diagnostics。
- scheduler diagnostics：Web UI 顯示 running / queued / slots / pages / opened / reused / closed / browser alive；queued target 顯示 `queue_position`；scan diagnostics 顯示 `last_page_reloaded_at`。
- worker 主次路徑：async resident worker 是正式產品主路徑；one-shot mode 與 sync resident worker 只作為 fallback / debug tooling。新增功能預設只要求 async resident 完整接上，除非該功能本身對 debug path 必要。
- Web UI dashboard：target 操作會回原 target anchor；首頁以 `/api/dashboard-revision` 條件式刷新；右側最近掃描貼文面板固定貼齊 target 設定區高度，貼文清單在面板內部捲動。
- `auto_adjust_sort`：已對照 JS sort helper chain，診斷寫入 `latest_scan.metadata.sort_adjust`，使用者已實測正常。
- `auto_load_more` posts：已對齊 JS posts scroll helper chain、snapshot/restore、load-more mode、moved distance / scroll step diagnostics、candidate collection limit 與 `collected_meta`；wheel mode 仍 deferred。
- comments D1-D4：已建立 comments target model / URL route / Web UI URL 自動判斷新增入口、comments extractor、comment id / canonical permalink、comment text cleanup、comments item dedupe aliases、comments latest scan / history / notification persistence、comment sort、nested scroll/load-more、comment-specific load-more guard、mutation relevance helper，並已接上 Web UI 開始 / 停止與 async resident executor 正式主路徑。
- defaults source：Python 版 deliberate divergence 預設值集中於 `core/defaults.py`，Web UI、service 與 model 不得各自硬寫一份。
- tests 已由 phase A/B/C 歷史命名整理為模組分類：`tests/core`、`tests/application`、`tests/persistence`、`tests/facebook`、`tests/worker`、`tests/scheduler`、`tests/webapp`、`tests/notifications`、`tests/automation`、`tests/cli`。

## 主要模組

- `core/`：資料模型、keyword、dedupe、refresh policy。
- `application/`：application service 與 context wiring。
- `persistence/`：SQLite schema、repositories 與 runtime data maintenance。
- `facebook/`：Facebook route、DOM extractor、permalink、sort / scroll helpers。
- `worker/async_resident.py`：resident Playwright context lifecycle 與 scheduler tick 接線。
- `worker/resident_queue.py`：TargetQueue 與 queued/running 去重。
- `worker/resident_page_pool.py`：async page pool 與 page ownership diagnostics。
- `worker/resident_executor.py`：ExecutorWorkerPool、scan guard、runtime/page diagnostics。
- `worker/group_posts.py`、`worker/runner.py`、`worker/resident.py`：posts worker 與 fallback / debug path。
- `scheduler/`：target-level planner 與 one-shot scheduler fallback。
- `webapp/`：FastAPI Web UI、templates、read model、scheduler/profile session managers。
- `tests/`：依正式模組分類，不再以 Phase A/B/C 命名；phase 只保留在文件進度與 script 名稱中。

## Scripts 整理策略

- `scripts/` 目前保留平鋪檔名，因使用者日常啟動指令仍依賴 `phase_b_webui.py` 等穩定入口；不在此輪搬成子資料夾，避免打斷操作。
- Phase 命名在 scripts 代表歷史/相容 CLI entrypoint，不代表正式模組邊界；正式程式碼與 tests 已用 package/module 分層。
- 若後續要整理 scripts，優先做相容 wrapper：保留既有 `phase_*` 檔名，新增較語義化入口或共用 CLI defaults，不直接移除舊命令。

## 架構決策與觸發條件

- `phase_offset_sec` 目前暫不實作；若 target 數量增加、同時 due 造成 profile / Facebook loading 壓力，應優先做系統自動分散 phase，不先做使用者手動欄位。
- `TargetSchedulePlanner.is_due()` / `compute_next_due_at(...)` 目前不為命名而拆；等 phase offset、jitter 或 richer due diagnostics 開工時再一起拆出可測 API。
- one-shot scheduler queue 化暫不做；one-shot 是 fallback / debug path，保留較簡單的 batch 路徑有助於排查 resident 問題。不可用半套方式偷偷補 queue parity；若 reviewer 要求所有 scheduler 路徑共用同一 admission model，必須開獨立完整任務。
- 獨立 load-more reentry guard 目前不做；posts target 已由 per-target scan guard 保護。不可只補 UI 欄位或零散 helper；若要處理，必須作為完整子任務接上設定、runtime、diagnostics 與測試。
- JS 版「第一則貼文與前次相同就跳過深度掃描」最佳化目前不移植；部分社團即使切到新貼文，最上方仍可能是置頂或管理員貼文，不能把 top item 當作最新貼文判斷。未來若要做，只能做成有 diagnostics 支撐、能辨識 pinned/admin/top-static post 的可選最佳化。
- Python 版刻意保留部分預設值與 JS 不同：`auto_adjust_sort=False` 避免新增 target 後立刻改動 Facebook 排序；`enable_desktop_notification=False` 避免本機桌面通知在未確認環境前自動跳出。這是 deliberate divergence，不是漏移植；使用者可在 Web UI 啟用。
- `persistence/sqlite.py` 是 P1 架構風險；目前新增 `group_configs` 作為唯一正式 group-scoped config 表，舊 `target_configs` 只作為既有 DB migration fallback。新正式功能不得直接讀寫 `target_configs`。若新增 comments schema、queue diagnostics persistence、schedule state 或 notification event 擴充，優先拆出 schema / targets / config / scan runs / notifications repository 模組。
- `TargetApplicationService` 是 P1 架構風險；`_create_*` 只保留為 internal helper，正式互動入口一律走 `upsert_*`，不得新增正式 call site 使用 `_create_*`。若新增 comments workflow、profile setup workflow、bulk operations 或 richer queue controls，優先拆出 runtime state、notification settings、scan recording 等 service。
- `webapp/app.py` 目前可接受；若再新增多頁或更複雜互動，才拆成 target / scheduler / notification / profile routes。

### Deferred 邊界

Deferred 不代表可用半套方式偷補。以下項目現在不做、也不是 rejected；未來若開工，必須作為完整 phase / 完整子任務實作，包含設定、runtime 行為、diagnostics、tests 與文件：

- `loadMoreMode=wheel`
- `phase_offset_sec`
- one-shot scheduler queue 化
- posts-only 獨立 load-more reentry guard
- fallback/debug path comments parity
- 大型 DOM helper 拆分

## Comments Phase Entry Plan

詳細規格與驗收標準見 `comments_phase_entry_checklist.md`。本節只保存目前正式進度與不可跳過的開工順序。

### Gate 0：comments phase 開工前驗收

comments phase 已通過 Gate 0，並已完成 D1-D4；目前 comments target 已接上正式 async resident 路徑，但仍需要使用者用實際 Facebook 貼文頁做手動驗收。

已成立：

- async resident 已明確定義為正式主路徑。
- one-shot / sync resident 已文件化為 fallback / debug path。
- `sqlite.py` / `TargetApplicationService` 胖檔風險已在 docs 中記錄。
- runtime state 已具備 `idle / queued / running / paused / error` 與 worker/page/reload diagnostics。
- 使用者已實測 `max_concurrent_scans=2` 時兩個 target `running`、第三個 target `queued`。
- `auto_adjust_sort` 已由使用者實測正常。
- 使用者已確認右側最近掃描貼文面板在面板內部捲動，且不再撐長 target 卡片。
- 使用者已確認目前顯示狀態正常，未觀察到 stale queued / running。
- 使用者已確認 `load_more_mode / movedDistance / scrollStep / stopReason` diagnostics 可判讀載入更多，且能穩定取得 10 篇貼文。
- 使用者已確認通知預設值與通知功能皆正常。

已有自動測試證據：

- bounded concurrency：`tests/worker/test_async_resident.py` 覆蓋 `max_concurrent_scans=2` 並行、第三 target queued 與 queue order。
- resident page reuse：`tests/worker/test_async_resident.py` 覆蓋同 target 跨 cycle 重用 page，且同一 group feed 帶 sorting query 時走 `reload` 而不是 `goto`。
- page diagnostics：`tests/webapp/test_app.py`、`tests/webapp/test_scheduler_session.py` 覆蓋 Web UI topbar 的 opened / reused / closed / browser alive diagnostics。
- scan guard：`tests/scheduler/test_loop.py` 覆蓋同 target running 重入會被 guard 擋下；`tests/webapp/test_app.py` 覆蓋 skip reason 會顯示在 UI。
- `auto_load_more` posts：`tests/worker/test_group_posts.py` 覆蓋 target count、round count、load more mode、stop reason、collected meta 與關閉 auto load more 的行為。
- notification defaults：`tests/webapp/test_app.py` 覆蓋通知預設值保存、測試通知、套用到 target；`tests/worker/test_group_posts.py` 覆蓋 desktop / ntfy / Discord dispatcher 在命中時發送。
- group-scoped config：`tests/application/test_services.py` 覆蓋同一社團 posts/comments target 共用設定，並確認從 comments 更新 config 後 posts 讀到同一份設定；`tests/persistence/test_sqlite.py` 覆蓋舊 target-scoped config fallback migration。
- 本機 `data/app.db` 快照檢查：截至 2026-05-04，本機 scan runs 彙總為 1771 success / 15 failed；runtime state 無 queued / running 殘留，當下為 3 paused / 1 error。

Gate 0 結論：

- comments phase 前置 Gate 0 的 posts 主路徑、runtime diagnostics、auto load more、通知與 UI 面板項目已具備自動測試與使用者實測確認。
- comments phase 已依使用者授權完成 D1-D4；目前已完成 target 建立、抽取、canonicalization、cleanup、latest scan state、comment sort、nested scroll/load-more、guard、mutation relevance helper，並已把 comments target 接入 Web UI 開始 / 停止與 async resident executor。

### Comments Phase D1-D4

Gate 0 通過後，comments phase 必須依序執行，不可把 comments 當成零碎小功能：

1. D1 model / schema / target creation：已建立 `TargetDescriptor.for_comments(...)`、`CreateCommentsTargetRequest`、`upsert_comments_target(...)`、comments route detection、Web UI 依 URL 自動判斷 posts/comments 與列表基本 diagnostics；comments target 預設 paused。
2. D2 extraction / canonicalization / cleanup：已建立 `facebook/comment_dom.py`、`facebook/comment_extractor.py` 與 `worker/comments.py`；支援可見留言抽取、comment id / canonical permalink、文字清理、comments dedupe aliases、seen / history / latest scan items / scan run / notification persistence。
3. D3 sort / scroll / mutation relevance：已建立 comment sort handling、comment nested scroll target collection/scoring、nested scroll ownership、comment-specific load-more guard 與 mutation relevance helper；comments worker 可執行 nested scroll load-more。
4. D4 UI / diagnostics / regression check：已開放 comments target 開始 / 停止，接入 scheduler planner / async resident executor / resident page reload 判斷，並補 comments target UI polish、latest scan 摘要、runtime state 相容與 posts regression tests。

### Comments Phase 禁止事項

- Gate 0 未通過前，不新增 comments helper 或 comments selector。
- 不可只新增 comments enum、UI 欄位、selector 或 scroll helper，就宣稱 comments mode 已支援。
- comments target 必須維持完整 end-to-end 語義：target model、scope model、extractor、canonicalization、cleanup、sort、scroll/load-more、latest scan/cache、UI/diagnostics。
- comments 開工後，獨立 load-more reentry guard 立即升級為必做。
- comments schema / repository / service logic 不可無限制灌進既有胖檔；開工前需先確認拆分策略。

## 下一步

1. 請使用者用實際 Facebook 單篇貼文 URL 建立 comments target，按「開始」後確認 Web UI 會進入 queued / running / idle，右側顯示最近掃描留言與 diagnostics。
2. 若 comments 實測可用，再補使用者實測紀錄到本文件，並依 `comments_phase_entry_checklist.md` 的 D4 驗收關閉 comments phase。
3. 若 comments 實測出現空抽取、排序失敗、nested scroll 無效或通知問題，先要求貼上該 target 的掃描診斷與單筆留言除錯，再對照 JS 版成熟邏輯小步修正。
4. 若 posts 實測再出現 stale queued / running、auto load more 不穩或通知失敗，優先補 posts 主路徑，暫停 comments 後續 polish。

## 目前不做

- 不把 comments 宣稱為已通過實機驗收；D4 程式接線與 regression tests 已完成，但仍待使用者用真實 Facebook DOM 驗證。
- 不做多 profile / 多 automation profile orchestration。
- 不做 EXE 打包。
- 不搬 userscript 的頁內 panel UI。
- 不把 `wheel mode` 或 comments nested scroll 宣稱為已完成。
- 不實作「第一則貼文與前次相同就跳過深度掃描」的 top-item early-skip 最佳化；置頂 / 管理員貼文會讓判斷不可靠。
- 不為命名重構而拆 planner API；等 phase offset、jitter 或 due diagnostics 需要時再拆。
- 不宣稱 mutation relevance 已接上即時觸發；目前 Python resident worker 仍是 polling，D3 只保存並測試 DOM-side relevance helper。

## 主要風險

- Facebook 可能要求重新登入、checkpoint 或其他驗證。
- headless / headed DOM 可能不一致。
- selector / extractor 可能因 Facebook DOM 變動而不穩。
- resident worker、設定視窗、capture script 不能同時持有同一 automation profile；看到 `profile_locked` 時先找仍在執行的 Playwright context。
- comments D1-D4 已完成程式接線與 regression tests；真實 Facebook comments DOM 仍可能因 selector / nested scroll / sort menu 變動而需要使用者診斷回饋。
- `feed_dom.py` 仍是大型短生命週期 evaluate 腳本；若 DOM 邏輯繼續擴大，應拆 selectors / permalink DOM / text DOM / author DOM。
