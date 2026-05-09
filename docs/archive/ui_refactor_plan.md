# Facebook Monitor UI 重構計畫 — 正式實作版

## 0. 文件定位

這是一份「可直接交給 Codex 實作」的 UI 重構規格。它已整併先前討論的 UI 版面、左側快速跳轉選單、右側預覽 tabs、查看紀錄、設定 modal、固定 / 浮動刷新，以及四張最終參考圖。

本文件不再使用 `v3`、`v3 修正版`、`v3.1` 這類命名。之後若要更新，直接更新本檔：

```text
docs/ui_refactor/ui_refactor_plan.md
```

本文件的核心判斷是：

```text
查看紀錄與固定 / 浮動刷新不是單純 UI 排版問題，必須先補齊資料能力與服務邊界，再做畫面重排。
```

因此，本計畫把這兩個尚未完整實作的功能列為 UI 重構的前置工作，而不是放到最後的視覺調整階段。

---

## 1. 專案內文件與圖片位置

建議建立以下結構：

```text
docs/
└── ui_refactor/
    ├── ui_refactor_plan.md
    └── reference_images/
        ├── 01_dashboard_recent_scan.png
        ├── 02_dashboard_hit_records_tab.png
        ├── 03_target_settings_modal.png
        └── 04_full_hit_records_modal.png
```

### 1.1 為什麼使用 `docs/ui_refactor/`

1. `docs/` 是專案文件慣例。
2. `ui_refactor/` 明確表示這些文件只服務 UI 重構。
3. 不使用 `docs/images/` 或 `docs/picture/`，避免未來 README 圖、架構圖、操作說明圖混在一起。
4. 圖片路徑固定後，Codex 可以在實作時被明確要求參考指定圖片。

### 1.2 參考圖片用途

| 圖片 | 路徑 | 用途 |
|---|---|---|
| 主頁：最近掃描 | `docs/ui_refactor/reference_images/01_dashboard_recent_scan.png` | 主頁 layout、左側 sidebar、target card、最近掃描 tab、preview row 格式。 |
| 主頁：命中紀錄 tab | `docs/ui_refactor/reference_images/02_dashboard_hit_records_tab.png` | 命中紀錄 tab。必須與最近掃描 tab 使用同一套 row 骨架。 |
| 設定 modal | `docs/ui_refactor/reference_images/03_target_settings_modal.png` | 掃描設定、固定 / 浮動刷新、通知設定、測試通知、儲存設定。 |
| 完整命中紀錄 modal | `docs/ui_refactor/reference_images/04_full_hit_records_modal.png` | 點擊「查看紀錄」後的詳細紀錄管理畫面與清空紀錄位置。 |

---

## 2. 圖片與文字規格衝突時的裁定

圖片是視覺參考，不是逐像素實作規格。若圖片與文字規格衝突，以本文件文字規格為準。

### 2.1 全域降噪規則

最終主頁不要顯示：

1. 左上角 Facebook 圖案。
2. 頂部 `背景掃描服務：執行中` pill。
3. 左下角常駐 `小提醒` 卡片。

若設定 modal 或完整紀錄 modal 圖片背景仍出現這些舊元素，實作時不要照抄。這些圖片只參考 modal 本體的排版與資訊層級。

### 2.2 右側 preview tab 不可被完整紀錄 modal 污染

完整命中紀錄 modal 可以顯示詳細欄位，例如：

1. 編號。
2. 類型：貼文 / 留言。
3. 作者。
4. 關鍵字。
5. 通知時間 / 命中時間。
6. 內容。
7. 原文連結。

但主卡片右側的「命中紀錄」tab 不得使用上述詳細格式。

最終規則：

```text
主卡片右側 tabs = 預覽格式。
查看紀錄 modal = 詳細管理格式。
```

### 2.3 最近掃描 tab 與命中紀錄 tab 必須使用同一套 preview row

這是本 UI 重構最重要的實作紀律。

兩個 tab 的 row 格式必須一致：

```text
作者名稱    [命中：5/23 或 未命中]
內容摘要使用整個可用寬度
                                           開啟原文
```

命中紀錄 tab 不得新增：

1. `貼文 / 留言` 類型欄位。
2. 左側獨立時間欄。
3. 編號欄。
4. 第二層 toolbar。
5. `命中紀錄(23) / 查看全部 / 清空` 子標題列。
6. 表格化排版。

要讓使用者感覺是在同一個結果區內切換資料來源，而不是切到另一個功能頁。

---

## 3. 最終 UI 方向

最終 UI 架構是：

