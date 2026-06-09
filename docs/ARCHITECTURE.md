# 架構說明

本文件只記錄穩定架構事實、正式主路徑與不可回退的產品語義。操作步驟看 `docs/USAGE.md`；工具命令看 `docs/tooling.md`。本機進度與交接筆記屬於 `docs/local/`，不追蹤到 GitHub。

## 核心原則

- Python 版是目前正式維護主體；target/scope、config、seen、notification、sort、load-more、diagnostics 等語義以本 repo 的 domain、application、worker 與 Web UI 契約為準。
- 原始 userscript repo 只作為歷史背景與必要時的行為追溯來源，不是日常開發的本地 reference。
- Web UI 是正式日常入口；scheduler 是 Web UI 背後的背景服務，不是使用者第二個主開關。
- async resident worker 是正式產品主路徑；one-shot 與 sync resident worker 僅作 fallback / debug tooling。
- 新功能優先沿用 Python 版既有模組邊界、資料模型、diagnostics 與測試契約；若刻意改變既有產品語義，需在 handoff 中說明原因與風險。

## 正式入口與本機邊界

- Web UI：`facebook-monitor`
- profile 登入 / 檢查：`facebook-monitor-login`
- 預設 data dir：`~/facebook_monitor_data`
- 預設 automation profile：`<data-dir>/profiles/automation_default`
- runtime path 由 `facebook_monitor.runtime.paths.RuntimePaths` 集中解析，Web UI、登入工具、admin/debug scripts 共享同一套 data/profile/logs 解析規則。
- launcher 啟動 Web UI 前會做本機 profile gate：若 `app_settings.profile_session_status` 是 `needs_login`，或專用 profile 內找不到 Facebook `c_user` + `xs` cookie，會先開 Facebook 首頁登入視窗；登入完成後才啟動 Web UI。
- launcher 不做每次啟動的網路 session check；session 失效、checkpoint 或 login page 由 worker 掃描 guard 標記為 `needs_login`，並透過 dashboard 警告提示使用者重啟。
- `--profile-dir` 必須落在 `<data-dir>/profiles/` 底下；外部測試 profile 只能使用 debug-only `--unsafe-profile-dir`，且不得指向日常 Chrome / Edge / Chromium profile。
- app-level single-instance lock 與 DB/profile resource locks 避免同一 runtime、DB 或 automation profile 被多個 process 同時使用。
- Web UI 預設只綁 loopback；mutating routes 由 CSRF token 保護。同一個 runtime dir 會沿用本機 CSRF token，避免瀏覽器舊分頁在程式重啟後第一次送出表單時被誤擋。
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
- keyword、include keyword groups、exclude-ignore phrases、refresh、notification 都是 target-scoped config。
- seen、latest scan、match history、notification events、runtime state 都是 target-scoped state。
- 使用者按下「開始」只恢復監看並要求立即掃描；seen、logical item aliases、notification dedupe/outbox 狀態與 `match_history` / 查看紀錄都會保留。若要讓目前仍符合關鍵字的同一 item 可在下次掃描再次通知，使用者需在 target 更多操作中明確執行「重置通知狀態」；這會清該 target 的 `notification_outbox` rows、同一 scan scope 的 legacy `seen_items` 與目前 epoch 的 logical item aliases，推進 target-scoped dedupe epoch，並保留或建立 initialized `scan_scope_state`，避免下一輪變成 baseline suppressed scan。
- 正式 config store 是 `target_configs[target_id]`；`group_configs` 只保留為舊資料 migration 來源。
- target 建立 / 更新正式入口是 `upsert_group_posts_target(...)` 與 `upsert_comments_target(...)`。
- Python 預設值集中於 `core/defaults.py`；Web UI、service、worker 不另寫一套。
- `targets.group_cover_image_url` 只服務 Web UI 社團縮圖顯示，不參與 scan、seen/baseline 或 notification 判斷。
- cover image URL 自動刷新採被動即時策略：只有 dashboard/browser 判定縮圖載入失敗時，才送出輕量 hint 並排 image-only refresh job。主動低頻背景刷新目前明確不做，因為使用者未開 dashboard 時壞縮圖沒有產品影響，且定期開 Facebook 頁面會增加 session 與資源負擔。
- cover image refresh 狀態 owner 是 `target_cover_image_refresh_state`。該狀態不得混用 `targets.metadata_status`，避免 UI 把縮圖維護誤解成名稱 metadata refresh。失敗時保留舊 URL，result/error 可由 DB state 查詢。

