# Facebook Group / Post Monitor Python 版重構計劃（給 Codex 的實作規格）

> 文件狀態：  
> 本文件是長期遷移設計與早期架構背景，不是目前進度來源。  
> 目前實作狀態、下一步、風險與不做事項以 `docs/TASK_BREAKDOWN.md` 為準。  
> 新對話或下一位 agent 交接以 `docs/HANDOFF.md` 為準。  
> 若本文件與 `docs/TASK_BREAKDOWN.md` 衝突，以 `docs/TASK_BREAKDOWN.md` 為準。

> 已演進差異：  
> - async resident worker 是唯一正式產品主路徑。  
> - one-shot mode 與 sync resident worker 只保留為 fallback / debug tooling，新功能預設不追求 fallback parity。  
> - scheduler 已改為 queue-based continuous executor，scheduler tick 只 enqueue due targets，executor worker slots 負責實際掃描。  
> - Web UI 的日常操作以每個 target 卡片的「開始 / 停止」為主，不再把全域 scheduler 啟停暴露成主要操作。  
> - comments target 已依 `comments_phase_entry_checklist.md` 完成 D1-D4 程式接線；是否通過真實 Facebook DOM 驗收，以 `docs/TASK_BREAKDOWN.md` 最新狀態為準。  
> - `phase_offset_sec`、planner API 拆分、one-shot queue 化、獨立 load-more reentry guard 等 deferred 項目的目前決策與不可半補邊界，請看 `docs/TASK_BREAKDOWN.md`。

## 0. 文件目的

本文件定義一個**以 Python 為主、以 Playwright 為核心瀏覽器自動化層**的 Facebook 監視器重構計劃。

此計劃的核心目標，不是單純把既有 Tampermonkey / userscript 程式逐行翻譯成 Python，而是：

1. **解決目前 userscript 必須維持前景視窗才能穩定運作的核心痛點**。
2. **保留目前已整理好的資料模型與功能邏輯**（per-group 設定、posts/comments 監視、scope 去重、通知、排序調整等）。
3. **把「互動式目標選擇」與「長時間背景監控」拆成兩個不同執行階段**。
4. 將系統從「頁面內嵌腳本」提升為「桌面自動化應用」，後續可逐步擴充成本機管理 UI / EXE / 多 target 管理工具。

本文件預設交付給 Codex 作為**完整實作藍圖**，因此會包含：

- 架構設計
- 模組切分
- 資料模型
- 風險與注意事項
- 初期測試計劃
- 搬遷難度評估
- 實作優先順序
- 不應做的事情

---

## 1. 問題定義與核心判斷

### 1.1 目前 userscript 方案的根本限制

現有 Tampermonkey 版本直接在 Facebook 頁面內執行，依賴：

- 前景頁面的 JS 計時器
- MutationObserver
- DOM 動態更新
- 同一個頁面內的面板 UI / 設定 UI
- 使用者手動切到某個社團或貼文後，腳本留在該頁面持續運作

這導致一個核心 UX 問題：

> 當視窗縮小、被其他視窗蓋住、或整體被 Chromium 視為背景頁時，頁面本身可能被節流，導致監視流程變得不穩定。

因此，真正要解決的不是「JS 不夠好」或「Python 比 JS 強」，而是：

> **執行模型必須從「前景分頁內腳本」改成「Python 主控的瀏覽器自動化 worker」。**

---

### 1.2 不應採取的錯誤方向

#### 錯誤方向 A：逐行把 userscript 翻成 Python，但仍維持一個可見 Facebook 視窗長時間掛著

這樣雖然比 userscript 稍微自由，但仍可能受到：

- Chromium 視窗遮擋 / occlusion 背景化
- 視窗縮小或被遮住後的 renderer / timer 節流
- 長時間保留可見視窗造成的使用干擾

這是**過渡方案**，不是最佳最終形態。

#### 錯誤方向 B：一開始就做一個完整桌面 GUI + 所有功能全搬 + 完整多 target 管理

這樣專案風險太高。

真正應該先驗證的是：

> **Facebook 在 Python + Playwright 背景 worker 模式下，能否穩定監控。**

在這件事尚未被驗證前，不應先投入大量時間在漂亮 UI。

---

## 2. 建議採用的總體方案

## 2.1 推薦方案：**Headed Setup + Headless Monitor**（混合式）

這是本計劃推薦的主方案。

### 階段 A：有視窗 Setup

使用 **headed Playwright** 啟動一個專用 Chromium / Chrome 視窗，讓使用者：

- 登入 Facebook
- 手動切到目標社團頁面，或某篇貼文頁面
- 檢查目標頁面內容
- 按下「加入監視 / 開始監視」
- 編輯 keywords / exclude / refresh / 通知等設定

### 階段 B：背景 Monitor

一旦 target 被建立完成：

