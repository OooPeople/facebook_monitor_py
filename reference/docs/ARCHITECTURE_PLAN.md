# Architecture Plan

這份文件是目前專案的架構索引。舊的 `V1_SPEC.md`、`REFACTOR_PLAN.md`、`STATE_REFACTOR_PLAN.md` 已移到 `docs/archive/` 作為完成紀錄；後續新增功能時，優先以本文件描述的現況與邊界為準。

## 目前定位

`src/facebook_group_refresh.user.js` 是一支單檔 Tampermonkey userscript，執行於 `https://www.facebook.com/groups/*`。

它的核心目標是：

- 在使用者已登入 Facebook 的瀏覽器頁面中監看單一社團。
- 從目前社團動態牆抽取少量最近貼文，或從單篇貼文頁抽取留言；留言模式可在自動載入更多啟用時以 scroll-only 方式保守載入更多。
- 以 include / exclude 關鍵字判斷是否通知。
- 對 scan target 做 scope-scoped 去重，避免重複提醒。
- 只透過使用者明確啟用的通知通道送出遠端通知。
- 維持保守頁面互動，不加入登入、發文、留言、按讚、加入社團、私訊或大量爬取能力。

## 單檔分層

目前部署仍維持單一 `.user.js`，但檔案內部已依責任分段。後續修改時應優先沿用這些區段，而不是先導入 bundler 或框架。

主要區段：

- `Storage / Config`：storage key、設定載入、舊資料 migration、group-scoped 設定與 store facade。
- `Config Use Cases`：keyword、refresh、notification、monitoring、UI 設定的 patch / persist 入口。
- `Text / Common Utils`：文字正規化、HTML escape、數值裁切、panel 位置計算、clipboard 等共用工具。
- `Matcher / Rules`：include / exclude 規則解析與比對。
- `Page Context / Scheduling`：scan target 判斷、社團名稱、排序偵測、refresh / scan 排程。
- `Extractor / DOM Collection`：observer root、候選貼文 / 留言容器、文字展開、permalink warmup、id / author / text 抽取。
- `Post Parsing / Notification Formatting`：scan item identity fragment、通知欄位與通知文字格式。
- `Persistence / Dedupe / History`：seen items、match history、最上方項目 snapshot、latest scan cache。
- `Scan Engine`：單輪掃描 orchestration、跨視窗收集、top-item shortcut、seen-stop、include / exclude 摘要、commit state。
- `Notifier`：GM desktop、ntfy、Discord Webhook 的通知任務分發。
- `UI / Modal`：主面板、debug、設定視窗、說明視窗、歷史紀錄視窗、拖曳位置。
- `Lifecycle / Observer`：啟動流程、MutationObserver、Facebook SPA route 監看、panel 補掛。

## 設定與儲存

正式設定集中於 `DEFAULT_CONFIG` 與 config patch helper。

目前主要設定：

- `includeKeywords`
- `excludeKeywords`
- `paused`
- `debugVisible`
- `enableGmNotification`
- `enableNtfyNotification`
- `enableDiscordNotification`
- `ntfyTopic`
- `discordWebhook`
- `maxPostsPerScan`
- `scanDebounceMs`
- `minRefreshSec`
- `maxRefreshSec`
- `jitterEnabled`
- `fixedRefreshSec`
- `autoLoadMorePosts`
- `autoAdjustSort`
- `matchHistoryGlobalLimit`

`INTERNAL_CONFIG` 只放內部 policy，例如目前的 `loadMoreMode`。不要把 internal-only 能力混進正式使用者設定，除非它真的要成為 UI 可調功能。

儲存策略：

- 優先使用 Tampermonkey `GM_getValue` / `GM_setValue` / `GM_deleteValue`。
- 舊版 `localStorage` 只作為 migration fallback。
- include / exclude、通知通道與端點、paused、refresh 等設定已改為 per-group bucket；同社團的貼文模式與留言模式共用設定。
- `seenPosts` 使用 scan scope 獨立 key：社團貼文模式使用社團 ID，單篇貼文留言模式使用 `groupId:post:parentPostId:comments`。
- `latestTopPosts`、`latestScanPosts` 使用獨立 key；社團貼文模式以 group id 為 key，單篇貼文留言模式以 scan scope id 為 key，服務最上方項目 shortcut。
- `matchHistory` 是全域清單，保留最近 `matchHistoryGlobalLimit` 筆。
- `panelPosition` 是全域位置，不分社團。