## Scan Pipeline

- Web UI `scan-once` 只排入 resident scheduler request，不直接啟動 browser scan。
- `worker.resident_main` 是正式 queue-based resident 主路徑。
- resident 啟動前會回收 stale runtime state，避免重啟後 target 永遠卡在 queued/running。
- scheduler running 時新增 target 若缺自訂名稱，Web route 不同步搶 profile；先建立 target，再由 resident metadata refresh 補齊名稱。
- posts 與 comments pipeline 各自處理 page preparation、sort、load-more、extract 與 diagnostics，最後進 shared finalize。
- shared finalize 集中處理 logical item aliases、legacy `seen_items` mirror、keyword classification、match history、notification dedupe/outbox、latest scan snapshot 與 scan run commit。
- 單輪 scan 會先編譯 target keyword matcher，再對每個 item 評估；include keyword groups 採組內 OR、組間 AND，不展開笛卡兒積；通知沿用 `matched_keyword` 顯示成立的 include rules，group 診斷保留在 history、latest scan、diagnostics 與 UI highlight 資料中。
- runtime status 只描述 executor 狀態；使用者停止語義由 `Target.paused` 與 `TargetDesiredState.STOPPED` 表示。

## Notification 與 Secret

- Notification 採 outbox boundary：scan transaction 先寫 match data、notification dedupe reservation 與 outbox，commit 成功後才做外部 I/O。`notification_dedupe` 承擔長期防重複語義，`notification_outbox` 只保存投遞佇列與近期投遞狀態。
- failed outbox rows 不由一般 scan commit 自動重試；日常 UI 只顯示失敗筆數與清除入口，目前不提供 failed 通知重試入口。
- sender exception、manual test error、outbox last_error 與 notification event message 不得暴露 endpoint / token。
- desktop notification 是 target-scoped compact message，正式摘要只包含 `社團`、`類型`、`命中` 三行，內容由共用 payload builder 產生；平台 sender 只負責投遞，不自行重組內容。
- Windows desktop notification 的正式路徑由目前 process 內的 Win32 `Shell_NotifyIconW` tray owner 送出，使用 bundled / source tree 的 `facebook-monitor-tray.ico` / `facebook-monitor.ico`，不再以 PowerShell process 與系統 information icon 作為正式路徑。Windows icon asset 尺寸需求歸 `packaging/assets/README.md` 管理。
- macOS frozen `.app` 的正式桌面通知主路徑由 `Facebook Monitor.app` 母程序提供 AF_UNIX socket，worker 以 UTF-8 JSON 傳 title/body/identifier，再由母程序用 UserNotifications、app icon 與系統預設通知音效送出。若 macOS 拒絕主 app UserNotifications identity，sender 會保留 `desktop_failed:macos_permission_denied`，提示使用者允許 `Facebook Monitor` 通知，不改走第二個 app identity。直接呼叫 launcher 的 `--facebook-monitor-notify` 只保留為沒有母程序 socket 時的 frozen fallback / debug path。macOS source mode 沒有 bundle 時保留 `osascript` fallback；不支援的平台回傳結構化失敗，不讓 scan pipeline crash。
- Discord webhook 使用傳統 `content` payload 與 `allowed_mentions.parse=[]`；內容本文保留多行純文字格式並 escape Discord Markdown，命中規則只列在 `命中：` 欄位，Facebook 連結直接顯示 URL。Components V2 曾是較理想的頻道內排版選項，但手機通知 preview 無法穩定顯示必要摘要，因此正式路徑不使用 Components V2。
- ntfy topic / Discord webhook 在 UI 明文顯示是刻意產品語義，讓使用者能確認輸入值是否正確；這不代表 DB 也保存明文。
- SQLite 內的 notification secrets 由 repository boundary 以 `cryptography` Fernet 加密保存；application、worker 與 Web UI 的 domain model 維持明文。
- 目前加密欄位是 `target_configs.ntfy_topic`、`target_configs.discord_webhook`、`sidebar_group_config_templates.ntfy_topic`、`sidebar_group_config_templates.discord_webhook`、`global_notification_settings.ntfy_topic`、`global_notification_settings.discord_webhook` 與 `notification_outbox.endpoint`；`global_notification_settings` 只保留給既有 DB / secret storage 相容性，不再作為 Web UI 全域通知預設入口。
- 密文以 `enc:v1:` prefix 保存，讓 repository 能辨識密文與 legacy plaintext rows；舊版 plaintext rows 可讀回，正常重新保存時會改寫為密文。
- encryption key 放在 DB 同層的 `secrets.key`，正式 application context 依 DB 路徑載入或建立 key。
- DB 檔案單獨外流時，notification topic / webhook 不再直接裸露；DB 與 `secrets.key` 同時外流時仍可解密，這是本機 DB-at-rest 加密的安全邊界，不是 OS keychain 等級保護。