- 儲存 target 資訊
- 儲存登入狀態（storage state 或 persistent profile）
- 交由 **headless worker** 在背景長時間監控
- 不再依賴那個可見 Facebook 視窗常駐前景

### 為什麼這是最合理的

因為你的需求同時包含：

1. **需要看得見的視窗**：手動選社團 / 選貼文 / 確認目標
2. **希望背景執行**：監視期間不要被前景視窗綁住

所以正確解法不是全程 headed 或全程 headless，而是：

> **用 headed 解決「選目標」問題，用 headless 解決「長時間監控」問題。**

---

## 2.2 相容備援方案：Headed Worker Compatibility Mode

若某些 Facebook 目標在 headless 下有額外不穩定因素（登入狀態、動態頁 DOM 差異、留言排序選單異常等），應保留一個**相容模式**：

- 使用 headed browser worker
- 掛 anti-throttling flags
- 以獨立 automation profile 執行

此模式不是預設模式，而是 fallback：

- 預設：headless worker
- 例外：某些 target 或某些頁面類型切到 compatibility headed worker

這樣可以降低一次性全賭在 headless 的風險。

---

## 3. 初期版本的產品邊界

## 3.1 第一階段 MVP 必須支援

### 功能
- 只支援 Facebook
- 支援兩種 target：
  - group feed posts
  - single post comments
- 支援 per-group / per-target 儲存設定
- 支援 include / exclude keyword
- 支援 seen item 去重
- 支援 ntfy 通知（先做一種最簡單的通知通道即可）
- 支援 headed setup
- 支援 headless monitor
- 支援儲存 / 重用登入狀態
- 支援最基本 history
- 支援每個 target 獨立啟動 / 停止，不因單一 target 設定變更而重啟全部 worker

### 非功能要求
- 重啟後可恢復既有 target 與狀態
- worker crash 不應讓整體資料毀損
- target 管理與 worker 執行分離
- target 啟停控制必須是 per-target 操作，A target 的 start/stop/config update 不應干擾 B target 的執行
- 初期不追求漂亮 GUI

---

## 3.2 第一階段刻意不做的事

以下功能不要在第一輪就做：

- 完整複製 userscript 的 Facebook 頁內面板 UI
- 拖曳面板 / 視窗內 debug console
- 一開始就支援太多通知通道
- 一開始就同時監控很多 target
- 一開始就做 EXE 打包
- 一開始就做完整桌面 GUI
- 一開始就追求與 JS 版完全 1:1 行為一致

理由：

> 第一優先是驗證「背景 worker 監控 Facebook 是否穩定」。

---

## 4. 建議技術棧

## 4.1 主語言與核心庫

- Python 3.13
- Playwright Python
- SQLite（狀態與設定儲存）
- Pydantic（設定／資料結構驗證）
- SQLAlchemy 或 SQLModel（二選一，偏好 SQLModel 或 SQLAlchemy 2.x）
- httpx（通知 HTTP client）
- FastAPI（第二階段管理 UI）
- APScheduler 或自製 worker loop（初期可先不用 APScheduler）

---

## 4.2 為什麼選 Playwright 而不是 Selenium

兩者都能做，但本專案偏向 Playwright，理由：

1. 對動態頁面與 locator auto-waiting 支援較友善
2. API 對 page / context / storage state 的分層更清楚
3. cookies / local storage / storage state 的管理較自然
4. 對多 target、多 context、背景 worker 的模型更乾淨
5. 你已有 Python 瀏覽器監視專案經驗，轉 Playwright 的心智負擔較低

若後續實驗發現 Facebook 在 Playwright 下有特殊問題，再評估 fallback 到 Selenium，不應一開始就雙框架並行。

---

## 5. 瀏覽器執行模型設計

## 5.1 三種模式

系統應正式支援三種模式（邏輯上，不一定第一版 UI 就全部露出）：

### 模式 1：Headed Setup Session
用途：
- 使用者登入
- 使用者選社團 / 選貼文
- 建立或編輯監視 target

特性：
- 有可見視窗
- 不長時間監視
- 可以由使用者手動導航

### 模式 2：Headless Worker（預設）
用途：
- 長時間背景監控

特性：
- 無可見 UI
- 不受桌面前景／遮擋體驗直接限制
- 應為預設 worker 模式

### 模式 3：Headed Worker Compatibility Mode（備援）
用途：
- 某些 target 在 headless 下不穩
- 需要暫時切到可見視窗以提高相容性

特性：
- 有可見視窗
- 必須使用 anti-throttling flags
- 不作為預設

---

## 5.2 不要使用使用者日常的主 Chrome Profile

### 強制規則
禁止使用：
- 使用者平常工作的主 Chrome profile
- 已在桌面上開啟的主要個人瀏覽器 profile

### 必須做法
建立**獨立 automation profile**，例如：

- `profiles/setup/`
- `profiles/worker_default/`
- `profiles/worker_fallback/`

