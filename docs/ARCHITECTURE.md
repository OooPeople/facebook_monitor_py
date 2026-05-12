# 架構說明

本文件只記錄穩定架構事實與不可回退的產品語義。
文件職責邊界看 `docs/README.md`；目前狀態、下一步、風險與驗證結果看 `docs/TASK_BREAKDOWN.md`；新對話交接看 `docs/HANDOFF.md`。

## 核心原則

- JS userscript 是功能語義來源，不是逐行翻譯來源。
- Python 版可以重新分層與命名，但 target/scope、config、seen、notification、sort、load-more、diagnostics 等語義必須對齊 JS 成熟版本。
- Web UI 是正式日常入口；scheduler 是 Web UI 背後的內部背景服務，不是使用者第二個主開關。
- resident main worker 是正式產品主路徑；one-shot mode 與 sync resident worker 只作 fallback / debug tooling。
- 新功能預設先接正式 async resident 主路徑；fallback/debug path parity 必須作為獨立完整任務處理。

## 正式入口

- Web UI：`facebook-monitor`
- profile 登入 / 檢查：`facebook-monitor-login`
- 預設 data dir：`~/facebook_monitor_data`；`--portable` 則改用 app base dir 旁的 `data`。
- automation profile：`<data-dir>/profiles/automation_default`
- runtime paths 由 `facebook_monitor.runtime.paths.RuntimePaths` 集中解析；正式 Web UI 與 profile setup 共用 `--data-dir`、`--db-path`、`--profile-dir`、`--profile-name`、`--logs-dir`、`--portable`。
- `--profile-dir` 必須落在 `<data-dir>/profiles/` 底下；需要外部測試 profile 時只能用 debug-only `--unsafe-profile-dir`，且 resolver 會拒絕常見 Chrome / Edge / Chromium 日常 profile。
- `RuntimePaths` 同時解析 read-only web resources；source mode 使用 package 內 `webapp/templates` / `webapp/static`，future frozen bundle 可透過 `_MEIPASS` 尋找 bundled resources。
- app-level single-instance lock 由 `facebook_monitor.runtime.instance_lock` 管理：同一 data root 以 `<data-dir>/runtime/app.lock` / `server.json` 管理既有 server；resolved DB path 與 profile dir 另以全域 resource locks 分別互斥，避免同 DB 或同 profile 被兩個 app instance 共用。
- launcher 不指定 `--port` 時預設先使用 `4818`，若該 port 被占用才 fallback 到可用 port，並開啟瀏覽器；`--auto-port` / `--port 0` 可明確要求直接挑可用 port，`--port` 可指定固定 port，`--no-open-browser` 可關閉自動開啟。啟動前會解析實際 port，並用同一個 port 寫入 server info、startup diagnostics、open-browser URL 與 uvicorn。明確指定固定 port 被占用時會 fail-fast，不寫 server info，也不啟動 uvicorn。
- `GET /health` 供 launcher 判斷既有 Web UI server 是否存活；第二次啟動若同一 runtime dir 已有健康 server，會直接退出，不啟動第二份 scheduler。
- Web UI mutating routes 由 per-process CSRF token 保護；launcher 預設只允許 loopback host，非 loopback bind 需明確使用 `--allow-non-loopback-host`。
- launcher 會透過 `runtime.logging_setup` 與 `runtime.startup_diagnostics` 寫入 `logs/app.log`、`logs/error.log`、`logs/startup.log`；三者皆採單檔大小上限與固定備份數輪替。startup diagnostics 只記路徑、版本、browser mode 與啟動語義，不記 cookies/tokens/session dump。
- build/runtime metadata 由 `runtime.build_metadata` 集中建立；目前顯示 app version、asset version、Python version、executable、frozen、packaging mode、build date 與 git commit，future packaging 可用環境變數注入 build date / commit / packaging mode。
- 設定頁 runtime diagnostics 由 `webapp.runtime_diagnostics` presenter 建立，只讀取 app state / paths / scheduler state，不直接操作 scheduler 或 profile。
- script 分層依 `scripts/admin/`、`scripts/debug/`、`scripts/internal/`；不得新增新的 `phase_*` script。

## 模組邊界