## Frozen Updater

- 目前正式更新目標支援 Windows PyInstaller onedir portable zip 與 macOS Apple Silicon onedir zip；source mode 只提供 GitHub Release 檢查，不把原始碼更新包裝成正式功能。
- App version 的唯一產品來源是 `pyproject.toml` 的 project version；release asset 檔名、GitHub tag 對齊、PyInstaller version resource 與 Web asset cache key 都應由此派生或被 release validation 檢查。`src/facebook_monitor/webapp/assets.py` 的 `ASSET_VERSION` 由 `APP_VERSION` 派生，不是第二個手動維護版本來源。
- macOS onedir 內包含 `Facebook Monitor.app` Finder / Dock native launcher；它啟動同一個 onedir 內的正式 `facebook-monitor` executable，並留在 Dock 作為可 Quit 的母程序與唯一 native notification owner。若舊版 updater 直接啟動新版 root `facebook-monitor` binary，新版 binary 會自動轉交給 `.app` launcher，updater 仍以 onedir 根目錄作為 app base dir。
- 設定頁只查 GitHub stable Release metadata；一般使用者 UI 不暴露 Preview / Stable channel 選擇、repository、asset 檔名或 SHA256 檔名。
- Release asset 檔名必須精確對齊 GitHub tag version：Windows 使用 `facebook-monitor-{version}-windows-portable.zip`，macOS arm64 使用 `facebook-monitor-{version}-macos-arm64-onedir.zip`，兩者都必須有同名 `.sha256`；同一個 release 還必須包含 `facebook-monitor-{version}-manifest.json` 與 detached signature。若 GitHub 只剩較舊 release，app 會用它做版本比較，但使用者看到的「最新版本」不會被較舊版本覆蓋。若 tag 與 zip 檔名版本不一致，更新檢查會視為不可用，不 fallback 到其他版本 zip。
- Web UI 只負責下載、驗證 Ed25519 signed manifest、交叉檢查 release zip 的 SHA256 / size、寫出 `<data-dir>/runtime/pending_update.json`，再啟動 temp updater 並要求主程式關閉。
- `facebook-monitor-updater.exe` / `facebook-monitor-updater` 是獨立 PyInstaller onedir entrypoint。從 Web UI 啟動時會複製 updater binary 與同層 `_internal/` 到唯一 temp 目錄，避免 updater 鎖住原 app base dir；舊 temp updater runtime copy 會依保留時間清理。
- updater 在主程式釋放 app instance lock 後，會重驗 signed manifest 與 zip SHA256、解壓 staging、檢查 zip safety limit、驗證 staging app root、備份目前 app files、替換 app files，並保留 `data/`。
- macOS updater 解壓 staging 時會保留 zip 內 POSIX executable bit 與安全的 tree-internal symlink，避免覆蓋後的 `facebook-monitor`、`facebook-monitor-updater`、bundled browser 或 PyInstaller runtime layout 失效；指向 app tree 外部的 symlink 會被拒絕。
- pending update path 必須受 runtime path resolver 管理；`zip_path` 必須位於 `<data-dir>/updates/`，`runtime_dir` 必須是 `<data-dir>/runtime`，DB/profile 路徑必須留在 data tree 內，logs 路徑若在 app root 內則必須位於 data tree 內。
- 成功套用後 updater 會清除本次下載 zip、`.sha256`、signed manifest / `.sig`、pending handoff 與 staging，並只保留本次成功套用產生的 1 份 updater 管理的 app backup 供人工追查或 rollback；cleanup 失敗只寫入 `updater.log cleanup_warning`，不反轉已成功的套用結果。
- 套用成功且 `--restart` 啟用時，updater 會用 pending handoff 內的 data/db/profile/logs 路徑啟動新版 app。
- updater 不接觸 cookies、tokens、browser profile 內容、DB schema migration rollback、notification outbox 或 Facebook scan pipeline。
- release artifact validation 是發佈前 gate，負責確認版本、manifest / signature、platform layout 與私密 runtime data 邊界；詳細 checklist 放在 `packaging/README.md`。這是發佈前檢查，不是 runtime updater 的替代品。
- Ed25519 signed manifest 是目前免費 updater 信任鏈，驗證 release metadata 由受信任 key 簽出；SHA256 只作 zip 完整性與交叉檢查。Windows Authenticode / macOS Developer ID signing 與 notarization 尚未導入，因此 OS 層發布者身分提示仍需由 release note 說明。