用途：
- 降低 profile 汙染風險
- 降低登入衝突風險
- 降低 Facebook / 瀏覽器行為難以預測的問題

---

## 5.3 Headless / Headed 的狀態接續方式

優先順序建議如下：

### 優先策略：persistent context
適用於：
- Facebook 這類登入與狀態較複雜的站點
- 希望讓 setup 與 worker 共享完整瀏覽器 session

做法：
- Setup 與 worker 都使用同一個 automation `userDataDir`
- setup 結束後關閉 context
- worker 再以相同 `userDataDir` 啟動

### 次要策略：storage_state
適用於：
- 驗證 headless worker 是否能跑
- session 相對簡單的情況

做法：
- setup 階段匯出 `storage_state.json`
- worker 建立新 context 時載入

### 初期建議
- 第一輪可行性驗證優先使用 **persistent context**，因為 Facebook 的登入狀態通常不只依賴 cookies。
- `storage_state` 保留為對照實驗或簡化 fallback，不作為第一版主要假設。
- Phase 0 必須明確記錄兩種策略的穩定性差異，避免正式架構建立後才發現 session 接續模型錯誤。

---

## 6. 防止節流與背景不穩定的策略

## 6.1 根本策略：預設 worker 使用 headless

最重要的不是 flags，而是：

> **預設長時間監控 worker 不應依賴可見前景視窗。**

因為只要 worker 仍然是一般可見 Chrome 視窗，就仍有可能受到 Chromium backgrounding / occlusion 的影響。

因此：

- **主要防節流手段 = headless worker**
- **不是只靠 anti-throttling flags**

---

## 6.2 Headed compatibility mode 的 anti-throttling flags

如果 worker 必須暫時改成 headed，可加以下 flags：

- `--disable-backgrounding-occluded-windows`
- `--disable-background-timer-throttling`
- `--disable-renderer-backgrounding`

### 但必須特別註明
這些 flags：
- 偏向 Chromium 測試/兼容用途
- 未必保證長期穩定
- 不是根本解法
- 不應作為主要依賴機制

### 實作要求
1. 只在 **headed compatibility mode** 啟用
2. 預設 **headless mode 不需要這些 flags 作為必要條件**
3. 在 config 與 log 中明確記錄是否啟用了 compatibility flags
4. 不要把 flags 是否存在，和邏輯正確性綁死

---

## 6.3 不要直接依賴頁內長時間 MutationObserver

Python 版不應複製 userscript 的頁內生命週期模型。

也就是不要以為要完整重建：
- 頁內 long-lived MutationObserver
- 面板 UI + observer 共用同一套 runtime
- 由頁面內腳本自己 schedule refresh

Python 版應改成：

- **worker loop 主控掃描節奏**
- 每輪重新收集 DOM 狀態
- 必要時在 page.evaluate() 內執行短生命週期 JS helper
- 而不是讓 injected page script 長期駐留作為核心調度器

這是從 userscript 過渡到外部自動化時，最重要的設計轉換之一。

---

## 7. 系統架構設計

## 7.1 邏輯分層

建議拆成下列層級：

### Layer 1: Domain / Core
純資料模型與邏輯，不碰 Playwright

- target 定義
- keyword matcher
- dedupe rules
- history merge
- refresh policy
- notification dispatch policy
- sort policy 決策

### Layer 2: Persistence
負責 SQLite / 設定檔 / session state

- targets CRUD
- configs CRUD
- seen items
- match history
- latest notification
- browser auth state metadata

### Layer 3: Browser Automation
負責 Playwright / 瀏覽器操作

- setup browser
- worker browser
- navigate / wait / sort / expand / extract

### Layer 4: Monitoring Engine
負責每輪掃描 orchestration

- load target
- open page
- prepare scan target
- collect items
- dedupe / match
- notify
- commit seen / history

### Layer 5: Management Interface
初期可沒有，後續再做

- local web UI
- target 清單 / 詳細設定
- 每個 target 獨立啟動 / 停止 / 暫停
- recent matches

管理介面建議採用「左側 target 清單 + 右側設定面板」或「target table + detail drawer」：

- 清單顯示 group name、group id、啟停狀態、最近掃描時間、最近錯誤、命中數。
- 詳細面板編輯 include/exclude keywords、通知設定、refresh policy、worker mode。
- 每個 target 都有獨立 start / stop 控制；操作單一 target 不應觸發全域 worker 重啟。
- 細分 script 與 console 只作為 Phase B 過渡入口，未來 UI 應呼叫 application service。
- Phase B.5 可先用 FastAPI + Jinja2 + plain HTML form 建立薄本機 UI，不引入 SPA framework 或前端 build system。
- Web UI route 只做 request parsing、呼叫 service、回傳 template；不可承擔 domain rule 或 worker orchestration。
- Web UI 自動掃描模式應以 worker runner 抽象切換，例如 resident / one-shot；UI 只傳遞模式設定，不直接管理 Playwright page pool。