```text
Top Bar
+ 左側快速跳轉 sidebar
+ 右側多 target cards
+ 卡片內關鍵字與設定摘要
+ 卡片內最近掃描 / 命中紀錄 preview tabs
+ 卡片操作列中的查看紀錄
+ 設定 modal
+ 完整命中紀錄 modal
+ 掃描診斷折疊區
```

這個方向保留：

1. JS 版主畫面簡潔、低干擾的精神。
2. Python Web UI 橫式卡片可同時看設定與結果的優點。
3. 左側社團列表式快速定位能力。
4. 命中紀錄作為核心成果的可見性。
5. 低頻設定集中到 modal 的資訊架構。

---

## 4. 功能前置判斷

這一節是為了避免 Codex 只做出 UI 外觀，卻沒有真正補齊功能。

### 4.1 查看紀錄不是單純按鈕

`查看紀錄` 不是只在卡片上新增一個按鈕。它需要完整資料能力：

1. 查詢某個 target 的命中紀錄 preview，供右側 `命中紀錄` tab 使用。
2. 查詢某個 target 的完整命中紀錄，供 modal 使用。
3. 查詢某個 target 的命中紀錄總數，供 tab label 與 sidebar hit count 使用。
4. 清空某個 target 的命中紀錄。
5. 清空後更新 sidebar count、tab count、modal list。
6. 清空不得影響 target 設定、latest scan snapshot、scan history、seen items、notification outbox。

### 4.2 固定 / 浮動刷新不是單純設定畫面

固定 / 浮動刷新不是只在設定 modal 中新增 radio button。

它需要確保：

1. Web UI form 能保存 refresh mode。
2. fixed mode 時保存固定秒數。
3. floating mode 時保存 min / max 秒數，並且不要把固定秒數誤寫回 config。
4. scheduler / planner 實際依照 target config 計算下一次掃描間隔。
5. 主卡片設定摘要能正確顯示：`固定 X 秒` 或 `浮動 X–Y 秒`。
6. start / restart / app startup 流程不得把 floating target 意外補回 fixed refresh。

若目前已有 refresh policy 或 jitter helper，應接上 Web UI 與 target config，而不是重新寫 scheduler 主線。

### 4.3 允許的小幅服務層修改

為了補齊上述兩個功能，可以修改：

1. target config form parser。
2. application request model。
3. target config update service。
4. query service / presenter / view model。
5. match history repository 的 read / count / clear method。
6. refresh interval resolver 或 target config 讀取邏輯。

但不要修改：

1. resident main executor。
2. scan pipeline。
3. scan_finalize 核心流程。
4. notification outbox 狀態機。
5. scheduler queue / concurrency model。
6. posts/comments extractor。

---

## 5. 全域頁面架構

主頁分成三大區：

```text
┌───────────────────────────────────────────────┐
│ Top Bar                                       │
├───────────────┬───────────────────────────────┤
│ Left Sidebar  │ Target Cards List              │
│ 快速跳轉       │ 多張 cards，右側不變單卡模式       │
└───────────────┴───────────────────────────────┘
```

### 5.1 Top Bar

參考：

```text
docs/ui_refactor/reference_images/01_dashboard_recent_scan.png
```

Top Bar 顯示：

1. 左側：`Facebook Monitor`，或未來替換為中文產品名。
2. 右側：`設定`。
3. 右側：`新增`。

Top Bar 不顯示：

1. Facebook 圖案。
2. `背景掃描服務：執行中` pill。
3. running / queued / slots / browser alive 等工程資訊。

全域背景服務狀態若仍需要保留，放到全域設定、全域診斷、tooltip 或後續診斷頁，不放在主畫面最高視覺權重的位置。

---

## 6. 左側 Target Navigation Sidebar

參考：

```text
docs/ui_refactor/reference_images/01_dashboard_recent_scan.png
docs/ui_refactor/reference_images/02_dashboard_hit_records_tab.png
```

### 6.1 定位

左側選單只做：

```text
快速跳轉到對應 target card。
```

它不是：

1. target detail page。
2. 第二套設定面板。
3. 開始 / 停止 / 刪除操作區。
4. master-detail 的 master list。

### 6.2 點擊行為

點擊 sidebar target 後：

1. 右側頁面 scroll 到對應 target card。
2. 對應 target card 可短暫 highlight。
3. sidebar 該 target row 顯示 active 狀態。
4. 右側仍然保留所有 target cards。
5. 不要把右側改成只顯示單一 target。

建議實作方式：

```text
anchor id + scrollIntoView({ behavior: "smooth", block: "start" })
```

