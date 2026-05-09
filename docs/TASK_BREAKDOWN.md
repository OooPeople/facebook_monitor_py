# 任務狀態

本文件只記錄目前狀態、下一步、風險、不做事項與最近驗證。
穩定架構事實看 `docs/ARCHITECTURE.md`；新對話交接看 `docs/HANDOFF.md`；歷史計畫與 review 細節看 `docs/archive/`。

## 目前狀態

- Web UI 是正式日常入口；背景 scheduler / resident main 會隨 Web UI 啟動。
- target 卡片「開始 / 停止」是日常主操作；posts / comments target 均可透過貼上 Facebook URL 建立，系統會自動判斷 target 類型。
- group-scoped config、target-scoped seen / latest scan / runtime / history、notification outbox boundary 已落地。
- posts/comments scan 都走正式 resident main queue；Web UI scan-once 只排入 resident request，不直接啟動 browser scan。
- dashboard read model、target card、設定 modal、命中紀錄 modal、collapsed summary、SSE revision 與 target-level partial update 已完成；UI 重構 Phase 9/10 已歸檔。
- Web UI read path 已避免 partial update 每輪重新 schema init；短暫 SQLite lock 會以 503 / skip 該輪處理，不應輸出 ASGI traceback。
- Web UI shutdown 已處理 dashboard SSE 與 Playwright driver close 的已知噪音；CTRL+C 應可乾淨關閉。
- 最近掃描 preview、命中紀錄 preview 與查看紀錄排序已對齊 latest scan snapshot：同輪較新的 item 顯示在上方。
- posts debug 已補 `linkDiagnostics` / `warmupDiagnostics`，用來排查 `linkCount > 0` 但沒有 permalink 的案例；`warmupDiagnostics.samples[].anchorDetails` 會保留 raw/resolved href、文字來源、屬性名稱、位置與簡短 DOM path，供判斷 text-only post 的 group-home anchor 是否其實是 timestamp。
- posts permalink warmup 已針對 text-only post 的 obfuscated timestamp anchor 補 Python 版擴充：上方區域、短小、resolved href 是同社團 group-home，且 raw href 是 Facebook root 或同社團 group-home、文字像時間的 header link 會進入 warmup；仍必須 hover/focus 後解析出 canonical permalink 才接受。
- posts debug 已補順序定位欄位：`firstSeenRound` / `roundItemIndex` / `collectionIndex` / `domIndex` / `domPosition`，用來判斷 UI 順序是 DOM 原始順序、跨輪收集順序，還是後處理造成；單筆 latest scan 不再輸出重複的完整 `debug_json`，避免診斷文字過大。
- posts extractor 同一輪候選容器已改為依畫面垂直位置排序後再抽取，避免 Facebook selector / DOM query 順序與實際視覺貼文順序不同。
- 查看紀錄已對齊 JS `matchHistory` 語義：Web UI 重啟清理不再刪 `match_history`，同一 target/item key 重新命中會刷新紀錄，並全域裁切最近 10 筆；卡片命中分頁、sidebar hit count 與 preview 只顯示本次 Web UI session 之後的 `notified_at`，完整查看紀錄 modal 則讀持久紀錄。
- comments debug 已補 `textDiagnostics`，用來排查留言文字候選與重複片段。
- posts/comments 文字片段合併已抽成 `facebook.text_snippet_dom` shared helper；只共用 exact duplicate、子片段包含與完整片段取代碎片語義，兩者的 DOM 範圍、permalink、排序與 load-more 仍維持分離。
- comments extractor 已補 target scope guard 與 comment anchor diagnostics：留言候選會記錄原始 anchor、route post id 與 scope reason，並排除明確不屬於目前 parent post 的背景留言。
- Discord webhook sender 已補 429 rate-limit 診斷與短 `Retry-After` 等待重試；若仍失敗，UI 訊息會保留 retry-after、global 與 Discord message 摘要。

## 使用者已確認

- queue / running 顯示正常，未觀察到 stale queued / running。
- posts auto load more diagnostics 可判讀，且能穩定取得 10 篇貼文。
- 通知預設值與通知功能正常。
- `auto_adjust_sort` 功能正常。
- comments target 可用真實 Facebook 單篇貼文 URL 建立並掃描留言。

## 下一步