---

## 7.2 建議目錄結構

目前 Python repo 已採用 `src/facebook_monitor/` package layout；下列原始 `app/`
建議結構視為邏輯分層參考，不要求改名搬目錄。現行對應關係：

- `app/core` -> `src/facebook_monitor/core`
- `app/persistence` -> `src/facebook_monitor/persistence`
- `app/facebook` / browser helpers -> `src/facebook_monitor/facebook`
- `app/monitor` / worker loop -> `src/facebook_monitor/worker` 與 `src/facebook_monitor/scheduler`
- `app/notifications` -> `src/facebook_monitor/notifications`
- `app/webui` -> `src/facebook_monitor/webapp`

```text
facebook_monitor_py/
  app/
    core/
      models.py
      enums.py
      keyword_rules.py
      dedupe.py
      history.py
      refresh_policy.py
      notification_policy.py
      target_scope.py
    persistence/
      db.py
      tables.py
      repositories/
        targets.py
        configs.py
        seen_items.py
        history.py
        notifications.py
        sessions.py
    browser/
      playwright_factory.py
      profiles.py
      setup_session.py
      worker_session.py
      anti_throttle.py
      auth_state.py
    facebook/
      route_detection.py
      selectors.py
      sort_controls.py
      extractors/
        common.py
        post_extractor.py
        comment_extractor.py
      preparation.py
      collection/
        post_collection.py
        comment_collection.py
      target_context.py
    monitor/
      worker_loop.py
      scan_service.py
      setup_service.py
      target_capture.py
      result_commit.py
    notifications/
      base.py
      ntfy.py
      discord.py
      desktop.py
    webui/
      main.py
      routes/
      templates/
      static/
    config/
      settings.py
      logging.py
  data/
    app.db
    profiles/
    storage_state/
  scripts/
    bootstrap_login.py
    run_worker.py
    run_webui.py
  docs/
    architecture.md
    migration_notes.md
```

---

## 8. 目標選擇（Setup）流程設計

## 8.1 使用者故事

使用者流程應如下：

1. 啟動 setup 模式
2. 程式開一個**可見 Facebook 瀏覽器視窗**
3. 使用者登入 Facebook（若尚未登入）
4. 使用者手動切到：
   - 某個社團主頁，或
   - 某篇貼文 permalink 頁面
5. 使用者按「Capture Current Page」
6. 程式自動判斷：
   - `target_kind = posts | comments`
   - `group_id`
   - `parent_post_id`（若為 comments）
   - `scope_id`
   - `canonical_url`
7. 使用者填入：
   - include/exclude keywords
   - refresh policy
   - notification channels
   - auto sort policy
8. 儲存 target
9. setup 視窗可關閉
10. headless worker 開始監控

---

## 8.2 Capture Current Page 時必須收集的資料

### 基本欄位
- `target_id`（UUID）
- `target_kind`：`posts` / `comments`
- `group_id`
- `group_name`（可選，但建議保存）
- `parent_post_id`（comments 時必填）
- `scope_id`
- `canonical_url`
- `created_at`
- `updated_at`
- `enabled`
- `paused`

### 設定欄位
- `include_keywords`
- `exclude_keywords`
- `min_refresh_sec`
- `max_refresh_sec`
- `jitter_enabled`
- `fixed_refresh_sec`
- `max_items_per_scan`
- `auto_adjust_sort`
- `auto_load_more`
- `enable_ntfy`
- `enable_discord`
- `enable_desktop_notification`
- `ntfy_topic`
- `discord_webhook`

### 執行狀態
- `last_seen_scan_at`
- `last_success_scan_at`
- `last_error`
- `last_notification_status`
- `worker_mode`：`headless` / `headed_compat`
- `desired_state`：`running` / `stopped`（或以 `enabled + paused` 表示）
- `runtime_state`：`idle` / `running` / `error`
- `last_heartbeat_at`

### 操作介面語意

- `Start target`：只讓該 target 進入可被 scheduler 執行的狀態，不影響其他 target。
- `Stop target`：只暫停該 target 的後續掃描，保留設定、seen 與 history，不影響其他 target。
- `Edit config`：更新該 target 所屬 group config；同一 group 下 posts/comments target 共用 keyword / refresh / notification 設定。scheduler 下一輪讀取新設定，或收到設定變更事件後套用，不應要求全域重啟。
- `Delete target`：屬於高風險操作，需確認是否保留 history；第一階段可先不做。

---

## 9. 背景 worker 設計

## 9.1 每個 worker 的責任

一個 worker 只做三件事：

1. 打開 target 頁面
2. 抽取 items
3. 完成本輪 commit（seen/history/notify）

worker 不負責：
- 管理 UI
- 人工選頁面
- 長駐頁內面板

---