例如：

```html
<a href="#target-<target_id>">...</a>
<section id="target-<target_id>">...</section>
```

### 6.3 Sidebar row 顯示內容

每個 row 顯示：

1. 社團縮圖或 placeholder。
2. 社團名稱，最多兩行。
3. 狀態摘要。

狀態摘要範例：

```text
執行中 · 今日命中 3 筆
已停止 · 尚未掃描
錯誤 · 需重新登入
```

### 6.4 Sidebar 不顯示內容

Sidebar 不應顯示：

1. 包含關鍵字。
2. 排除關鍵字。
3. 完整通知設定。
4. ntfy topic。
5. Discord webhook。
6. 開始 / 停止。
7. 儲存。
8. 查看紀錄。
9. 設定。
10. 刪除。

---

## 7. Target Card 結構

參考：

```text
docs/ui_refactor/reference_images/01_dashboard_recent_scan.png
docs/ui_refactor/reference_images/02_dashboard_hit_records_tab.png
```

每張 target card 分成：

1. 卡片頭部：識別、狀態、常用操作。
2. 卡片主體：左側關鍵字與設定摘要，右側結果預覽 tabs。
3. 卡片底部：掃描診斷折疊區。

### 7.1 卡片頭部

左側：

1. 社團縮圖。
2. 社團名稱。
3. 狀態 badge。
4. 次要資訊列。

範例：

```text
兄弟猛象金冠軍票券與棒球商品交流平台    執行中
社團貼文 · 最近掃描 04:07 · 最近通知 Discord sent 04:07
```

右側操作按鈕固定順序：

```text
[停止 / 開始] [儲存] [查看紀錄] [設定] [⋯]
```

### 7.2 查看紀錄按鈕定位

`查看紀錄` 是 target 的核心成果入口，不應藏在 `⋯` 或 tab toolbar 裡。

定位：

```text
查看紀錄 = target 主要操作之一。
```

### 7.3 刪除 target

刪除是低頻且破壞性操作，放在 `⋯` 更多選單中，並必須有確認提示。

---

## 8. Target Card 左側區域

參考：

```text
docs/ui_refactor/reference_images/01_dashboard_recent_scan.png
```

左側只保留高頻、常看的資訊。

### 8.1 關鍵字設定

保留在卡片主畫面：

1. 包含關鍵字。
2. 排除關鍵字。

欄位形式：

```text
包含關鍵字
[ 5/23 ]

排除關鍵字
[ 徵、收 ]
```

### 8.2 設定摘要

主卡片只顯示設定摘要，不直接攤開低頻設定表單。

摘要內容：

```text
刷新：浮動 25–35 秒
目標掃描：10 筆
載入更多：開啟
排序：最相關（啟動後轉最新）
通知：ntfy / Discord
```

摘要下方保留：

```text
查看全部設定
```

此按鈕和卡片頭部的 `設定` 打開同一個 target 設定 modal。

### 8.3 移到設定 modal 的低頻設定

以下移到設定 modal：

1. 桌面通知 checkbox。
2. ntfy checkbox。
3. ntfy topic input。
4. Discord webhook checkbox。
5. Discord webhook URL input。
6. 測試通知。
7. 自動載入更多。
8. 開始後自動調整最新排序。
9. 目標掃描項目數。
10. 固定 / 浮動刷新詳細欄位。

---

## 9. 右側結果預覽區

參考：

```text
docs/ui_refactor/reference_images/01_dashboard_recent_scan.png
docs/ui_refactor/reference_images/02_dashboard_hit_records_tab.png
```

分頁只有兩個：

```text
[最近掃描] [命中紀錄 23]
```

### 9.1 分頁區不得有第二層 toolbar

不要在分頁下方或右方再加：

```text
命中紀錄（23）   查看全部   清空
```

最終決策：

```text
右側分頁只負責預覽切換。
查看完整紀錄由卡片頭部「查看紀錄」按鈕負責。
清空紀錄只放在完整紀錄 modal 內。
```

### 9.2 共用 preview row partial / component

最近掃描與命中紀錄必須共用同一個 preview row partial / component，不得各自複製一份 HTML 結構。

建議命名方向：

```text
_preview_row.html
preview_row component
TargetPreviewRow
```

實際檔名可依目前 template 架構調整，但必須維持單一來源。

---

## 10. 最近掃描 Tab

### 10.1 職責

最近掃描 tab 回答：

```text
這個 target 最近一次掃描抓到了什麼？
```

### 10.2 資料來源

