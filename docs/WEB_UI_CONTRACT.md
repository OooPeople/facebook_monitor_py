# Web UI Contract

本文件只記錄 Web UI 呈現、互動一致性與 route / presenter 邊界。target
lifecycle、scheduler、scan pipeline、notification outbox、dedupe、
runtime cleanup 與資料語義仍以 `docs/ARCHITECTURE.md` 為主；使用者操作步驟看
`docs/USAGE.md`；browser-level manual QA checklist 看 `docs/tooling.md`。

## 邊界

- 本文件可以描述 layout、sidebar 呈現、modal、button、icon、partial update 與 read model payload 的 UI 契約。
- 本文件不得重述 target start / stop / reset 的完整資料語義；需要時只連回 `docs/ARCHITECTURE.md#web-ui-語義`。
- 本文件不定義 scheduler、worker、notification、dedupe、persistence migration 或 Facebook DOM helper 的 runtime 行為。
- 若 UI 規則會影響 target state、scan scheduling、notification outbox、
  dedupe、persistence 或 Facebook runtime 行為，主語義必須留在
  `docs/ARCHITECTURE.md`，本文件只能摘要或連結。
- Web UI 若需要新資料，優先新增 read model / presenter；不得為 UI 小修順手重寫 worker、notification outbox、scheduler runtime 或 Facebook DOM helper。

## 審查契約表

### 文件邊界表

| 主題 | 本文件可定義 | 必須留在 `ARCHITECTURE.md` |
|---|---|---|
| Target 控制 | 按鈕位置、label、modal 互動 | start / stop / reset 的 persistence 語義 |
| 側欄 | 視覺順序、drag/drop UI、template 確認文案 | layout 寫入 transaction 與 scheduler 獨立性 |
| 局部更新 | 前端 transport state 與 DOM replacement contract | revision 真實來源與 DB trigger 語義 |
| 通知 | 顯示區塊與遮罩 secret 表單行為 | outbox、dedupe、dispatch、retry / cleanup 語義 |
| Facebook 資料 | 畫面呈現的 diagnostics 欄位 | extractor、sort、load-more、scan pipeline 行為 |
| Facebook safety | global warning、single/batch Start 風險確認 | incident transaction 與 browser runtime |

### UI 契約表

| 介面區塊 | 穩定契約 | 不可隨手改動 |
|---|---|---|
| Target 卡片 | 身分 / 標題 / 狀態 / 結果區塊消費 presenter payload | DOM id、data attributes、JS selectors |
| 側欄 layout | 前端只呈現已保存的 layout 意圖 | scheduler scan order 或 target repository order |
| 群組 template | 破壞性批次覆蓋，且必須要求使用者確認 | config fallback owner 或隱性全域繼承 |
| 動態對話框 | confirm / input / action dialogs 走共用 helper | dashboard 流程內使用原生 `confirm/prompt/alert` |
| Dashboard revision | 一個 EventSource，加上最多一個 polling fallback | 多條並行 transport paths 同時更新同一份 state |
| Facebook temporary-block warning | full page 與 batch partial 使用同一 read model；只呈現 bounded 狀態 | raw target/profile/session/token/evidence/path |

## Target Card 與結果呈現

- target card header 顯示 target identity、target kind、最近掃描與下次刷新；左側圓形位置保留給社團縮圖。
- 社團縮圖載入失敗時，target card 先退回文字 avatar，並在同一頁面 session
  中針對同一 target/URL 只上報一次；image-only maintenance 的產品語義以
  `docs/ARCHITECTURE.md#web-ui-語義` 與
  `docs/ARCHITECTURE.md#target-與-state` 為準。
- 貼文 / 留言模式 chip 是 target kind 分類標籤，不是執行狀態 badge，也不得與 `已啟用` / `已停止` 混淆。
- 右側結果 panel header 可顯示最近一輪 scan cycle result；這是掃描結果摘要，不是錯誤或使用者停止狀態。
- 最近通知摘要不放在 target card header；通知狀態由 notification events、outbox diagnostics 與相關 read model 承接。
- 命中紀錄 UI 稱 `match_history` 時間為「記錄時間」；route / presenter payload 對外使用 `recorded_at`。