## 9.2 單 target worker vs 多 target worker

### 初期建議
**先做單程序多 target 的簡單排程**：
- 每輪從 DB 取所有 enabled targets
- 依序掃描
- 每個 target 建立/重用 context/page
- 每個 target 的啟停狀態獨立判斷；A target 停止時不應中斷 B/C target。
- config update 應在 target 下一輪掃描前生效，除非該設定需要重建 browser context。
- 進入 resident worker 時，應保留 one-shot worker 作為 fallback；resident worker 只改變瀏覽器 / page 生命週期，不重寫抽取、去重、通知與 scan run 寫入流程。

### 不建議初期做
- 每個 target 一個獨立 OS process
- 複雜的多程序併發架構

理由：
先驗證穩定性，再談擴充性。

---

## 9.3 掃描輪次標準流程

每輪掃描應明確拆成：

1. `load_target_config()`
2. `open_target_page()`
3. `ensure_target_ready()`
4. `ensure_preferred_sort()`
5. `collect_items()`
6. `normalize_items()`
7. `dedupe_against_seen_store()`
8. `apply_keyword_rules()`
9. `dispatch_notifications()`
10. `commit_seen_items()`
11. `commit_history()`
12. `commit_latest_scan_state()`
13. `schedule_next_run()`

### 規則
- 每一步都要能 log
- 每一步失敗時要留下明確 error reason
- 不要把所有邏輯塞進單一大函式
- 每輪開始前重新確認 target 是否仍 enabled 且未 paused。
- target 被停止時，scheduler 應取消或跳過該 target 的下一輪掃描，不影響其他 target。

---

## 10. 目前 JS -> Python 模組搬遷規劃

你的現有 userscript 已經有很多可直接沿用的資料模型概念，這是本專案最大的優勢。

## 10.1 可直接移植的高價值邏輯

### A. 設定模型
目前 JS 已有：
- group-scoped config bucket
- keyword / notification / monitoring / refresh 分組
- enable/disable notification channels
- per-group store key 設計

### Python 搬法
- 用 `TargetConfig` / `GroupConfig` Pydantic model 重建
- 寫入 SQLite
- 讀取時做 normalize 與 fallback

### 難度
**低到中**

---

### B. scopeId / targetKind 模型
目前 JS 已明確區分：
- group feed posts
- single post comments
- `scope_id` 用於 baseline / seen partition
- `group_id` 保留作為 config/history partition

### Python 搬法
- 做 `TargetDescriptor` model
- `scope_id = group_id`（posts）
- `scope_id = {group_id}:post:{parent_post_id}:comments`（comments）

### 難度
**低**

---

### C. include / exclude keyword 規則
JS 裡的 `buildKeywordRule / parseKeywordInput / matchRules` 都屬於純邏輯，可直接翻譯。

### Python 搬法
- 完整保留語義
- 單元測試直接覆蓋

### 難度
**很低**

---

### D. seen / history / latest scan / latest notification
目前 JS 已有完整資料流與 history entry 結構，包括：
- `itemKind`
- `parentPostId`
- `commentId`
- `postKey`
- `author`
- `text`
- `permalink`
- `includeRule`
- `notifiedAt`

### Python 搬法
- 用 SQLite table 取代 GM storage
- `seen_items`、`match_history`、`last_notifications`、`latest_scan_runs`

### 難度
**中**

---

## 10.2 需要重寫、不能直接照搬的部分

### A. 面板 UI / 頁內 modal
JS 版的 panel、debug 區塊與 lifecycle 強耦合。

### Python 搬法
- 不搬頁內面板
- 初期以 log + DB + CLI 替代
- 第二階段改成本機 Web UI

### 難度
**不搬最省；若要重做則高**

---

### B. MutationObserver 生命週期
JS 版 heavily 依賴 observer 安裝與頁內 route 維護。

### Python 搬法
- 不重建長駐 observer 作為主流程
- 改成每輪 scan 主動收集 DOM
- 必要時短時間觀察 DOM 穩定，不做永久 observer 核心調度

### 難度
**中到高，但屬於必要設計轉換**

---

### C. DOM 抽取層
目前 JS 已有：
- selectors
- expander click
- post/comment extract
- permalink warmup
- canonical container promotion

### Python 搬法
- 不是逐行翻譯 DOM API
- 要改成 Playwright locator / evaluate 風格
- 將 DOM preparation 與 extraction 拆開
- permalink 這類已成熟且會影響 item identity 的邏輯，應優先搬 userscript 既有語義，包括 canonical URL 正規化、候選來源排序與必要診斷，不應另寫一套臨時規則

### 難度
**高**

這會是整個搬遷最花時間的部分之一。

---

## 10.3 模組搬遷難度總表