新增持久化資料時，應先補：

- `STORAGE_KEYS`
- `STORE_DEFINITIONS` 或 `PER_GROUP_STORE_DEFINITIONS`
- 對應的 normalize / load / save helper
- smoke test 覆蓋 migration 或基本讀寫形狀

## Runtime State

目前 `STATE` 已分成 runtime 區塊：

```js
const STATE = {
  config,
  scanRuntime,
  notificationRuntime,
  routeRuntime,
  uiRuntime,
  schedulerRuntime,
  sessionRuntime,
};
```

各區塊責任：

- `config`：目前生效中的正式設定。
- `scanRuntime`：最近掃描結果、最近 scan items、掃描錯誤、掃描中與載入更多狀態。
- `notificationRuntime`：最近一次通知狀態。
- `routeRuntime`：Facebook SPA route、route settle 與目前 group id。
- `uiRuntime`：panel 掛載、panel 位置、拖曳狀態。
- `schedulerRuntime`：MutationObserver、scan timer、refresh timer、route/render interval，以及本腳本操作 Facebook UI 時使用的短暫 mutation suppression window。
- `sessionRuntime`：本次 userscript session 內已初始化的 scan scope set。

## Scan Target 與 Scope

掃描目標由 `getCurrentScanTarget()` 統一建立，避免 orchestration 層直接解析 URL。

目前支援：

- `kind: "posts"`：社團貼文 feed。
- `kind: "comments"`：單篇貼文頁留言；會先掃目前已載入 DOM，並在自動載入更多啟用時做 scroll-only 多視窗收集。

主要術語：

- `groupId`：Facebook 社團識別，也是設定與 history 的主要分區。
- `parentPostId`：留言模式的父貼文 ID。
- `scopeId`：baseline / seen / dedupe 的分區。

目前設計決策：

- config 是 group-scoped：同一社團內 posts/comments 共用關鍵字、通知端點、暫停狀態與 refresh 設定。
- baseline / seen 是 target-scoped：不同單篇貼文留言頁不共用 seen baseline。
- match history 是全域最近清單，紀錄項目仍保留 group id 與 group name。

重要寫入應優先透過現有 patch helper：

- `setConfigPatch()`
- `setScanRuntimePatch()`
- `setNotificationRuntimePatch()`
- `setRouteRuntimePatch()`
- `setUiRuntimePatch()`
- `setSchedulerRuntimePatch()`
- `setSessionRuntimePatch()`

後續若新增功能會同時動到多個 runtime 區塊，先補小型 orchestration helper，不要把寫入散落在 event handler、extractor 或 notifier 內。

## 掃描流程

掃描入口是 `runScan(reason)`，目前已整理成薄 orchestration：

1. `createScanExecutionContext(reason)` 建立 page / group / rule / baseline context。
2. `collectScanExecutionData(scanContext)` 收集 scan items 並建立 include / exclude 摘要。
3. `markScopeInitializedAfterScan(scopeId, baselineMode)` 完成第一次掃描 baseline 註記。
4. `commitScanState(groupId, scopeId, summaries, matchesToNotify)` 發送通知、寫入 history、標記 seen。
5. `buildSuccessfulScanRuntimeState(...)` 建立最新 panel/debug state。
6. `applySuccessfulScanRuntimeState(...)` 套用 runtime state。
7. finally 階段重排 refresh 並重繪 panel。

第一次進入某 scan scope 時會進入 baseline mode：建立 seen baseline，不對既有項目發通知。從暫停切回開始時，目前語義是 restart current target：清掉目前 scan scope 的 seen baseline，並重新掃描。

## 貼文收集與抽取

抽取流程分成候選收集、DOM 準備、資料抽取與過濾：

