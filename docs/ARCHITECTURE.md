# 架構說明

本文件只記錄穩定架構事實與不可回退的產品語義。
目前狀態、下一步、風險與驗證結果看 `docs/TASK_BREAKDOWN.md`；新對話交接看 `docs/HANDOFF.md`。

## 核心原則

- JS userscript 是功能語義來源，不是逐行翻譯來源。
- Python 版可以重新分層與命名，但 target/scope、config、seen、notification、sort、load-more、diagnostics 等語義必須對齊 JS 成熟版本。
- Web UI 是正式日常入口；scheduler 是 Web UI 背後的內部背景服務，不是使用者第二個主開關。
- resident main worker 是正式產品主路徑；one-shot mode 與 sync resident worker 只作 fallback / debug tooling。
- 新功能預設先接正式 async resident 主路徑；fallback/debug path parity 必須作為獨立完整任務處理。

## 正式入口

- Web UI：`scripts/start/webui.py`
- profile 登入 / 檢查：`scripts/start/setup_login.py`
- automation profile：`data/profiles/automation_default`
- script 分層依 `scripts/start/`、`scripts/admin/`、`scripts/debug/`、`scripts/internal/`；不得新增新的 `phase_*` script。

## 模組邊界

- `core/`：資料模型、keyword rules、dedupe、refresh policy、Python 預設值。
- `application/`：context wiring、target registry/config/runtime、monitoring commands、scan recording、route detection。
- `persistence/`：SQLite connection、schema、migrations、row mappers、repositories、runtime data maintenance。
- `facebook/`：Facebook route detection、DOM extraction、permalink、sort / scroll helpers。
- `worker/`：posts/comments scan pipeline、shared finalize、failure finalize、resident main、fallback/debug workers。
- `scheduler/`：target planner、runtime recovery、one-shot fallback scheduler。
- `notifications/`：desktop / ntfy / Discord sender、channel dispatch、outbox service 與 manual test。
- `webapp/`：FastAPI app assembly、routes、form models、read model、diagnostics presenter、scheduler/profile session managers。

## Target 與 State

- posts target 代表社團貼文監視。
- comments target 代表單篇社團貼文留言監視。
- keyword / refresh / notification 是 group-scoped config。
- seen、baseline、latest scan、match history、notification events、runtime state 是 target-scoped。
- 舊 `target_configs` 只作 migration fallback；正式路徑只讀寫 `group_configs`。
- legacy target config read path 使用 `LegacyTargetConfig`，必須明確轉成 `TargetConfig(group_id=target.group_id)` 後才能保存到 `group_configs`。
- target 建立 / 更新正式入口是 `upsert_group_posts_target(...)`、`upsert_comments_target(...)`。
- `Upsert*TargetRequest` 可區分「未提供欄位」與「明確 false / 空值 / None」。
- Python 預設值集中於 `core/defaults.py`；Web UI、service、worker 不另寫一套。

## Scan Pipeline

- Web UI `scan-once` 只排入 resident scheduler request，不直接啟動 browser scan。
- Web UI scheduler start 只啟動 resident main；不接受 one-shot mode。
- `worker.resident_main` 是正式 queue-based resident 主路徑。
- `worker.one_shot_dispatch` 與 `scheduler.one_shot_loop` 是 fallback/debug path。
- `worker.posts_pipeline` / `worker.comments_pipeline` 處理 target-specific page preparation、sort、load-more、extract 與 diagnostics；各自透過 pipeline finalize helper 進 shared finalize。
- `worker.scan_orchestration` 放共用 login guard 與 scroll policy helper。
- `worker.scan_finalize` 集中處理 seen aliases、keyword classification、match history、notification outbox、latest scan snapshot 與 scan run commit。
- `worker.scan_failure_finalize` 集中失敗 scan run metadata。
- `worker.scan_metadata` 集中 posts/comments scan metadata shape。
- runtime status 只表示 executor 狀態：`idle / queued / running / error`。
- 使用者停止語義由 `Target.paused` + `TargetDesiredState.STOPPED` 表示。