| 模組 | 是否可直接搬語義 | Python 實作難度 | 備註 |
|---|---:|---:|---|
| Keyword 規則 | 是 | 低 | 直接翻譯純邏輯 |
| Config / per-group store | 是 | 低~中 | 改 SQLite |
| scopeId / target model | 是 | 低 | 保持現有語義 |
| seen/history merge | 是 | 中 | 改 DB transaction |
| notification dispatch policy | 是 | 低~中 | HTTP client + desktop notify |
| refresh policy | 是 | 低 | 幾乎直接搬 |
| sort policy 決策 | 是 | 中 | 控制方式改 Playwright |
| DOM selectors / extraction | 否（需重寫） | 高 | 最大工作量之一 |
| 頁內 panel UI | 否 | 高 | 初期不要做 |
| MutationObserver lifecycle | 否 | 中~高 | 改 worker orchestration |
| route 變更維護 | 部分 | 中 | 由 worker 掌控導航 |

---

## 11. 建議資料庫設計

## 11.1 必備表

### `targets`
```text
id (pk)
name
target_kind              -- posts / comments
group_id
group_name
parent_post_id
scope_id
canonical_url
enabled
paused
worker_mode              -- headless / headed_compat
created_at
updated_at
```

`enabled` / `paused` 的初期語意：

- `enabled = 1, paused = 0`：可被 scheduler 掃描。
- `enabled = 1, paused = 1`：使用者暫停監視，保留設定與 history。
- `enabled = 0`：停用或保留給後續刪除/封存語意。

### `group_configs`
```text
group_id (pk)
include_keywords
exclude_keywords
min_refresh_sec
max_refresh_sec
jitter_enabled
fixed_refresh_sec
max_items_per_scan
auto_load_more
auto_adjust_sort
enable_desktop_notification
enable_ntfy
enable_discord
ntfy_topic
discord_webhook
```

正式 Python 路徑已對齊 JS 成熟版：keyword / refresh / notification config 屬於 group-scoped config。舊版 `target_configs(target_id pk/fk)` 已降級為 migration-only fallback，不再是正式資料來源；新正式功能不得直接讀寫。

### `seen_items`
```text
id (pk)
scope_id
item_key
item_kind
parent_post_id
comment_id
first_seen_at
last_seen_at
```

### `match_history`
```text
id (pk)
target_id
group_id
group_name
item_kind
parent_post_id
comment_id
item_key
author
text
permalink
include_rule
timestamp_text
notified_at
```

### `scan_runs`
```text
id (pk)
target_id
started_at
finished_at
status
item_count
matched_count
error_message
sort_adjust_attempted
sort_adjust_changed
sort_adjust_reason
sort_before_label
sort_after_label
worker_mode
```

### `notification_events`
```text
id (pk)
target_id
item_key
channel
status
message
created_at
```

### `target_runtime_state`（進入 scheduler 前建議新增）
```text
target_id (pk/fk)
desired_state              -- running / stopped
runtime_state              -- idle / running / error
last_started_at
last_stopped_at
last_heartbeat_at
last_error
active_worker_id
updated_at
```

此表用於支援 UI 上的獨立 start / stop 顯示與未來 scheduler 控制。Phase A/B 可先用 `targets.enabled + targets.paused` 過渡，但進入長駐 scheduler 前應補 runtime state。

### `auth_profiles`
```text
id (pk)
profile_name
mode                      -- persistent / storage_state
user_data_dir
storage_state_path
updated_at
```

---

## 12. 通知設計

## 12.1 初期建議只做 1 個穩定通道

第一階段只需要先做：
- ntfy

理由：
- 已有現成需求
- HTTP API 簡單
- 最適合先驗證 worker 穩定性

---

## 12.2 後續通道

第二階段再加：
- Discord webhook
- Windows 桌面通知

### 注意
不要讓通知模組回頭依賴 Playwright page 物件。通知模組應只吃：

- item data
- target config
- rendered message

做到完全與瀏覽器層解耦。

---

## 13. 初期測試計劃

## 13.1 測試原則

### 先驗證執行模型，再驗證功能完整度

順序必須是：

1. Python worker 背景模式是否穩
2. Facebook session 是否可延續
3. posts target 是否能穩定抽取
4. comments target 是否能穩定抽取
5. notifications 是否正常
6. 再做更多設定 UI 與多 target

---

## 13.2 測試 Phase 0：開工前可行性 Spike

### 目標
在建立正式 Python 專案架構前，先證明最關鍵的執行模型真的可行：

- 使用獨立 automation profile 登入 Facebook。
- headed setup 關閉後，headless worker 仍能使用同一 profile 進入目標社團。
- worker 在沒有前景 Facebook 視窗的情況下，仍能定期取得最新 group feed posts。
- 背景執行期間，使用者可以切換、遮住或關閉其他桌面視窗，不影響監控流程。