只顯示最近一次 scan snapshot。

它不是歷史資料列表。

### 10.3 Row 格式

```text
作者名稱    [命中：5/23 或 未命中]
內容摘要使用整個可用寬度
                                           開啟原文
```

具體規則：

1. 第一行：作者 + badge。
2. 第二行起：內容摘要。
3. 右側：`開啟原文` link。
4. 不使用表格。
5. 不增加獨立時間欄。
6. 不增加貼文 / 留言類型欄。

---

## 11. 命中紀錄 Tab

### 11.1 職責

命中紀錄 tab 回答：

```text
這個 target 最近保存了哪些命中項目？
```

它是歷史命中紀錄的快速預覽，不是完整管理頁。

### 11.2 與最近掃描 Tab 的關係

| 分頁 | 資料來源 | 顯示目的 |
|---|---|---|
| 最近掃描 | 最近一次 scan snapshot | 看這一輪抓到什麼 |
| 命中紀錄 | 已保存的歷史命中 preview | 看最近有哪些值得回看的命中 |

兩個 tab 使用同一套 row component。

### 11.3 Row 格式

```text
作者名稱    [命中：5/23]
內容摘要使用整個可用寬度
                                           開啟原文
```

不得顯示：

1. `貼文 / 留言` 類型 badge。
2. 獨立時間欄。
3. 編號。
4. 通知時間欄。
5. `查看全部`。
6. `清空`。
7. 表格格式。

### 11.4 顯示筆數

卡片內顯示 preview 筆數即可，建議 5 筆左右。

若有更多紀錄，由卡片右上角 `查看紀錄` 進入完整 modal。

---

## 12. 查看紀錄 Modal

參考：

```text
docs/ui_refactor/reference_images/04_full_hit_records_modal.png
```

### 12.1 入口

從 target card 右上角按鈕開啟：

```text
查看紀錄
```

### 12.2 職責

完整命中紀錄 modal 回答：

```text
這個 target 到目前為止保存過哪些命中紀錄？
我要如何詳細查看、開原文或清空？
```

### 12.3 Modal 詳細欄位

完整 modal 可以顯示：

1. 編號。
2. 類型：貼文 / 留言。
3. 作者。
4. 關鍵字。
5. 通知時間 / 命中時間。
6. 內容。
7. 原文連結。

### 12.4 清空紀錄

`清空紀錄` 只放在完整 modal 右上角。

不要放在：

1. 卡片右側 tab。
2. 分頁列右側。
3. sidebar。
4. 更多選單。

清空前確認：

```text
確定要清空此 target 的所有命中紀錄嗎？
此操作不會刪除 target，也不會清除最近掃描結果或設定。
```

### 12.5 清空資料邊界

清空只刪除該 target 的 `match_history` 或等價命中紀錄資料。

不得清除：

1. target config。
2. latest scan snapshot。
3. scan_runs。
4. seen_items。
5. notification_events。
6. notification_outbox。

---

## 13. 設定 Modal

參考：

```text
docs/ui_refactor/reference_images/03_target_settings_modal.png
```

### 13.1 入口

兩個入口打開同一個 modal：

1. 卡片右上角 `設定`。
2. 左側設定摘要下方 `查看全部設定`。

### 13.2 區塊

設定 modal 包含：

1. 掃描設定。
2. 刷新設定。
3. 通知設定。

### 13.3 掃描設定

包含：

1. 自動載入更多項目。
2. 開始後自動調整成最新排序。
3. 目標掃描項目數。

### 13.4 刷新設定

包含固定 / 浮動刷新。

UI 文案：

```text
刷新模式
[固定刷新] 每固定秒數執行一次掃描
[浮動刷新] 在最小與最大秒數之間隨機刷新
```

固定刷新欄位：

```text
刷新秒數：60
```

浮動刷新欄位：

```text
最小刷新秒數：25
最大刷新秒數：35
```

### 13.5 保存語義

固定模式：

```text
fixed_refresh_sec = 使用者輸入秒數
jitter_enabled = false
min_refresh_sec / max_refresh_sec 可保留既有值，但不作為當前模式依據
```

浮動模式：

```text
fixed_refresh_sec = None
jitter_enabled = true
min_refresh_sec = 使用者輸入最小值
max_refresh_sec = 使用者輸入最大值
```

重點：浮動模式不得因為 Web form default 或 startup default 被重新寫回固定秒數。

### 13.6 驗證規則