## Notification

- Notification 採 outbox boundary。
- scan transaction 只寫 outbox 與 scan 結果。
- scan path 呼叫 enqueue + after-commit once 語義，不保留 direct dispatch API。
- 同一個 scan transaction 內 notification outbox dispatch hook 只註冊一次。
- commit 後立即 dispatch 只 claim 並處理 pending outbox；failed retry 必須走明確 retry API。
- outbox dispatch 在外部 I/O 前會把 pending row 原子 claim 成 `processing_pending`，failed retry row 原子 claim 成 `processing_failed`，避免多 connection 並發 commit 重複發送同一筆通知。
- 過期 `processing_pending` 只回 `pending`；過期 `processing_failed` 只回 `failed`，避免一般 scan commit 順手重試 failed row。
- 外部 notification I/O 必須在 commit 後執行，再回寫 outbox / notification event 狀態；sender raise 也要留下 failed event。

## Persistence

- SQLite schema version 目前為 v12。
- raw v10 代表舊 schema 與 v11 paused runtime status 已有 migration fixture 測試。
- 沒有 `schema_metadata` 的既有 DB 不支援自動升級，啟動時 fail fast。
- dashboard revision 使用單列 revision table + SQLite triggers，不透過完整 dashboard rows 或全表 fingerprint 重建 hash。

## Web UI 語義

- `webapp/app.py` 只負責 app assembly。
- routes 依 dashboard / targets / settings / scheduler 分模組。
- form parsing 集中於 `webapp.form_models`。
- scan diagnostics formatting 集中於 `webapp.diagnostics_presenter`。
- target 卡片的「開始 / 停止」是日常使用主開關。
- 「開始」清該 target seen scope、要求立即掃描並喚醒 scheduler。
- 「停止」只暫停排程，保留 seen/history。

## UI 重構邊界

- UI 重構可以修改 `webapp/routes/*`、`webapp/templates/*`、`webapp/static/*`、`webapp/query_service.py`、`webapp/*_presenter.py`、必要的 form/schema 與 application command DTO。
- UI 重構不得順手重寫 `worker/scan_finalize.py`、`worker/posts_pipeline.py`、`worker/comments_pipeline.py`、`worker/resident_main*`、`notifications/outbox_service.py`、`persistence/repositories/notification_outbox.py`、`facebook/feed_dom.py`、`facebook/comment_dom.py` 或 scheduler runtime。
- 若 UI 需要新的資料欄位，優先新增 read model / presenter；只有使用者操作語義真的改變時，才新增 application command。
- UI 重構不得把 one-shot mode、全域 scheduler 主開關、direct notification dispatch 或 legacy `target_configs` formal path 帶回正式路徑。

## Facebook 行為

- `auto_adjust_sort` 必須保留 preferred label、before/after label、attempted/changed/reason 與 mutation suppression diagnostics。
- `auto_load_more` 不能退回單純 `window.scrollBy(...)`；posts/comments 都要保留 scroll target、fallback、snapshot/restore 與 diagnostics。
- comments target 不是 posts 換 selector；必須保留 comment-specific extractor、canonicalization、cleanup、sort、nested scroll/load-more、dedupe、latest scan/history/notification persistence。
- posts/comments 可共用文字片段合併 helper；但 selector、permalink、sort、load-more 與 target scope 不應硬合併。
- Python resident main worker 目前是 polling；不得宣稱 mutation relevance 已接上即時觸發。

## Deliberate Divergence

- `enable_desktop_notification=False`：避免本機桌面通知在未確認環境前自動跳出。

## Deferred 邊界

Deferred 不代表可以半套偷補。以下項目未來若開工，必須包含設定、runtime 行為、diagnostics、tests 與文件：

- `loadMoreMode=wheel`
- `phase_offset_sec`
- one-shot scheduler queue 化
- fallback/debug path comments parity
- DOM helper 拆分