### 專案位置
- Phase 0 可以先在目前 repo 同層建立新的 Python 專案資料夾，例如 `../facebook_monitor_py`。
- 不要把 spike 程式塞進現有 userscript repo 的 `src/`，避免把單檔 Tampermonkey 專案與 Python worker 混在一起。
- Phase 0 通過後，再把該資料夾整理成正式 Python 專案骨架。

### 測試內容
- 只做 **1 個 group posts target**
- 只做 **includeKeywords**
- 只做 **ntfy / log**
- 只做 **seen item dedupe**
- 只做最小 extractor：能抓到貼文 key、作者、文字、permalink 即可
- 優先測 persistent context；必要時再用 `storage_state` 做對照
- 不做 UI
- 不做 SQLite repository 分層
- 不做 comments target
- 不做完整 target 管理

### 通過標準
- 連續跑 2~4 小時不失效
- 期間可正常切換其他桌面視窗
- worker 不依賴人工維持前景
- 重新啟動 worker 後，仍能沿用登入狀態
- 每輪 scan 都能輸出可檢查的 log：target URL、item count、matched count、error reason
- 若 headless 失敗，必須能明確判斷是登入、DOM、排序、載入或 extractor 問題

### 不通過時的處理
- 若 persistent context 的 headless session 無法穩定延續，先排查 profile lock、登入挑戰與 headless DOM 差異；必要時再測 `storage_state` 或 headed compatibility worker，不要直接投入完整架構。
- 若 Facebook headless DOM 與 headed 差異過大，先確認是否仍值得做 Python 版，而不是硬搬 userscript。
- 若只能依賴可見 headed worker，則本方案只能解部分痛點，應重新評估投入成本。

---

## 13.3 測試 Phase 1：登入狀態穩定性

### 測試內容
- setup 登入後儲存 state
- 關閉 headed setup
- 啟動 headless worker
- 驗證 worker 是否仍能進入 target 頁並看到可掃描內容

### 要記錄
- cookies 是否足夠
- local storage / IndexedDB 是否有缺
- 是否需要改 persistent context

---

## 13.4 測試 Phase 2：社團貼文監視

### 測試內容
- 新增一個社團貼文 target
- 驗證 refresh / sort / extract / dedupe / notify

### 必須覆蓋
- 畫面前景
- 視窗被遮住
- 視窗關閉後 headless worker 接手

---

## 13.5 測試 Phase 3：單篇貼文留言監視

### 測試內容
- 新增一個 comments target
- 驗證 comment extractor
- 驗證 comment sort 調整
- 驗證 seen item scope 是否獨立於 posts target

---

## 13.6 測試 Phase 4：多 target 與多社團

### 測試內容
- 同時啟動 2~3 個 target
- 至少包含：
  - 1 個 posts target
  - 1 個 comments target
- 驗證不同 target 的 seen/history/config 不互相污染

---

## 13.7 初期必做的手動測試情境

1. Setup 視窗登入 Facebook 後關閉，再啟 worker
2. worker 跑 1 小時以上
3. worker 跑時切換其他最大化視窗
4. worker 跑時讓桌面鎖定 / 喚醒（可選）
5. 重新啟動 app 後，target 與 seen/history 是否仍存在
6. 刪除某 target 後，scope / history 是否正確清理
7. 同一 group 下 posts 與 comments target 是否不互相覆寫 seen

---

## 14. Codex 實作優先順序

## 14.1 Phase 0：Playwright 背景監控 Spike

Codex 先做最小可行性驗證，不先建立完整分層架構：

- 在現有 repo 同層建立獨立 Python 專案資料夾（建議 `facebook_monitor_py`）
- 選定最小環境管理方式
- 安裝 Playwright Python
- 建立獨立 automation profile
- headed 登入 / 選定一個 group posts target
- headless 使用同一 profile 開啟 target URL
- 抽取少量可見貼文並輸出 log
- 實作最小 include keyword、seen dedupe、ntfy 或 log 通知
- 連續背景執行 2~4 小時

### 驗收條件
- 不需要 Facebook 視窗保持前景
- headless worker 可重啟並沿用登入狀態
- 至少一個 posts target 能穩定掃描與去重
- 失敗時有明確 log 可判斷原因

### 完成後決策
- 通過：進入 Phase A，建立正式資料模型與 SQLite 架構。
- 部分通過：先補 headed compatibility mode spike，再決定是否進入 Phase A。
- 不通過：暫停 Python 版完整重構，不投入後續 GUI / DB / 多 target 架構。

---

## 14.2 Phase A：基礎骨架

Phase 0 通過後，Codex 再做：

- 專案骨架
- SQLite schema
- Pydantic models
- target repository
- config repository
- seen/history repository
- notification interface
- logging

### 驗收條件
- 可建立 target
- 可保存設定
- 可寫入 seen/history
- 尚未碰 Facebook

---

## 14.3 Phase B：Headed Setup Browser

Codex 再做：

- Playwright headed setup session
- 讀目前 URL
- 判斷 posts/comments target
- capture target metadata
- 保存 target