- `core/`：資料模型、keyword rules、dedupe、refresh policy、Python 預設值。
- `application/`：context wiring、target registry/config/runtime、monitoring commands、scan recording、route detection。
- `persistence/`：SQLite connection、schema、migrations、row mappers、repositories、runtime data maintenance。
- `facebook/`：Facebook route detection、DOM extraction、permalink、sort / scroll helpers。
- `worker/`：posts/comments scan pipeline、shared finalize、failure finalize、resident main、fallback/debug workers。
- `scheduler/`：target planner、runtime recovery、one-shot fallback scheduler。
- `notifications/`：desktop / ntfy / Discord sender、safe diagnostics、channel dispatch、outbox service 與 manual test。
- `webapp/`：FastAPI app assembly、routes、form models、read model、diagnostics presenter、scheduler/profile session managers。

## Target 與 State

- posts target 代表社團貼文監視。
- comments target 代表單篇社團貼文留言監視。
- keyword / exclude ignore phrases / refresh / notification 是 target-scoped config。
- seen、baseline、latest scan、match history、notification events、runtime state 是 target-scoped。
- 正式路徑只讀寫 `target_configs[target_id]`；`group_configs` 只作 v14 migration 來源，fresh schema 不再建立此表。
- v14 migration 會把既有 `group_configs[group_id]` 複製成每個 target 各自的 `target_configs[target_id]` row。
- `targets(target_kind, scope_id)` 由 DB unique index 保護；v17 migration 會先合併歷史重複 scope，再建立唯一索引。
- target 建立 / 更新正式入口是 `upsert_group_posts_target(...)`、`upsert_comments_target(...)`。
- `Upsert*TargetRequest` 可區分「未提供欄位」與「明確 false / 空值 / None」。
- Python 預設值集中於 `core/defaults.py`；Web UI、service、worker 不另寫一套。

## Scan Pipeline

- Web UI `scan-once` 只排入 resident scheduler request，不直接啟動 browser scan。
- Web UI scheduler start 只啟動 resident main；不接受 one-shot mode。
- `worker.resident_main` 是正式 queue-based resident 主路徑。
- resident 啟動前會回收 stale `QUEUED` / `RUNNING` runtime state；`QUEUED` 過期會回到 `IDLE` 並保留立即掃描請求，`RUNNING` 過期會標成 `ERROR`。
- scheduler running 時新增 target 若缺自訂名稱，Web route 不同步搶 profile；先保存 fallback target name，再把 metadata refresh request 交給 resident scheduler，由既有 persistent browser context 開短期 page 補齊 `name/group_name`。
- `worker.one_shot_dispatch` 與 `scheduler.one_shot_loop` 是 fallback/debug path。
- 正式主路徑的 persistent browser context 由 `automation.browser_runtime` 集中建立；目前只正式支援 `playwright_chromium`，Chrome / Edge / custom executable 只保留選項介面，尚未宣稱支援。
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
- scan path 呼叫 enqueue + after-commit once 語義；正式 façade 只暴露 outbox/retry/manual-test 入口，不暴露低階 direct dispatch API。
- 同一個 scan transaction 內 notification outbox dispatch hook 只註冊一次。
- commit 後立即 dispatch 只 claim 並處理 pending outbox；failed retry 必須走明確 retry API。
- Web UI startup runtime cleanup 不刪 `notification_outbox`，避免 pending / failed 通知失去 retry 邊界。
- outbox dispatch 在外部 I/O 前會把 pending row 原子 claim 成 `processing_pending`，failed retry row 原子 claim 成 `processing_failed`，避免多 connection 並發 commit 重複發送同一筆通知。
- 過期 `processing_pending` 只回 `pending`；過期 `processing_failed` 只回 `failed`，避免一般 scan commit 順手重試 failed row。
- 外部 notification I/O 必須在 commit 後執行，再回寫 outbox / notification event 狀態；sender raise 也要留下 failed event。
- ntfy topic / Discord webhook 可在 UI 表單明文顯示以保留日常操作性；但 sender exception、manual test error、outbox last_error 與 notification event message 不得保存或回填 endpoint / token，只保留安全化錯誤類型。
- notification secrets 在 SQLite 內以 `cryptography` Fernet 加密保存；`target_configs`、`global_notification_settings` 與 `notification_outbox.endpoint` 的 secret 欄位由 repository 寫入前加密、讀取後解密。
- encryption key 放在 DB 同層的 `secrets.key`；DB 與 key 同時外流時仍可解密，這是本機 DB-at-rest 加密的安全邊界。
- 目前 notification outbox 是 commit-after immediate dispatch：scan transaction 先寫 match history / latest scan / outbox 並 commit，commit 成功後由新的 application context 發送 pending outbox，避免外部 I/O 共用原本 scan transaction lifecycle；尚未改成獨立常駐 background dispatcher。
- secret-bearing repositories 不提供隱性明文 codec 預設值；正式 application context 會注入 DB 對應 Fernet codec，測試若需要 legacy 明文資料必須明確傳入 `PlaintextSecretCodec`。
- 命中紀錄資料來源是 `match_history`，不需要等待 ntfy / Discord / desktop sender 全部完成才可讀取；notification event 只是命中紀錄的發送狀態補充資料。