1. [已完成] Dashboard UI 美化：以 `docs/ui_refactor/reference_ui.html` 作為視覺與版面語義參考，但不直接覆蓋現有實作。
2. [已完成] 先整理守則：已把 reference UI 使用規則、互動契約保留、修改範圍與黃金比例要求寫入 `AGENTS.md`。
3. [已完成] 調整 dashboard shell / topbar / sidebar：維持中央內容寬度與淡冷灰頁面、白卡、輕陰影；保留 sidebar anchor、active state 與 partial update 相關 `data-*`。
4. [已完成] 調整 target card：保留 target 開始/停止、儲存、查看紀錄、設定、更多操作、collapse 與診斷；外觀靠近 reference 的 card head、chip、輕陰影。
5. [已完成] 調整展開區：左側「關鍵字與設定」與右側「最近掃描 / 命中紀錄」採接近 0.618:1 的 grid 比例，手機版改單欄。
6. [已完成] 調整 preview rows / hit records 視覺：貼文項目改成乾淨白卡、輕陰影、badge 與連結膠囊感；保留 tab state、hit count 與 modal 載入更多互動。
7. [已完成] 驗證：已執行 compile、ruff、pytest，並用臨時 preview DB 啟動 Web UI 做桌面/窄版視覺檢查、tabs、collapse 與 hit records modal 檢查。
8. [已完成] UI 字級微調：保留 sidebar `Facebook Monitor` 文字但移除左上角圓形 logo；左側「關鍵字與設定」標題列與右側 tabs 對齊，並小幅放大 target card 內設定與 preview 正文字級。
9. [已完成] 設定摘要可讀性微調：設定摘要 value 改為一般字重，並改用 inline SVG icon + label/value 資訊列；「查看全部設定」改成滿寬 outline button。
10. [已完成] 展開卡片細節修正：左側標題列高度對齊右側 tabs，左右 panel 底部高度對齊，刷新 icon 改成箭頭與圓弧不相連。
11. [已完成] 設定 modal 掃描設定壓縮高度：`auto_load_more` 與 `auto_adjust_sort` 改為同列兩欄，窄版仍回單欄。
12. [已完成] 新增 target 頁面 UI 重排：改為中央單一卡片、分區表單、刷新/通知設定與底部 action bar；畫面只保留 URL、顯示名稱、refresh mode、notification settings，max items / auto load more / auto sort 以 hidden defaults 提交。
13. [已完成] 全域設定頁 UI 重排：改為中央卡片，profile 狀態與通知預設分區排版，保留 profile open/close、通知保存、測試通知與套用到所有 target 的 form action。
14. [已完成] UI CSS cache key 更新：`style.css` 與內部 `@import` 統一升版，避免瀏覽器沿用舊版 `layout.css` / `responsive.css` 導致設定頁仍呈現 full-width 舊排版。
15. [已完成] 全域設定頁通知預設值微調：欄位順序對齊個別 target 設定 modal，並把測試通知、批次套用與保存按鈕拆成 footer 分組。
16. [已完成] UI CSS 架構整理：新增 `static/styles/pages.css` 集中新增 target / 全域設定頁專用規則，讓 `layout.css` 回到 shell、topbar、list 等通用佈局職責，`responsive.css` 僅保留跨元件響應式規則。
17. [已完成] Sidebar scroll-sync：主內容區滾動時，以 viewport 中線附近的 target card 自動同步左側欄 active item；桌面 fixed sidebar 會保守地讓 active item 保持可見，窄版不干擾頁面滾動；`main.js` 對 `sidebar.js` 的 ES module import 已加 cache key，避免瀏覽器沿用舊模組。
18. [已完成] Sidebar 點擊跳轉體驗微調：點左側 target 觸發 smooth-scroll 時，暫時鎖住 active item 在被點擊的 target，避免中途經過其他卡片時左側欄閃爍或依序選中。
19. [已完成] Sidebar 字級微調：左側 target 名稱與狀態字級略放大，比例接近中央卡片內文但仍低於卡片主標題，提升掃描清單可讀性。
20. [已完成] Sidebar 寬度微調：新增 `--sidebar-width: 336px` 統一控制固定左欄與主內容位移，並把桌面/窄版切換 breakpoint 提到 `1240px`，減少左欄內容壓縮感。
21. [已完成] 表單與 sidebar 狀態微調：個別 target 設定 modal、新增 target 頁面字級對齊主頁內文字級；sidebar target 狀態改成與卡片一致的圓弧 pill 背景，命中數 / 尚未掃描作為次要文字呈現。
22. 由使用者持續實測 posts/comments extractor、通知內容與 dashboard partial update；若再出現單筆異常，優先看整輪 scan diagnostics 與單筆 item debug。
23. 若 posts/comments extractor 繼續擴大，優先拆 `facebook/feed_dom.py` 與 `facebook/comment_dom.py` 內的大型 evaluate script，而不是把兩條不同 DOM 邏輯硬合併。
24. 若遇到 JS 已有成熟語義的功能，先依 `docs/REFERENCE_MAP.md` 對照 `reference/src/facebook_group_refresh.user.js`，再小步修正 Python 版。
25. 後續 UI 小修維持現有拆分邊界：template partial、`static/dashboard/*.js`、`static/styles/*.css`、read model / presenter。

## 目前不做

- 不做多 profile orchestration。
- 不做 EXE 打包。
- 不搬 userscript 的頁內 panel UI。
- 不實作 top-item early-skip；置頂 / 管理員貼文會讓判斷不可靠。
- 不宣稱 mutation relevance 已接上即時觸發；目前 Python resident main worker 仍是 polling。
- 不新增新的 `phase_*` script。
- 不把 posts/comments extractor、sort controls、scroll/load-more 細節硬抽成共用大函式。
- 不把 one-shot / sync resident fallback 包裝成正式產品 parity。

## 主要風險

- Facebook 可能要求重新登入、checkpoint 或其他驗證。
- headless / headed DOM 可能不一致。
- selector / extractor 可能因 Facebook DOM 變動而不穩。
- resident worker、設定視窗、capture script 不能同時持有同一 automation profile；看到 `profile_locked` 時先找仍在執行的 Playwright context。
- `feed_dom.py` 與 `comment_dom.py` 仍含大型短生命週期 evaluate script；若 DOM 邏輯繼續擴大，應拆 selectors / permalink DOM / text DOM / author DOM。

## 驗證

```powershell
.\scripts\uv.ps1 run pytest -q
.\scripts\uv.ps1 run python -m compileall -q src scripts tests
.\scripts\uv.ps1 tool run ruff check src tests
```

最近一次結果：

- `.\scripts\uv.ps1 run pytest -q`，`192 passed`
- `.\scripts\uv.ps1 run python -m compileall -q src tests`，通過
- `.\scripts\uv.ps1 tool run ruff check src tests`，通過