1. 秒數必須為正整數。
2. 浮動刷新最小秒數必須小於或等於最大秒數。
3. 建議 UI 層阻止過短秒數，例如低於 10 或 15 秒。
4. 切換固定 / 浮動模式時，只顯示該模式相關欄位。
5. 儲存時顯示錯誤訊息，不要靜默失敗。

### 13.7 通知設定

包含：

1. 桌面通知。
2. ntfy。
3. ntfy topic。
4. Discord webhook。
5. Discord webhook URL。
6. 測試通知。

`測試通知` 留在設定 modal，不放主卡片。

---

## 14. 掃描診斷折疊區

每張 target card 底部保留：

```text
▸ 掃描診斷
```

預設收起。

展開後可顯示：

1. 下次刷新時間。
2. 最近錯誤。
3. 停止原因。
4. target id / group id / scope id。
5. runtime counters。
6. browser / tab 狀態。
7. scan diagnostics。

診斷資訊是進階資訊，不應預設顯示。

---

## 15. 資料與 ViewModel 需求

### 15.1 Sidebar ViewModel

每個 sidebar item 需要：

1. target id。
2. display name。
3. thumbnail URL 或 placeholder。
4. running status。
5. today hit count。
6. latest error summary。
7. anchor target id。
8. active state。

### 15.2 Target Card ViewModel

每張 target card 需要：

1. target id。
2. target kind：posts / comments。
3. group name。
4. thumbnail。
5. enabled / paused / running / idle / error 狀態。
6. 最近掃描時間。
7. 最近通知摘要。
8. include keywords。
9. exclude keywords。
10. refresh mode。
11. fixed interval seconds。
12. min interval seconds。
13. max interval seconds。
14. max scan items。
15. load more enabled。
16. auto sort enabled。
17. notification summary。
18. latest scan preview items。
19. hit record preview items。
20. hit record total count。
21. diagnostics summary。

### 15.3 Preview Row ViewModel

最近掃描與命中紀錄 tab 共用 preview row 結構。

建議欄位：

```text
author_name
badge_text
badge_kind
content_preview
permalink
secondary_text optional
```

`badge_kind` 可支援：

1. hit。
2. not_hit。
3. warning。

UI 顯示結構保持一致。

### 15.4 Full Hit Record ViewModel

完整紀錄 modal 可使用不同 ViewModel。

建議欄位：

```text
record_id
sequence_number
target_id
item_type
author_name
matched_keyword
matched_at
notified_at
notification_summary
content
permalink
```

---

## 16. API / Route 需求

命名只作為方向，實際可配合目前 FastAPI route 結構。

### 16.1 Dashboard

需要提供：

1. sidebar target summary。
2. target card list。
3. latest scan preview。
4. hit record preview。
5. hit record total count。
6. refresh mode summary。

### 16.2 Hit Records

需要提供：

1. 查詢某 target 的 preview hit records。
2. 查詢某 target 的完整 hit records。
3. 查詢某 target 的 hit records count。
4. 清空某 target 的 hit records。

清空 endpoint 必須只清該 target 的命中紀錄，不影響：

1. target 設定。
2. 最近掃描 snapshot。
3. scan history。
4. seen items。
5. notification outbox。

### 16.3 Settings

需要支援更新：

1. include keywords。
2. exclude keywords。
3. refresh mode。
4. fixed interval seconds。
5. min / max interval seconds。
6. max scan items。
7. load more。
8. auto sort。
9. notification settings。

---

## 17. 實作邊界

UI 重構期間可以修改：

1. `webapp/routes`。
2. `templates`。
3. `static`。
4. `query_service`。
5. presenter / view model。
6. form schema / request schema。
7. 設定 update endpoint。
8. 命中紀錄查詢 / 清空 endpoint。
9. match history read repository。
10. refresh mode form parsing。
11. target config update request。
12. sidebar navigation JS。
13. CSS / layout。

避免修改：

1. resident main executor。
2. notification outbox 狀態機。
3. scan finalize core logic。
4. posts/comments extractor。
5. scheduler request queue core。
6. scheduler concurrency model。

若為了 hit records 或 refresh mode 需要 schema 或 service 小幅調整，必須保持修改範圍集中，並在 PR 說明中清楚列出。

---

## 18. 正確實作順序

這是本文件最重要的工程順序。不要先做視覺美化，也不要先把卡片排版重寫完才回頭補功能。

### Phase 0：文件與參考圖落位

目標：先讓 Codex 能穩定引用文件與圖片。

工作：

1. 建立 `docs/ui_refactor/`。
2. 放入 `ui_refactor_plan.md`。
3. 放入四張 reference images。
4. 確認文件內路徑與實際檔案一致。

