# Web UI Contract

本文件只記錄 Web UI 呈現、互動一致性與 route / presenter 邊界。target lifecycle、scheduler、scan pipeline、notification outbox、dedupe、runtime cleanup 與資料語義仍以 `docs/ARCHITECTURE.md` 為主；使用者操作步驟看 `docs/USAGE.md`；browser-level manual QA checklist 看 `docs/tooling.md`。

## 邊界

- 本文件可以描述 layout、sidebar 呈現、modal、button、icon、partial update 與 read model payload 的 UI 契約。
- 本文件不得重述 target start / stop / reset 的完整資料語義；需要時只連回 `docs/ARCHITECTURE.md#web-ui-語義`。
- 本文件不定義 scheduler、worker、notification、dedupe、persistence migration 或 Facebook DOM helper 的 runtime 行為。
- 若 UI 規則會影響 target state、scan scheduling、notification outbox、dedupe、persistence 或 Facebook runtime 行為，主語義必須留在 `docs/ARCHITECTURE.md`，本文件只能摘要或連結。
- Web UI 若需要新資料，優先新增 read model / presenter；不得為 UI 小修順手重寫 worker、notification outbox、scheduler runtime 或 Facebook DOM helper。

## Target Card 與結果呈現

- target card header 顯示 target identity、target kind、最近掃描與下次刷新；左側圓形位置保留給社團縮圖。
- 社團縮圖載入失敗時，target card 先退回文字 avatar，並在同一頁面 session 中針對同一 target/URL 只上報一次；image-only maintenance 的產品語義以 `docs/ARCHITECTURE.md#web-ui-語義` 與 `docs/ARCHITECTURE.md#target-與-state` 為準。
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
- 前端 revision transport 預設使用 EventSource 長 SSE；無 EventSource 支援或 SSE reconnect 逾時後才啟動 `/api/dashboard-revision` polling fallback。EventSource open 後必須停止 fallback polling，任一時間最多保留一個 EventSource 與一個 polling interval。
- route / template / static module 應消費 read model 或 presenter payload；不直接承擔 scan、dedupe、outbox 或 persistence owner 語義。
- 新 UI 欄位若只是呈現既有狀態，優先擴充 read model / presenter；若需要新增持久狀態，必須先回到 `docs/ARCHITECTURE.md` 定義資料 owner 與 runtime 語義。

## 共用互動元件

- 會改狀態的 dashboard JSON fetch 走共用 CSRF helper。
- 確認與輸入類彈窗走共用 dynamic dialog module，不使用瀏覽器原生 `confirm/prompt/alert`。
- 內容型 modal 可以保留 Jinja `<dialog>`，但關閉/backdrop 行為走共用 helper。
- Modal 關閉入口遵守單一可見 dismiss pattern：read-only modal 用右上角關閉；form/action/confirm/prompt modal 用底部取消或取消按鈕，不同時顯示兩套。
- Web UI icon 使用 inline SVG，避免文字 glyph 造成跨字型對齊差異。
- Button 以共用 `button, .button` 與 modifier class 為基礎；局部 class 只保留尺寸、位置或狀態差異。