## Sidebar Layout 與 Group Template

- Sidebar layout UI 呈現與操作順序來自 sidebar read model；不得在前端把 visual order 解讀成 scheduler 掃描順序。
- 缺失 placement 顯示在未分組區；前端呈現這個 fallback 時不得自行補寫 layout state。
- 調整順序與 group placement UI 只收集使用者意圖；實際保存與資料 owner 語義以 `docs/ARCHITECTURE.md#sidebar-layout-與-group-template` 為準。
- Sidebar 排序正式保存只走 `/api/sidebar/layout` 的單一 layout command；舊分段 write routes 只作 legacy tombstone，不得接回正式前端流程。
- Group template UI 必須把套用呈現為破壞性批次覆蓋操作，要求使用者確認，並避免暗示它是 target config fallback。
- Sidebar group start / stop 控制只呈現批次套用 target start / stop；不得暗示存在 group-scoped runtime state。

## Partial Update 與資料邊界

- 前端收到 dashboard batch payload 後更新 sidebar 與 target cards；partial update 的 revision 來源以 `docs/ARCHITECTURE.md#web-ui-語義` 為準。
- Dashboard invariant warning，以及單一卡片與命中紀錄的 invariant validation，
  都只代表當次畫面已載入的 read scope，不得宣稱已驗證全庫健康；完整 audit 邊界以
  `docs/ARCHITECTURE.md#web-ui-語義` 為準。
- 前端 revision transport 預設使用 EventSource 長 SSE；無 EventSource 支援或
  SSE reconnect 逾時後才啟動 `/api/dashboard-revision` polling fallback。
- EventSource open 後必須停止 fallback polling，任一時間最多保留一個
  EventSource 與一個 polling interval。
- Temporary-block warning 另保留 deadline timer 與單一 bounded 低頻 dashboard batch
  refresh，因期限到達不一定增加 SQLite dashboard revision。它沿用既有
  `applyDashboardPartialUpdate`，同步 warning、sidebar 與 cards；不得再建立另一條
  warning-only 或 target-only polling transport。
- Temporary-block banner 是 12 小時 advisory warning，不是強制 cooldown lock，也不
  顯示 recovery CTA。每個 stopped target 與 sidebar group／批次 Start 必須使用目前
  warning generation，以共用 dialog 顯示「可能無法取得內容、也可能造成更久限制」；
  使用者確認後才送出。
  Partial update 必須同步新增／移除這組確認屬性與 generation，不能沿用過期確認；
  stale confirmation 必須重新顯示最新警告。Transaction 與 race 語義以
  `docs/ARCHITECTURE.md#scan-pipeline` 為準。
- 確認不消耗 warning；警告期間每次 single/batch Start 都重新提示。確認成功只執行
  原有 Start 語義，不改 target 設定。Stop、設定檢視與 diagnostics 不受影響。
- route / template / static module 應消費 read model 或 presenter payload；不直接承擔 scan、dedupe、outbox 或 persistence owner 語義。
- 新 UI 欄位若只是呈現既有狀態，優先擴充 read model / presenter；若需要新增持久狀態，必須先回到 `docs/ARCHITECTURE.md` 定義資料 owner 與 runtime 語義。

## 共用互動元件

- 會改狀態的 dashboard JSON fetch 走共用 CSRF helper。
- 確認與輸入類彈窗走共用 dynamic dialog module，不使用瀏覽器原生 `confirm/prompt/alert`。
- 內容型 modal 可以保留 Jinja `<dialog>`，但關閉/backdrop 行為走共用 helper。
- Modal 關閉入口遵守單一可見 dismiss pattern：read-only modal 用右上角關閉；form/action/confirm/prompt modal 用底部取消或取消按鈕，不同時顯示兩套。
- Web UI icon 使用 inline SVG，避免文字 glyph 造成跨字型對齊差異。
- Button 以共用 `button, .button` 與 modifier class 為基礎；局部 class 只保留尺寸、位置或狀態差異。