## Persistence

- SQLite schema version 目前為 v17。
- raw v10 代表舊 schema、v11 paused runtime status 與 v12 歷史缺欄已有 migration fixture 測試。
- 既有 DB 必須有有效 `schema_metadata.version`；缺失、無效、為 `0` 或高於目前 app 支援版本時，一律 fail fast，不得靜默標成 current。
- `persistence/schema.py` 負責 current schema bootstrap 與 migration orchestration；既有 DB 欄位補齊一律進 `persistence/migrations.py` 明確版本鏈，不保留平行 schema repair 模組。
- 後續若升 schema version，必須在 `persistence/migrations.py` 新增明確版本鏈 migration，不得把新欄位 repair 直接塞回 schema bootstrap。
- dashboard revision 使用單列 revision table + SQLite triggers，不透過完整 dashboard rows 或全表 fingerprint 重建 hash。
- Web UI theme 是 app-level preference，正式來源是 `app_settings['theme']`；頁面初始值由 server 注入，前端切換時寫回 `/settings/theme`。

## Web UI 語義

- `webapp/app.py` 只負責 app assembly。
- `create_app(...)` 接受已解析的 `templates_dir` / `static_dir`，launcher 會傳入 `RuntimePaths` 的 resource paths；route 不自行推導 resource 位置。
- routes 依 dashboard / targets / settings / scheduler 分模組。
- form parsing 集中於 `webapp.form_models`。
- scan diagnostics formatting 集中於 `webapp.diagnostics_presenter`。
- dashboard target 狀態、設定摘要與收合摘要 presenter 集中於 `webapp.dashboard_presenters`，`TargetRow` 保持 read model 聚合角色。
- template 入口 CSS / JS cache key 集中於 `webapp.assets.ASSET_VERSION`；ES module 與 CSS 內部 import 不再分散手動版本字串。
- target 卡片的「開始 / 停止」是日常使用主開關。
- Web UI 啟動時預設停止 targets，不自動恢復上次 active targets；目前產品語義是使用者手動開始需要監視的 target。
- Web UI 不註冊全域 scheduler start/stop 日常 route；target 卡片開始/停止與 scan-once 只喚醒內部 resident scheduler。
- 「開始」清該 target seen scope、要求立即掃描並喚醒 scheduler。
- 「停止」只暫停排程，保留 seen/history。
- dashboard partial update 以 revision change detection 觸發，再用 batch card payload 更新 sidebar 與 target cards。
- `/api/dashboard-events` 是短生命週期 revision event stream：正常路徑約 1 秒偵測 revision 變更，瀏覽器會自動重連；它不是長連線 SSE 即時推送架構。`/api/dashboard-revision` polling 只作 EventSource 不可用或連線失敗時的 fallback。
- 命中紀錄 UI 稱 `match_history` 時間為「記錄時間」；API 暫留 `notified_at` legacy key 作相容，前端新欄位使用 `recorded_at`。

## UI 重構邊界

- UI 重構可以修改 `webapp/routes/*`、`webapp/templates/*`、`webapp/static/*`、`webapp/query_service.py`、`webapp/*_presenter.py`、必要的 form/schema 與 application command DTO。
- UI 重構不得順手重寫 `worker/scan_finalize.py`、`worker/posts_pipeline.py`、`worker/comments_pipeline.py`、`worker/resident_main*`、`notifications/outbox_service.py`、`persistence/repositories/notification_outbox.py`、`facebook/feed_dom.py`、`facebook/comment_dom.py` 或 scheduler runtime。
- 若 UI 需要新的資料欄位，優先新增 read model / presenter；只有使用者操作語義真的改變時，才新增 application command。
- UI 重構不得把 one-shot mode、全域 scheduler 主開關、direct notification dispatch 或 legacy `group_configs` formal path 帶回正式路徑。

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
- DOM script 片段內部更細粒度 helper 測試與再拆分