- `findFeedRoot()` 找 feed root，找不到時退回 `document.body`。
- `collectPostContainers()` 依 `SELECTORS.postContainerCandidates` 收集視窗附近候選容器。
- `preparePostContainerForExtraction()` 展開折疊文字，並執行最小 permalink warmup。
- `extractPostRecord()` 統一輸出貼文資料形狀。
- `getNonPostReason()` 過濾排序控制列、留言回覆等非貼文內容。
- `collectFeedPostsAcrossWindows()` 在保守捲動下累積多個可見視窗的唯一貼文。

貼文資料形狀保留：

```js
{
  postId,
  permalink,
  author,
  text,
  normalizedText,
  timestampText,
  timestampEpoch,
  groupId,
  source,
  extractedAt
}
```

目前 `timestampText` 與 `timestampEpoch` 只保留欄位形狀，不再從 Facebook DOM 抽取時間。不要在沒有明確需求時重新加入時間解析，因為這通常會增加 selector 脆弱性。

## 留言收集與抽取

留言模式會先掃描單篇貼文頁中已載入在 DOM 裡的留言；若自動載入更多已啟用，會透過 scroll-only 的保守捲動嘗試載入更多留言。

主要流程：

- `collectCommentContainers()` 以 `comment_id` / `reply_comment_id` permalink anchor 收集候選留言容器。
- `collectSettledCommentCandidates()` 在短時間內等待留言 DOM 穩定，降低 reload 後只抓到部分留言的機率。
- `extractCommentRecord()` 輸出 `itemKind: "comment"`、`commentId`、`parentPostId` 與 canonical comment permalink。
- `collectCommentsAcrossWindows()` 是 comments 專用跨視窗 collector，負責累積留言、保守捲動、等待 DOM 穩定與回填 scan meta。

留言自動載入邊界：

- `collectCommentScrollTargets()` 收集留言附近與頁面中可能的可捲容器，避免只依賴 `document.scrollingElement`。
- 每輪正式掃描只做 scroll-only 載入；通知、seen、baseline 仍由 `runScan()` 的 commit 階段處理。
- 點擊「查看更多留言」或「查看先前留言」屬於更高互動等級，應與 scroll-only 分開設計與驗證。

## 去重與快取

scan item identity 目前優先順序：

1. comment item：`commentId` / comment permalink / parent post + composite fallback。
2. feed post item：`postId` / canonical permalink / composite fallback。
3. legacy fallback key。

`getPostKeyAliases(item)` 會為同一個 scan item 建立多組等價 key，降低不同掃描輪次抽到不同欄位時造成重複通知的機率。

目前有兩類掃描最佳化：

- `target-aware sort preparation`：若使用者設定開啟，掃描前會依目前 target 保守嘗試切到偏好排序。社團貼文模式偏好「新貼文」；單篇貼文留言模式偏好「由新到舊」。排序辨識、選單選項搜尋與點擊結果應維持在 Page Context / Scheduling 的排序 helper，不塞進 scan orchestration。
- `top-item shortcut`：社團貼文模式比對最新最上方貼文；單篇貼文留言模式比對最新最上方留言。若相同，可跳過深度掃描並沿用上一輪完整掃描快取。
- `seen-stop`：feed-post only。在「新貼文」排序且已有 seen 紀錄時，連續遇到足夠數量的已看過貼文後停止更深掃描。

這些都是保守最佳化。新增功能若會改變掃描深度、排序假設或 identity key，必須同步檢查這兩條捷徑。留言模式目前不使用 seen-stop。

## 通知架構

通知由 `notifyForScanItem(item)` 分發。

通道定義集中於 `NOTIFICATION_CHANNEL_DEFINITIONS`，目前包含：

- `gmDesktop`：本地 Tampermonkey `GM_notification`，預設啟用。
- `ntfy`：需要使用者勾選通道並設定 topic 才送出。
- `discord`：需要使用者勾選通道並設定 Webhook URL 才送出。

每個通道都有對應的 `enable...Notification` config flag，由 settings modal 寫入 `notification` config group。遠端通知端點必須維持 opt-in；新增任何會把資料送出本機的通道前，應先確認需求與文件。

通知內容由共用 formatter 建立：

- `getNotificationFields()`
- `buildCompactNotificationBody()`
- `buildRemoteNotificationBody()`

