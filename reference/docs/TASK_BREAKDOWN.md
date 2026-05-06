# Task Breakdown

這份文件是後續工作的任務拆解入口。`V1_SPEC.md`、`REFACTOR_PLAN.md`、`STATE_REFACTOR_PLAN.md` 已移到 `docs/archive/` 作為已完成紀錄；新增功能或整理工作時，優先在本文件補任務，再依 `ARCHITECTURE_PLAN.md` 的邊界落實。

`HANDOFF_PLAN.md` 先保留空白，等下一個具體任務確定後再寫交接內容。

## 目前狀態

已完成：

- V1 Tampermonkey userscript 主功能已落地。
- include / exclude 關鍵字規則已可用。
- GM desktop、ntfy、Discord Webhook 通知已可用，且可在設定中獨立勾選；遠端通知維持 opt-in。
- group-scoped config、scope-scoped seen items、最上方項目 snapshot、latest scan cache 已落地。
- match history 已整理為全域最近紀錄。
- permalink / postId / commentId / fallback key 的去重策略已落地。
- baseline mode、top-item shortcut、feed-only seen-stop、保守 scroll load-more 已落地。
- 單篇貼文留言模式已落地，支援已載入 DOM 掃描與 scroll-only 自動載入更多留言。
- scan target / scope 邊界已定義：config 仍以社團為 scope，baseline / seen 以 scan target 為 scope。
- observer root 與 mutation relevance 已有 target-aware 入口；留言模式會依 comment permalink、留言文字與 direct-target attributes / characterData 訊號觸發 mutation 重掃，並有 mutation suppression 避免排序操作自觸發。
- 主面板、設定視窗、help modal、history modal、debug panel、panel drag 已落地。
- `STATE` runtime 分區與 mutation helper 已完成。
- `scripts/smoke_check_userscript.js` 已覆蓋主要純邏輯與 policy helper。

目前保留的已知取捨：

- 專案仍是單檔 userscript，不拆多檔、不導入 bundler。
- `STATE` 仍是單一 mutable object，只是已分區與收斂寫入入口。
- Facebook DOM selector 仍是最大外部風險。
- `timestampText` / `timestampEpoch` 保留資料欄位，但目前不做時間抽取。
- panel position 是全域設定，不分社團。
- 背景分頁或最小化視窗時，refresh timer 可能受瀏覽器節流影響。

## 文件整理任務

本輪文件整理目標：

- [x] 保留舊文件作為歷史與完成紀錄。
- [x] 建立 `ARCHITECTURE_PLAN.md` 作為目前架構索引。
- [x] 建立 `TASK_BREAKDOWN.md` 作為後續任務入口。
- [x] 建立 `COMMENT_MONITOR_PLAN.md` 作為單篇貼文留言監控的詳細實作計畫。
- [ ] 等下一個具體任務明確後，再撰寫 `HANDOFF_PLAN.md`。

可選的後續文件清理：

- [ ] 視需要更新 `README.md` 的文件索引，標註哪些文件是目前入口、哪些是歷史紀錄。
- [x] 將舊的 `V1_SPEC.md`、`REFACTOR_PLAN.md`、`STATE_REFACTOR_PLAN.md` 移到 `docs/archive/`。
- [ ] 新增功能完成後，同步更新 `docs/USAGE.md` 與必要的架構文件。

## 目前具體功能計畫

- [x] 單篇貼文留言監控：已完成 target-aware scope、target-aware 排序偵測與掃描前自動切回偏好排序、target-aware observer relevance、debug 顯示、scroll-only 自動載入更多留言、最上方留言快篩與 scan capability 顯示。
- [ ] 若後續要支援點擊「查看更多留言」或「查看先前留言」，必須獨立設計，不混入目前 scroll-only collector。
- [ ] 若後續需要 target-scoped config，先設計 `configScopeId`，不要在現有 group-scoped config helper 中直接散落 `if comments`。

## 新功能前置流程

每個新功能開始前，先完成下面幾件事：

1. 明確寫出使用者可見行為。
2. 判斷是否會把資料送出本機；若會，必須維持 opt-in。
3. 判斷主要改動區段：config、matcher、extractor、scan、notifier、UI、storage 或 lifecycle。
4. 確認是否會影響 baseline、dedupe、top-item shortcut、seen-stop。
5. 先決定 smoke test 要補哪個純邏輯或 policy helper。
6. 實作後執行：

```powershell
node .\scripts\smoke_check_userscript.js
```

7. 若功能涉及 Facebook 實頁、Tampermonkey 權限或通知端點，補手動驗證步驟。

## 任務分類

### 1. 設定與 UI 類

適用情境：

- 新增設定欄位。
- 調整 refresh / scan 參數。
- 新增或修改設定視窗欄位。
- 新增 debug 顯示欄位。

建議修改點：

- `DEFAULT_CONFIG`
- `CONFIG_FIELD_DEFINITIONS`
- `CONFIG_GROUP_DEFINITIONS`
- `build...ConfigPatch()`
- `persist...Config()`
- settings modal draft flow
- panel view state / debug rows
- smoke test config helper

注意事項：

- group-scoped 設定要確認是否加入 `GROUP_SCOPED_CONFIG_GROUPS`。
- internal-only policy 不要混進正式 config。
- UI 欄位儲存後應能 reload 保留。

### 2. 關鍵字與比對類

適用情境：

- 修改 include / exclude 語法。
- 新增大小寫、符號、同義詞或進階規則。
- 調整空白與分號規則。

建議修改點：

- `normalizeForMatch()`
- `buildKeywordRule()`
- `parseKeywordInput()`
- `matchesKeywordRule()`
- `matchRules()`
- help modal 與 `docs/USAGE.md`
- smoke test keyword matching