---

### Phase 1：前置功能 A — 查看紀錄資料能力

目標：先讓「查看紀錄」不是空按鈕，也不是假 UI。

工作：

1. 為 match history 補齊 target-scoped query：preview list、full list、count。
2. 補 clear-by-target 能力。
3. 確認 clear-by-target 只清命中紀錄，不清 latest scan / seen / notification outbox。
4. 建立 hit record preview ViewModel。
5. 建立 full hit record ViewModel。
6. 建立需要的 route / endpoint。
7. 清空後能刷新 sidebar count、tab count、modal list。

參考：

```text
docs/ui_refactor/reference_images/02_dashboard_hit_records_tab.png
docs/ui_refactor/reference_images/04_full_hit_records_modal.png
```

完成條件：

```text
即使主畫面尚未重排，也能從服務層查到某 target 的命中紀錄 preview / full list / count，並能安全清空該 target 的命中紀錄。
```

---

### Phase 2：前置功能 B — 固定 / 浮動刷新真正接入

目標：讓固定 / 浮動刷新在資料、form、設定保存與 scheduler 到期判斷上都成立。

工作：

1. Form model 新增 refresh mode。
2. Update request / upsert request 支援 fixed / floating 語義。
3. fixed mode 保存 fixed_refresh_sec。
4. floating mode 保存 min_refresh_sec / max_refresh_sec，並確保 fixed_refresh_sec 不會覆蓋 floating。
5. 設定摘要能顯示 `固定 X 秒` 或 `浮動 X–Y 秒`。
6. scheduler / planner 讀取 config 時能正確使用 fixed 或 floating。
7. start / restart / startup 流程不得把 floating mode 轉回 fixed mode。
8. 新增必要的 form validation。

參考：

```text
docs/ui_refactor/reference_images/03_target_settings_modal.png
```

完成條件：

```text
即使設定 modal 尚未完成，後端與 form 語義已能保存並套用固定 / 浮動刷新模式。
```

---

### Phase 3：Dashboard ViewModel / Presenter 整理

目標：template 不直接吃 raw DB row。

工作：

1. 建立 sidebar item ViewModel。
2. 建立 target card ViewModel。
3. 建立 shared preview row ViewModel。
4. 建立 settings summary ViewModel。
5. 將 latest scan preview 與 hit record preview 都轉成同一個 preview row 結構。
6. 將 hit record total count 帶入 tab label 與 sidebar。
7. 將 refresh mode summary 帶入卡片左側設定摘要。

---

### Phase 4：共用 preview row partial / component 與 CSS 基礎

目標：先鎖住最近掃描與命中紀錄的共同 row 骨架，避免後面做成兩套介面。

工作：

1. 建立 shared preview row partial / component。
2. 支援 hit / not_hit badge 樣式。
3. 支援 content preview 使用整個可用寬度。
4. 支援右側 `開啟原文` link。
5. 不支援 row 內表格欄位。
6. 最近掃描與命中紀錄都必須使用這個 partial。

參考：

```text
docs/ui_refactor/reference_images/01_dashboard_recent_scan.png
docs/ui_refactor/reference_images/02_dashboard_hit_records_tab.png
```

---

### Phase 5：Layout Shell + 左側快速跳轉 Sidebar

目標：完成 top bar + sidebar + main cards list 的骨架。

工作：

1. 建立左側 sidebar。
2. 加入搜尋框。
3. 顯示 target summary rows。
4. 實作點擊後 scroll 到對應 card。
5. active target row 高亮。
6. target card 被跳轉時可短暫 highlight。
7. 移除左下小提醒卡片。
8. 移除頂部背景掃描服務 pill。
9. 移除左上 Facebook 圖示。

---

### Phase 6：Target Card 重排 + 右側 Tabs

目標：完成主卡片資訊架構。

工作：

1. 卡片頭部重排。
2. 加入 `查看紀錄` 按鈕。
3. `刪除` 移入更多選單。
4. 左側只保留關鍵字與設定摘要。
5. 右側 tabs：最近掃描 / 命中紀錄。
6. 最近掃描使用 shared preview row。
7. 命中紀錄使用同一個 shared preview row。
8. 不在 tab 區放清空或查看全部。
9. 掃描診斷預設收起。

---

### Phase 7：設定 Modal

目標：把低頻設定集中到設定視窗。

工作：

1. 掃描設定區。
2. 刷新設定區。
3. 固定 / 浮動刷新切換。
4. 通知設定區。
5. 測試通知。
6. 儲存 / 取消。
7. 表單驗證。
8. 儲存後更新卡片摘要。