### 驗收條件
- 使用者可手動導航後按一下 capture
- target 正確落 DB

---

## 14.4 Phase C：Headless Worker MVP

Codex 再做：

- headless worker loop
- 1 個 posts target
- basic extractor
- ntfy notify
- seen dedupe

### 驗收條件
- 背景跑 2 小時以上

---

## 14.5 Phase D：Comments Target

Codex 再做：

- comment target open / prepare / extract
- sort handling
- seen scope 隔離

### 驗收條件
- comments target 可獨立運作

---

## 14.6 Phase E：Local Management UI

最後再做：

- FastAPI web UI
- target list / edit / delete
- recent matches
- worker status

---

## 15. Codex 實作注意事項（強制）

## 15.1 不要做的事情

1. **不要逐行機械翻譯 userscript**
2. **不要把 UI 與 worker 綁死在同一個執行流程**
3. **不要一開始就複製頁內 panel**
4. **不要使用使用者主 Chrome profile**
5. **不要把 anti-throttling flags 當成主解法**
6. **不要在第一版就做太多通道、太多模式**
7. **不要把 selectors 寫死在一個超巨大檔案裡**
8. **不要把 DB 寫入散在自動化流程中，必須有 repository 層**
9. **不要用無窮大的 while loop + sleep 粗暴控制所有 worker**
10. **不要把 page.evaluate 的大型 JS 腳本當成主邏輯容器**

---

## 15.2 必須做的事情

1. 模組分層
2. 完整 log
3. DB transaction / commit 邊界清楚
4. target / scope / seen / history 分離
5. headless 為預設 worker
6. headed compatibility mode 為 fallback
7. 所有 extractor 與 DOM preparation 可獨立測試
8. 所有關鍵設定有預設值與驗證
9. 所有通知結果要記錄 status
10. 所有 worker 錯誤要落 DB / log，不能只印 console

---

## 16. 專案啟動時應優先做的第一批工作

### Day 0 / Task 0：Phase 0 Spike
- 在目前 repo 同層建立 `facebook_monitor_py`，作為 Python 版獨立工作區。
- 建立最小 Playwright Python 腳本，不建立完整 DB / repository / Web UI。
- 使用獨立 automation profile 完成 headed 登入。
- 關閉 setup 視窗後，以 headless worker 重用該 profile 開啟一個 group posts target。
- 每輪輸出貼文數、命中數、錯誤原因與目前 URL。
- 連續背景執行 2~4 小時，確認不需要 Facebook 視窗維持前景。

### Day 0 驗收後決策
- 通過才進入 Day 1。
- 未通過時，先修正 session / headless / extractor 問題，不建立正式架構。
- 若只能用 headed compatibility mode，先記錄限制並重新評估是否仍值得繼續。

### Day 1 / Task 1
- 建立 repo 與骨架
- 選定 Poetry/uv/pip-tools 其中一種環境管理方式
- 建立基礎 logging
- 建立 SQLite schema

### Day 1 / Task 2
- 寫 `TargetDescriptor`、`TargetConfig`、`SeenItem`、`MatchHistoryEntry`
- 把目前 JS 的核心資料模型翻成 Python model

### Day 2 / Task 3
- 寫 headed setup browser
- 實作 `capture_current_target()`
- 驗證是否能從目前頁面抓到 group_id / parent_post_id / target_kind

### Day 2 / Task 4
- 寫 headless worker stub
- 只做 `open page -> log title/url -> exit`

### Day 3 / Task 5
- 完成最小 posts extractor
- seen item dedupe
- ntfy 通知

### Day 4+
- comments target
- sort handling
- history
- local web UI

---

## 17. 我對此專案是否值得做的最終判斷

### 結論
**值得做，而且方向正確。**

理由：

1. 你現在最大的痛點是「前景依賴」，這不是 userscript 能優雅解掉的問題。
2. 你現有 JS 腳本已經不是原型，而是有成熟資料模型的系統。
3. 這代表你搬到 Python 時，不是從零開始，而是把：
   - 資料模型
   - 規則邏輯
   - 去重邏輯
   - 通知設計
   搬到新的執行模型上。
4. 最難的不是「能不能做」，而是「不要一開始做太大」。

因此，本計劃主張：

> **先做小而能證明價值的 Python 背景 worker，再逐步取代 userscript。**

---

## 18. 一句話版本（給 Codex 的總指令）

請不要把現有 userscript 直接逐行翻譯成 Python。

請以以下原則重構：

- **headed setup for manual target selection**
- **headless worker for long-running monitoring**
- **SQLite-backed target/config/seen/history model**
- **Playwright-based page preparation + extraction**
- **local management UI only after background monitoring is proven stable**

若某些 target 在 headless 下不穩，再提供 **headed compatibility worker mode**，並用 anti-throttling flags 作為備援，而不是作為主要方案。