注意事項：

- include 空白目前代表「所有貼文先視為 include 命中」。
- exclude 命中優先抑制通知。
- 語法越複雜，越需要保持 debug 顯示可診斷。

### 3. Extractor / DOM 類

適用情境：

- Facebook DOM 變動造成貼文抓不到。
- 新增貼文欄位。
- 改善 author / permalink / postId / text 抽取。

建議修改點：

- `SELECTORS`
- `TEXT_PATTERNS`
- `REGEX_PATTERNS`
- `collectPostContainers()`
- `preparePostContainerForExtraction()`
- `extractPostRecord()`
- permalink / postId helper
- debug panel scan item rows

注意事項：

- 不要同一輪同時重構 scan orchestration。
- 優先使用穩定結構、URL、ARIA 或資料屬性，避免依賴易變 CSS class。
- 任何可能增加頁面互動的抽取策略都要保守。
- 若新增 fixture，必須先去識別化。

### 4. Scan / Dedupe 類

適用情境：

- 調整掃描深度。
- 調整 auto-load-more。
- 調整 baseline / seen / history 行為。
- 修改 top-item shortcut 或 seen-stop。

建議修改點：

- `SCAN_LIMITS`
- `collectScanItems()`
- `collectFeedPostsAcrossWindows()`
- `buildScanItemSummary()`
- `commitScanState()`
- `getPostKey()` / `getPostKeyAliases()`，目前名稱保留但語意已涵蓋 scan item
- `markItemSeen()`
- `addMatchHistory()`
- smoke test identity / seen / history

注意事項：

- 同一個 scan item 在 id、permalink 或 fallback 欄位變動時，仍應盡量被視為同一項。
- seen store 是 per-scope；不要清掉其他社團或其他單篇貼文留言 scope。
- match history 是全域最近清單；不要重新切回 per-group，除非有明確需求。
- 手動開始目前語義是 restart current target，會清掉目前 scan scope 的 seen baseline。

### 5. Notification 類

適用情境：

- 新增通知通道。
- 調整通知文字。
- 加入通知失敗診斷。

建議修改點：

- `NOTIFICATION_CHANNEL_DEFINITIONS`
- `buildNotificationChannelRunnerMap()`
- `createNotificationChannelTasks()`
- `getNotificationFields()`
- `buildCompactNotificationBody()`
- `buildRemoteNotificationBody()`
- settings modal
- `docs/USAGE.md`
- smoke test notification formatting

注意事項：

- 遠端通知必須 opt-in。
- 通道開關屬於 notification config group，端點與通道狀態要一起經過 settings modal / persist helper。
- 不要把 token、webhook、topic 寫進範例預設值。
- 測試通知不得寫入 seen 或 match history。
- Discord 內容目前會裁切到安全長度，新增通道也要注意大小限制。

### 6. Lifecycle / Scheduler 類

適用情境：

- 調整 route change 行為。
- 調整 refresh / scan debounce。
- 調整 observer 或 panel 補掛策略。

建議修改點：

- `scheduleScan()`
- `scheduleRefresh()`
- `installObserver()`
- `handleRouteTransition()`
- `startMaintenanceLoops()`
- scheduler runtime patch helper

注意事項：

- Facebook 是 SPA，route change 後要保留 settle window。
- 暫停時不應安排 scan 或 refresh。
- 不要製造多個 observer 或 timer handle。
- panel 被 Facebook 重掛移除時仍需能補回。

## 低優先維護任務池

目前不急，但未來可視情況處理：

- [ ] 若 panel 更新開始造成輸入干擾，再拆更細的局部更新。
- [ ] 若新功能增加更多 runtime transition，再補 runtime transition 文件。
- [ ] 若 selector 維護成本升高，再為 Facebook DOM 依賴補更集中的 debug checklist。
- [ ] 若通知通道增加到三個以上遠端通道，再考慮更明確的 adapter 形狀。
- [ ] 若 smoke test 開始太長，可依主題拆 helper，但仍維持無第三方依賴。

目前不建議投入：

- [ ] 不做 bundler / package manager 化。
- [ ] 不做 Redux / reducer / dispatcher 化。
- [ ] 不做 class-heavy 重寫。
- [ ] 不做 headless browser 背景監控。
- [ ] 不做登入、發文、留言、按讚、加入社團或私訊 automation。

## 手動驗證清單

涉及實頁功能時，至少確認：

1. 在 `https://www.facebook.com/groups/<group-id>/` 可看到主面板。
2. include / exclude 儲存後 reload 仍保留。
3. 暫停後不掃描、不 refresh。
4. 從暫停切回開始會重新開始目前社團監控。
5. debug panel 可開關，且能看到最近掃描摘要。
6. `查看紀錄` 可顯示命中紀錄。
7. 設定視窗可保存 refresh、掃描數量、通知通道與端點。
8. 測試通知可送出，不寫入 seen / history。
9. 新命中貼文只通知一次。
10. exclude 命中可抑制通知。
11. 多社團使用時，各社團設定與 seen state 不互相污染。
12. 若調整 extractor，確認至少一篇真實貼文能取得 text，並盡量取得 permalink 或 postId。

## 完成定義

一個新任務完成前，至少要符合：

- 程式變更集中在對應架構區段。
- 沒有新增預設外送資料的行為。
- 沒有引入未討論的第三方依賴。
- `node .\scripts\smoke_check_userscript.js` 通過。
- 若有使用者可見行為，已更新 `docs/USAGE.md` 或在任務文件中留下手動驗證步驟。
- 若需要交接，最後再填 `HANDOFF_PLAN.md`。