參考：

```text
docs/ui_refactor/reference_images/03_target_settings_modal.png
```

---

### Phase 8：完整命中紀錄 Modal

目標：讓 `查看紀錄` 成為完整紀錄管理入口。

工作：

1. 點擊卡片 `查看紀錄` 開啟 modal。
2. 顯示 target 名稱。
3. 顯示命中紀錄總數。
4. 顯示詳細紀錄列表。
5. 顯示 `清空紀錄`。
6. 清空前確認。
7. 清空後刷新 tab count 與 sidebar hit count。
8. modal 關閉後不影響主卡片狀態。

參考：

```text
docs/ui_refactor/reference_images/04_full_hit_records_modal.png
```

---

### Phase 8.5：UI 層架構整理

目標：在 Phase 9 / Phase 10 前先降低 UI 層胖檔風險，避免 target card 收合與 SSE 繼續堆進同一個 template、CSS 與 ViewModel 檔。

工作：

1. 將 `index.html` 拆成 target sidebar、target card、settings modal、hit records modal、refresh fields 等 template partial。
2. 將 dashboard inline JS 拆到 `static/dashboard/*.js`，至少分出 forms、sidebar、tabs、modals、hit records、revision client、debug tools。
3. 將 `style.css` 改為 CSS 入口並拆到 `static/styles/*.css`；至少切分 tokens/base、layout、sidebar、target card、preview rows、settings modal、hit records modal、diagnostics/debug、feedback、responsive。
4. 將 `webapp/schemas.py` 拆成 preview、hit record、dashboard / settings summary 等 ViewModel 模組；`schemas.py` 只保留 compatibility exports。
5. 將 `match_history` 納入 dashboard revision source，確保命中紀錄新增 / 清空都能讓 dashboard revision 改變。
6. 不做新的視覺功能，不直接進 Phase 9 收合 / 展開，也不做 Phase 10 SSE。

不碰範圍：

1. resident worker。
2. notification outbox。
3. scan finalize。
4. posts/comments extractor。
5. scheduler queue / concurrency。

完成條件：

```text
首頁可正常渲染，查看紀錄與設定 modal 行為維持不變；
dashboard JS 不再內嵌於 index.html；
ViewModel 不再集中於單一 schemas.py；
清空 match_history 會推進 dashboard revision。
```

---

### Phase 9：視覺整理、Responsive、空狀態與驗收

目標：統一視覺、確認不同寬度下的可用性，並補上不改動資料語義的卡片層級互動 polish。

工作：

1. 字級層級。
2. 間距。
3. 對齊。
4. badge 樣式。
5. tabs 樣式。
6. modal overlay。
7. 空狀態。
8. sidebar sticky / scroll。
9. 中小螢幕寬度測試。
10. target card 收合 / 展開互動：
    - 預設維持展開。
    - 卡片右上可提供向上 / 向下箭頭按鈕。
    - 收合後只顯示 target header、狀態、最近掃描 / 最近通知、命中數與設定摘要等整體摘要。
    - 收合時不顯示關鍵字 textarea、preview tabs、preview rows 或掃描診斷內容。
    - 收合狀態不得改變 target enabled / paused、scan runtime、notification、history 或 settings 資料。
    - 若有自動刷新或 reload，應盡量維持使用者剛才的收合 / 展開狀態；若不能可靠維持，需明確記錄為限制。
11. 驗收「最近掃描」與「命中紀錄」是否仍共用 row component。

---

## 19. 驗收標準

完成後必須符合：

1. 主畫面沒有左上 Facebook 圖示。
2. 主畫面沒有頂部背景掃描服務 pill。
3. 主畫面沒有左下常駐小提醒卡片。
4. 左側 sidebar 只做快速跳轉。
5. 點擊 sidebar 後右側 scroll 到對應 card。
6. 右側仍然顯示所有 target cards，不是單一卡片模式。
7. 每張卡片可一眼看到狀態、最近掃描與最近通知。
8. 卡片操作列包含 `開始 / 停止`、`儲存`、`查看紀錄`、`設定`、`⋯`。
9. 刪除不作為高調常駐按鈕。
10. 左側卡片區只保留關鍵字與設定摘要。
11. 低頻設定集中到設定 modal。
12. 固定 / 浮動刷新真的保存並影響 scheduler interval，不只是 UI 欄位。
13. 最近掃描 tab 顯示最近一次 scan snapshot。
14. 命中紀錄 tab 顯示歷史命中 preview。
15. 最近掃描 tab 與命中紀錄 tab 使用同一個 preview row partial / component。
16. 命中紀錄 tab 不顯示貼文 / 留言類型欄。
17. 命中紀錄 tab 不顯示獨立時間欄。
18. 命中紀錄 tab 不顯示查看全部 / 清空 toolbar。
19. `查看紀錄` modal 顯示完整詳細紀錄。
20. `清空紀錄` 只出現在完整紀錄 modal。
21. 清空紀錄前有確認提示。
22. 清空紀錄不影響 target config、latest scan、seen、notification outbox。
23. 掃描診斷預設收起。
24. UI 不重新攪動 worker / notification / scan pipeline / resident 主線。

