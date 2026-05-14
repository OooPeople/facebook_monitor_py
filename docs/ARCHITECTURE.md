# 架構說明

本文件只記錄穩定架構事實、正式主路徑與不可回退的產品語義。操作步驟看 `docs/USAGE.md`；工具命令看 `docs/tooling.md`；目前狀態與驗證看 `docs/TASK_BREAKDOWN.md`；新對話接手看 `docs/HANDOFF.md`。

## 核心原則

- JS userscript 是功能語義來源，不是逐行翻譯來源。
- Python 版可以重新分層與命名，但 target/scope、config、seen、notification、sort、load-more、diagnostics 等語義必須對齊 JS 成熟版本。
- Web UI 是正式日常入口；scheduler 是 Web UI 背後的背景服務，不是使用者第二個主開關。
- async resident worker 是正式產品主路徑；one-shot 與 sync resident worker 僅作 fallback / debug tooling。
- 新功能若涉及 JS 已有成熟行為，先對照 `docs/REFERENCE_MAP.md` 指向的 userscript，再實作 Python 版。

## 正式入口與本機邊界

- Web UI：`facebook-monitor`
- profile 登入 / 檢查：`facebook-monitor-login`
- 預設 data dir：`~/facebook_monitor_data`
- 預設 automation profile：`<data-dir>/profiles/automation_default`
- runtime path 由 `facebook_monitor.runtime.paths.RuntimePaths` 集中解析，Web UI、登入工具、admin/debug scripts 共享同一套 data/profile/logs 解析規則。
- `--profile-dir` 必須落在 `<data-dir>/profiles/` 底下；外部測試 profile 只能使用 debug-only `--unsafe-profile-dir`，且不得指向日常 Chrome / Edge / Chromium profile。
- app-level single-instance lock 與 DB/profile resource locks 避免同一 runtime、DB 或 automation profile 被多個 process 同時使用。
- Web UI 預設只綁 loopback；mutating routes 由 CSRF token 保護。
- runtime logs 與 startup diagnostics 只記啟動語義與環境資訊，不記 cookies、tokens 或 session dump。

## 模組邊界

- `core/`：資料模型、預設值、compiled keyword rules、dedupe、refresh policy。
- `application/`：use cases、target registry/config/runtime、monitoring commands、scan recording。
- `persistence/`：SQLite schema、migrations、repository、runtime data maintenance。
- `facebook/`：Facebook route detection、permalink、DOM extraction、sort 與 scroll helper。
- `worker/`：posts/comments scan pipeline、shared finalize、resident worker 與 fallback/debug workers。
- `scheduler/`：target planner、runtime recovery、one-shot fallback scheduler。
- `notifications/`：desktop / ntfy / Discord sender、safe diagnostics、channel dispatch、outbox service。
- `webapp/`：FastAPI assembly、routes、form models、read model、presenters、templates、static modules。
- `scripts/`：低頻 admin、debug、internal tools；不得新增新的 `phase_*` script，也不得把 debug tool 包裝成日常入口。

## Target 與 State

- posts target 代表社團貼文監視。
- comments target 代表單篇社團貼文留言監視。
- target identity 由 `target_kind + scope_id` 決定，並由 DB unique index 保護。
- keyword、exclude-ignore phrases、refresh、notification 都是 target-scoped config。
- seen、baseline、latest scan、match history、notification events、runtime state 都是 target-scoped state。
- 正式 config store 是 `target_configs[target_id]`；`group_configs` 只保留為舊資料 migration 來源。
- target 建立 / 更新正式入口是 `upsert_group_posts_target(...)` 與 `upsert_comments_target(...)`。
- Python 預設值集中於 `core/defaults.py`；Web UI、service、worker 不另寫一套。

## Scan Pipeline

- Web UI `scan-once` 只排入 resident scheduler request，不直接啟動 browser scan。
- `worker.resident_main` 是正式 queue-based resident 主路徑。
- resident 啟動前會回收 stale runtime state，避免重啟後 target 永遠卡在 queued/running。
- scheduler running 時新增 target 若缺自訂名稱，Web route 不同步搶 profile；先建立 target，再由 resident metadata refresh 補齊名稱。
- posts 與 comments pipeline 各自處理 page preparation、sort、load-more、extract 與 diagnostics，最後進 shared finalize。
- shared finalize 集中處理 seen aliases、keyword classification、match history、notification outbox、latest scan snapshot 與 scan run commit。
- 單輪 scan 會先編譯 target keyword matcher，再對每個 item 評估；多組 include 命中會完整保留給通知、history、latest scan 與 UI highlight。
- runtime status 只描述 executor 狀態；使用者停止語義由 `Target.paused` 與 `TargetDesiredState.STOPPED` 表示。

## Notification 與 Secret

