# UI 重構 Phase 9/10 完成紀錄

本文件保存 Phase 8.5 後 Phase 9/10 的實作要求與完成結果。Phase 9/10 已完成並歸檔；目前狀態、下一步與驗證結果以 `docs/TASK_BREAKDOWN.md` 為準。

## Phase 9 前已收斂

- Dashboard read model 已從 all-target N+1 改為批次讀取 targets / configs / runtime / latest scan / scan runs / notifications / match history counts。
- 已新增 target-level read 邊界：
  - `GET /api/sidebar`
  - `GET /api/targets/{target_id}/card`
- Dashboard client state 已集中到 `static/dashboard/state.js`，管理 dirty form、scroll restore、refresh suppression、preview tab、未來 collapsed target state。
- 已新增 `TargetCardSummary`，Phase 9 收合摘要不得把新欄位繼續塞回 `TargetRow`。
- 查看紀錄 modal 已改成分批載入，預設每次 50 筆，使用「載入更多」延伸，不再一次抓 200 筆。
- Phase 9 前文案漂移已先收斂：`Targets` 改為 `監看清單`，`Include / Exclude keywords` 改為 `包含 / 排除關鍵字`，`保存設定` 改為 `儲存設定`，runtime status 不再顯示 raw `idle/running/queued/error`。

## Phase 9A：Target Card 收合 / 展開

狀態：已完成。

要求：

1. 預設全部展開。
2. 每張卡片可獨立收合 / 展開，不做 accordion。
3. 收合狀態只存在 `localStorage`，不寫 DB。
4. 若該 target 有尚未儲存欄位，不可靜默收合。
5. 收合後仍保留 header、狀態、命中數、刷新摘要、通知摘要與操作列。
6. 不修改 worker、scheduler queue、notification outbox、scan pipeline。
7. 新 JS 放在 `static/dashboard/*`，樣式放在 `static/styles/*`。

實作結果：

- `_target_card.html` 已加入 `data-target-card`、`target-collapsed-summary`、`target-collapsible` 與 `data-collapse-toggle`。
- `static/dashboard/card_collapse.js` 負責 localStorage 狀態、aria-expanded、按鈕文字與 dirty guard。
- `static/styles/target-collapse.css` 負責 collapsed summary 與收合狀態樣式。
- settings modal 與 hit records modal 保留在 collapsible 區外，收合狀態仍可開啟。
- 有未儲存變更時阻止收合並顯示 inline feedback。
- 未做 SSE、partial update 或 responsive polish。

## Phase 9B：Polish / Responsive / 空狀態

狀態：已完成。

要求：

1. 檢查不同 viewport 下 sidebar、target card、modal 文字與按鈕不重疊。
2. 保持最近掃描 / 命中紀錄 preview row 共用 `_preview_rows.html`。
3. 空狀態使用使用者語言，不顯示工程 enum 或 raw id。
4. 若有新的產品文案，統一放在 template / presenter，不要散落到 JS 內。

實作結果：

- 卡片 header 按鈕改為可換行，避免中窄版擠壓。
- 卡片操作列文案統一為「儲存」，設定 modal submit 保留「儲存設定」。
- 首頁空狀態改為引導使用「新增」，不再提 console。
- 最近掃描 / 命中紀錄 preview 空狀態補說明文字，完整命中紀錄 modal 空狀態補管理語義說明。
- collapsed summary、preview row link、modal header/footer 已補窄版換行與滾動樣式。
- 未改資料模型、worker、scheduler、notification 或 SSE。

## Phase 10 前置原則

SSE / EventSource 可以做，但不得只把 3 秒 polling 換成 SSE 後仍無條件整頁 reload。

Phase 10 應分成：

1. Phase 10A：SSE revision event + `/api/dashboard-revision` polling fallback。
2. Phase 10B：target-level partial update，優先使用 `/api/sidebar` 與 `/api/targets/{target_id}/card`。

若使用者正在編輯 target 表單，partial update 必須延後或只更新非表單區，不可覆蓋未儲存輸入。

## Phase 10A：SSE revision event + polling fallback

狀態：已完成。

要求：

1. 新增 dashboard revision SSE endpoint。
2. 前端優先使用 EventSource 接收 revision event。
3. EventSource 不支援、未成功 open 或 error 時，回到既有 `/api/dashboard-revision` polling fallback。
4. SSE 與 polling 正常情況下不可同時無限制觸發同一更新。
5. revision 相同時不處理。
6. 使用者正在編輯表單時不得強制刷新。
7. 本階段若仍使用整頁 reload，必須標明只是 Phase 10A bridge，不是 Phase 10 完成態。

實作結果：

- 新增 `GET /api/dashboard-events`，使用 `text/event-stream` 輸出 `dashboard_revision` event，payload 包含 `revision` 與 `last_changed_at`。
- SSE stream 會送 keepalive comment，避免 idle connection 過早中斷。
- `static/dashboard/revision_client.js` 先啟用 EventSource；若瀏覽器不支援、open timeout 或 error，才啟用 polling fallback。
- Phase 10A 當時只建立 revision event transport；整頁 reload bridge 已在 Phase 10B 被正常路徑 partial update 取代。

## Phase 10B：Target-level partial update

狀態：已完成。

目標：

1. revision 變更後不再無條件 `window.location.reload()`。
2. 透過 `/api/sidebar` 更新 sidebar row。
3. 透過 `/api/targets/{target_id}/card` 更新 status、header summary、counts、最近掃描 preview、命中紀錄 preview 與 collapsed summary。
4. dirty target 或正在編輯中的 target 不覆蓋表單欄位。
5. active preview tab、collapsed state、hit records modal scroll 不應被背景更新打斷。

實作結果：

- 新增 `static/dashboard/partial_updates.js`，revision 變更後 fetch `/api/sidebar` 與 `/api/targets/{target_id}/card`，更新 sidebar status、target status badge、header summary、hit count、collapsed summary、最近掃描 preview 與命中紀錄 preview。
- 新增 `static/dashboard/render_preview_rows.js`，集中以 JS 重建與 `_preview_rows.html` 對齊的 preview row DOM，避免 renderer 散落到 revision client。
- `/api/targets/{target_id}/card` 的 preview row JSON 已包含 `debug_summary`、`debug_text` 與 `has_debug`，供最近掃描 partial update 保留「除錯」區塊。
- `revision_client.js` 改為 revision 變更後先跑 partial update；只有 target list changed、target card fetch failed 或 partial update exception 時才 fallback 到安全整頁 reload。
- dirty target 只更新 header/sidebar/count/collapsed summary 等非表單區，preview rows 延後並顯示 inline feedback，避免覆蓋使用者正在編輯的輸入欄位。
- active preview tab 與 collapsed state 仍由既有 `state.js` / DOM 狀態維持，partial update 不重建整張 card。
- 本階段未啟動瀏覽器實測；驗證包含 dashboard route test、JS syntax check、compile、全量 pytest 與 ruff。