---

## 20. 給 Codex 的實作指令草案

```text
請依照 docs/ui_refactor/ui_refactor_plan.md 進行 UI 重構。

請同時參考以下圖片：
- docs/ui_refactor/reference_images/01_dashboard_recent_scan.png
- docs/ui_refactor/reference_images/02_dashboard_hit_records_tab.png
- docs/ui_refactor/reference_images/03_target_settings_modal.png
- docs/ui_refactor/reference_images/04_full_hit_records_modal.png

執行順序必須遵守文件第 18 節，不要先做視覺美化。

核心要求：
1. 先補齊查看紀錄資料能力：preview、full list、count、clear-by-target。
2. 先補齊固定 / 浮動刷新資料與 scheduler 使用邏輯。
3. Phase 8 後、Phase 9 前必須先做 Phase 8.5 UI 層架構整理：拆 template partial、拆 dashboard JS、拆 CSS、拆 ViewModel、補 match_history revision source。
4. 左側 sidebar 只做快速跳轉，不要把右側改成單一卡片模式。
5. 右側仍然顯示所有 target cards。
6. 主畫面移除左上 Facebook 圖示、頂部背景掃描服務 pill、左下小提醒卡片。
7. target card 操作列固定為：[開始/停止] [儲存] [查看紀錄] [設定] [⋯]。
8. 右側 tabs 只有 [最近掃描] [命中紀錄 23]。
9. tabs 內不要再加第二層 toolbar，不要放查看全部，不要放清空。
10. 最近掃描與命中紀錄 tab 必須使用同一套 preview row partial / component。
11. preview row 格式為：作者 + badge 在第一行，內容摘要在下方並使用整個可用寬度，右側為開啟原文。
12. 命中紀錄 tab 不要顯示貼文 / 留言類型欄、編號欄、獨立時間欄或表格格式。
13. 完整詳細欄位只出現在「查看紀錄」modal。
14. 清空紀錄只出現在「查看紀錄」modal，且必須有確認。
15. 設定 modal 需支援固定刷新與浮動刷新。
16. 不要修改 resident worker、notification outbox 狀態機、scan_finalize 核心邏輯、posts/comments extractor、scheduler queue/concurrency。

完成後請回報：
1. 修改了哪些 template / CSS / route / query service / view model。
2. 查看紀錄資料能力如何實作：preview、full list、count、clear-by-target。
3. 清空紀錄是否只作用於該 target 的命中紀錄。
4. 固定 / 浮動刷新如何保存、驗證、顯示與影響 scheduler interval。
5. Sidebar 如何定位與跳轉。
6. 最近掃描與命中紀錄是否共用同一個 preview row component / partial。
7. Phase 8.5 是否仍維持 template / JS / CSS / ViewModel 拆分，不再回到胖 `index.html`、胖 `style.css` 或胖 `schemas.py`。
8. 是否有碰到禁止修改的主線；若有，列出原因與範圍。
```

---

## 21. 後續可再討論但不列入第一版 blocker

1. 深色模式。
2. target 分組。
3. 全域命中紀錄頁。
4. 全域搜尋。
5. 未讀命中狀態。
6. 匯出命中紀錄。
7. mobile drawer sidebar。
8. 社團縮圖自動抓取與快取。
9. target detail route。

---

## 22. 最終結論

這份正式實作版的核心紀律是：

1. `查看紀錄` 與 `固定 / 浮動刷新` 先補功能，再進入卡片重排與 modal 實作。
2. `命中紀錄` tab 只是 preview，不是管理頁。
3. `查看紀錄` modal 才是詳細管理頁。
4. 最近掃描與命中紀錄 tab 必須共用同一個 preview row component。
5. 左側 sidebar 是快速跳轉，不是第二套操作面板。
6. UI 重構不得重新攪動已封口的 worker、notification outbox、scan pipeline、resident scheduler 主線。