- Notification 採 outbox boundary：scan transaction 先寫 match data 與 outbox，commit 成功後才做外部 I/O。
- failed outbox retry 必須走明確 retry API；一般 scan commit 不順手重試 failed row。
- sender exception、manual test error、outbox last_error 與 notification event message 不得暴露 endpoint / token。
- ntfy topic / Discord webhook 在 UI 明文顯示是刻意產品語義，讓使用者能確認輸入值是否正確；這不代表 DB 也保存明文。
- SQLite 內的 notification secrets 由 repository boundary 以 `cryptography` Fernet 加密保存；application、worker 與 Web UI 的 domain model 維持明文。
- 目前加密欄位是 `target_configs.ntfy_topic`、`target_configs.discord_webhook`、`global_notification_settings.ntfy_topic`、`global_notification_settings.discord_webhook` 與 `notification_outbox.endpoint`。
- 密文以 `enc:v1:` prefix 保存，讓 repository 能辨識密文與 legacy plaintext rows；舊版 plaintext rows 可讀回，正常重新保存時會改寫為密文。
- encryption key 放在 DB 同層的 `secrets.key`，正式 application context 依 DB 路徑載入或建立 key。
- DB 檔案單獨外流時，notification topic / webhook 不再直接裸露；DB 與 `secrets.key` 同時外流時仍可解密，這是本機 DB-at-rest 加密的安全邊界，不是 OS keychain 等級保護。

## Persistence

- SQLite schema 使用明確版本與 migration chain。
- 既有 DB 必須有有效 `schema_metadata.version`；缺失、無效或高於目前 app 支援版本時 fail fast。
- 新欄位或資料轉換必須進 `persistence/migrations.py`，不得把既有 DB repair 塞回 current schema bootstrap。
- dashboard revision 使用單列 revision table + SQLite triggers，支援 Web UI partial update。
- Web UI theme 是 app-level preference，正式來源是 `app_settings['theme']`。
- Runtime data maintenance 只清可重建 scan/debug 資料與可選的 `seen_items`；不得清除 `notification_outbox`。

## Web UI 語義

- target 卡片的「開始 / 停止」是日常使用主開關。
- Web UI 啟動時預設停止 targets，不自動恢復上次 active targets。
- Web UI 不註冊全域 scheduler start/stop 日常 route。
- 「開始」會清該 target seen scope 與 notification outbox 去重 rows、要求立即掃描並喚醒 scheduler。
- 「停止」只暫停排程，保留 seen/history。
- dashboard partial update 以 revision change detection 觸發，再用 batch payload 更新 sidebar 與 target cards。
- 命中紀錄 UI 稱 `match_history` 時間為「記錄時間」；API 暫留 `notified_at` legacy key 作相容。
- UI 若需要新資料，優先新增 read model / presenter；不得為了 UI 小修順手重寫 worker、notification outbox、scheduler runtime 或 Facebook DOM helper。

## Sidebar Layout 與 Group Template

- Sidebar layout 是 Web UI 呈現與操作順序，不改變 `TargetRepository.list_all()` 或 scheduler 掃描順序。
- Sidebar group、target placement 與 group template 由 `SidebarLayoutService` 集中處理；route 不直接組合多段 repository write。
- 排序保存必須用單一 layout command 同時更新 group order 與 target placements，避免只保存一半狀態。
- Dashboard read model 可以依 placement 排列 rows，但不得為缺失 placement 寫入 DB；補寫必須留在 application command 或 migration。
- 舊平面 target order API 只能用在沒有 grouped placement 的相容情境；已有 grouped placement 時不得打平 sidebar 狀態。
- Group template 只是批次套用工具，不是 config fallback owner；正式 target config 仍只讀寫 `target_configs[target_id]`。
- 新增 group 時會 snapshot 當下全域 keyword defaults 到 group template；既有 group template 不跟著全域設定靜默覆蓋。
- Group template 套用是破壞性批次覆蓋操作，必須經使用者確認並在 application transaction 內完成。

## Web UI 共用互動元件

- 會改狀態的 dashboard JSON fetch 走共用 CSRF helper。
- 確認與輸入類彈窗走共用 dynamic dialog module，不使用瀏覽器原生 `confirm/prompt/alert`。
- 內容型 modal 可以保留 Jinja `<dialog>`，但關閉/backdrop 行為走共用 helper。
- Modal 關閉入口遵守單一可見 dismiss pattern：read-only modal 用右上角關閉；form/action/confirm/prompt modal 用底部取消或取消按鈕，不同時顯示兩套。
- Web UI icon 使用 inline SVG，避免文字 glyph 造成跨字型對齊差異。
- Button 以共用 `button, .button` 與 modifier class 為基礎；局部 class 只保留尺寸、位置或狀態差異。

## Facebook 行為邊界

- `auto_adjust_sort` 必須保留 preferred label、before/after label、attempted/changed/reason 與 diagnostics。
- `auto_load_more` 不能退回單純 `window.scrollBy(...)`；posts/comments 都要保留 scroll target、fallback、snapshot/restore 與 diagnostics。
- comments target 不是 posts 換 selector；必須保留 comment-specific extractor、canonicalization、cleanup、sort、nested scroll/load-more、dedupe、latest scan/history/notification persistence。
- posts/comments 可共用純文字片段處理，但 selector、permalink、sort、load-more 與 target scope 不硬合併。
- Python resident main worker 目前是 polling；不得宣稱 mutation relevance 已接上即時觸發。

## Deferred 邊界

以下項目尚未作為正式功能完成。未來若開工，必須包含設定、runtime 行為、diagnostics、tests 與文件：

- `loadMoreMode=wheel`
- `phase_offset_sec`
- one-shot scheduler queue 化
- fallback/debug path comments parity
- DOM script 片段內部更細粒度 helper 測試與再拆分