## Persistence

- SQLite schema 使用明確版本與 migration chain。
- 既有 DB 必須有有效 `schema_metadata.version`；缺失、無效或高於目前 app 支援版本時 fail fast。
- 新欄位或資料轉換必須進 `persistence/migrations.py`，不得把既有 DB repair 塞回 current schema bootstrap。
- dashboard revision 使用單列 revision table + SQLite triggers，支援 Web UI partial update。
- Web UI theme 是 app-level preference，正式來源是 `app_settings['theme']`；尚未保存偏好時預設為 `dark`。
- Runtime data maintenance 只清可重建 scan/debug 資料與可選的 legacy `seen_items` mirror；不得把 `notification_outbox` 當作一般 runtime/debug 資料清掉。bounded retention 另行清理 60 天外 logical/dedupe state 與短期 terminal outbox rows。
- Local dedupe 採 60 天 bounded horizon：`logical_items` / `logical_item_aliases` 以 `last_seen_at` 保留 60 天，`notification_dedupe` 以 `last_deduped_at` 保留 60 天；超過 horizon 的舊 item 若再次出現，可能被視為新的可通知項目。
- bounded retention 會短留 terminal outbox rows：`sent` / `skipped` 預設保留 7 天，`failed` / `processing_failed` 預設保留 14 天供診斷；`pending` / `processing_pending`、latest scan 仍引用的 logical item 與 active notification dedupe references 會被保護。

## Web UI 語義