測試通知不得寫入 seen 或 match history；目前 `sendTestNotification()` 只走 notifier 與 panel render。

## UI 架構

主面板由 `createPanel()` 建立、`renderPanel()` 更新。

目前 UI 分工：

- 主面板 shell：固定 DOM 與按鈕。
- view state：`getPanelViewState()`、`getPanelStatusViewState()`、`getPanelDebugViewState()`。
- section update：`updatePanelControls()`、`updatePanelStatusSection()`、`updatePanelDebugSection()`。
- settings modal：讀草稿、套用草稿、持久化 refresh / notification 設定。
- history modal：讀全域 match history 並顯示可開啟 scan item 連結。
- help modal：definition-driven 的 include / ntfy / Discord 說明。
- panel drag：位置正規化、viewport clamp、持久化。

低優先可改善點是進一步降低 panel 重刷範圍，但目前沒有必要為形式而拆更細。

## Lifecycle

啟動流程：

1. userscript IIFE 檢查 `window.__FB_GROUP_REFRESH_RUNNING__`，避免重複初始化。
2. `start()` 呼叫 `bootstrapAppRuntime()` 與 `startMaintenanceLoops()`。
3. `bootstrapAppRuntime()` 建立 panel、安裝 observer、安排初始 scan 與 refresh。
4. `startMaintenanceLoops()` 每秒檢查 route change 與 panel 是否被 Facebook SPA 移除。
5. `handleRouteTransition()` 在 route 改變時 reload group config、重置 scan state、重裝 observer、安排掃描。

掃描與刷新排程：

- MutationObserver root 由 `findObserverRoot(scanTarget)` 選擇：feed target 優先 feed root，comment target 優先留言捲動容器或 main 區。
- MutationObserver 透過 `shouldRescanForMutation(scanTarget, mutations)` 判斷是否 debounce 安排 scan；feed target 保留較寬的新增節點訊號，comment target 則使用 comment permalink、留言文字與 direct-target attributes / characterData 訊號，並先套用 mutation suppression，避免本腳本的排序操作自觸發重掃。
- route 切換後套用 `ROUTE_SETTLE_MS`，避免抓到半穩定 DOM。
- refresh 只在監控啟用且位於支援的 group page 時安排。
- refresh 秒數可用 jitter range 或 fixed seconds。

## 測試與驗證

最小驗證指令：

```powershell
node .\scripts\smoke_check_userscript.js
```

smoke test 透過 `__FB_GROUP_REFRESH_TEST_MODE__` 載入 userscript，只暴露穩定純邏輯，不啟動真實 lifecycle。

目前覆蓋重點：

- userscript metadata / test hook
- text normalization
- config patch 與 group-scoped storage
- panel position helper
- keyword matcher
- refresh payload 與 scan limits
- permalink / postId extraction helper
- scan target、comment sort、comment permalink / author / DOM settle helper
- scan item identity aliases、dedupe、scope-scoped seen store、history merge
- top-item shortcut 與 feed-only seen-stop helper
- target-aware observer root 與 mutation rescan helper
- notification formatting
- runtime state helper

有 DOM、Facebook 實頁、Tampermonkey 權限、通知端點的行為仍需手動驗證。

## 變更邊界

後續新增功能時，先判斷它主要屬於哪個區段：

- 新設定：走 `DEFAULT_CONFIG`、config patch helper、settings modal、storage facade。
- 新關鍵字語法：改 `Matcher / Rules`，並補 smoke test。
- 新抽取欄位：改 extractor 與 scan item record shape，並同步 debug panel。
- 新 notification channel：改 notification channel registry、runner map、settings UI 與 opt-in 文件。
- 新掃描策略：改 scan engine / scheduler，並檢查 top-item shortcut 與 seen-stop。
- 新 UI 顯示：優先從 view state 與 section renderer 切入，不直接散讀 `STATE`。

避免事項：

- 不引入 bundler、框架或第三方依賴，除非需求明確且先討論。
- 不做背景 headless browser、OCR、CAPTCHA、stealth automation。
- 不自動登入、發文、留言、按讚、加入社團或私訊。
- 不把遠端通知改成預設啟用。
- 不在同一輪同時大改 selector / extractor 與 scan orchestration。