- target 卡片的「開始 / 停止」是日常使用主開關。
- Web UI 啟動時預設停止 targets，不自動恢復上次 active targets。
- Web UI 啟動時的 runtime data cleanup 只清可重建的 scan/debug snapshot（`scan_runs`、`latest_scan_items`、`notification_events`），不得清 `seen_items`、logical item aliases、notification dedupe/outbox、`match_history` 或 `scan_scope_state`；否則啟動後第一輪掃描會被誤判成 baseline suppressed scan，造成新命中不通知。
- Web UI 不註冊全域 scheduler start/stop 日常 route。
- 「開始」會保留 seen scope、logical item aliases、notification dedupe 與 notification outbox rows，只要求立即掃描並喚醒 scheduler。
- 「停止」只暫停排程，保留 seen/history。
- 「重置通知狀態」位於 target 卡片更多操作，會清該 target 的 notification outbox rows（包含待送、處理中、失敗、已送出或略過的投遞狀態）、同一 scan scope 的 legacy seen items 與目前 epoch 的 logical item aliases，並推進 target-scoped dedupe epoch。它會保留或建立 initialized `scan_scope_state`，但不清 match history 或設定，因此下一輪不是 baseline suppressed scan；若同一貼文或留言仍符合關鍵字，會被視為 new 並可再次通知。
- target card header 顯示 target identity、target kind、最近掃描與下次刷新；左側圓形位置保留給社團縮圖。
- 社團縮圖載入失敗時，UI 會立即退回文字 avatar，並在同一頁面 session 中針對同一 target/URL 只上報一次。這個上報只排 image-only maintenance job，不直接開 Facebook，也不標記 target 掃描錯誤。
- target 設定中的「重新抓取名稱與封面」是手動 metadata refresh；使用者按下後允許用 Facebook 抓到的社團名稱覆蓋 target 顯示名稱。若只要修復壞縮圖，應使用 UI 壞圖自動上報觸發的 image-only flow，不應改動此手動按鈕語義。
- 貼文 / 留言模式 chip 是 target kind 分類標籤，不是執行狀態 badge，也不得與 `已啟用` / `已停止` 混淆。
- 右側結果 panel header 可顯示最近一輪 scan cycle result；這是掃描結果摘要，不是錯誤或使用者停止狀態。
- 最近通知摘要不放在 target card header；通知狀態由 notification events、outbox diagnostics 與相關 read model 承接。
- dashboard partial update 以 revision change detection 觸發，再用 batch payload 更新 sidebar 與 target cards。
- 命中紀錄 UI 稱 `match_history` 時間為「記錄時間」；API 暫留 `notified_at` legacy key 作相容。
- UI 若需要新資料，優先新增 read model / presenter；不得為了 UI 小修順手重寫 worker、notification outbox、scheduler runtime 或 Facebook DOM helper。

## Sidebar Layout 與 Group Template

- Sidebar layout 是 Web UI 呈現與操作順序，不改變 `TargetRepository.list_all()` 或 scheduler 掃描順序。
- Sidebar group、target placement 與 group template 由 `SidebarLayoutService` 集中處理；route 不直接組合多段 repository write。
- 排序保存必須用單一 layout command 同時更新 group order 與 target placements，避免只保存一半狀態。
- Dashboard read model 可以依 placement 排列 rows，但不得為缺失 placement 寫入 DB；缺失 placement 採 lazy fallback 顯示在未分組區，補寫只可由明確排序保存 command 或 migration 進行。
- 舊平面 target order API 只能用在沒有 grouped placement 的相容情境；已有 grouped placement 時不得打平 sidebar 狀態。
- Group template 只是批次套用工具，不是 config fallback owner；正式 target config 仍只讀寫 `target_configs[target_id]`。
- 新增 group 時會 snapshot 當下全域 keyword defaults 到 group template；通知設定使用系統預設，不自動繼承全域通知或任一 target，既有 group template 不跟著全域設定靜默覆蓋。
- Group template 套用是破壞性批次覆蓋操作，必須經使用者確認並在 application transaction 內完成。
- Sidebar group 開始 / 停止只批次套用各 target 的開始 / 停止語義；不另定義 group-scoped runtime state，也不清 seen、match history 或 notification outbox。

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
- fallback/debug path comments feature completeness
- DOM script 片段內部更細粒度 helper 測試與再拆分
