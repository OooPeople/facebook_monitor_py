# 工程審查指南

本文件定義本專案預設 review 範圍與輸出格式。使用者只要說「審查」、「review」、「架構審查」、「幫我看這次變更」或類似要求，除非明確限定只看某一項，預設都要用完整工程審查視角，而不是只看語法或測試是否通過。具體產品語義仍以 `docs/ARCHITECTURE.md` 為主，本文件不取代功能規格。

參考基準：

- Google Engineering Practices：design、functionality、complexity、tests、naming、comments、style、documentation。
- GitLab Code Review Guidelines：quality、performance、reliability、security、observability、maintainability、backwards compatibility、deployment。
- OWASP Code Review Guide / Secure Code Review Cheat Sheet：manual security review、trust boundary、input validation、authentication / authorization、data flow、error handling、configuration、dependency risk。

## 預設審查清單

### 1. 需求與產品語義

- 是否真的解決使用者問題，而不是只補 UI、欄位或函式名稱。
- 是否符合本專案 Python 版既有 target、scheduler、worker、notification、Web UI 語義。
- 是否有改變使用者可見行為；若有，原因與風險是否講清楚。

### 2. 架構邊界與依賴方向

- 是否遵守 domain / application / infrastructure / webapp / worker / scripts 的既有責任邊界。
- UI 變更是否偷改 worker scan pipeline、notification outbox、scheduler recovery、persistence migration 或 Facebook DOM helper。
- 新 helper / service / repository 是否放在正確層級，沒有形成平行流程或繞過正式入口。

### 3. 單一來源與狀態流程

- 版本、預設值、enum / status 字串、schema version、打包檔名、runtime 門檻、UI cache key 是否有單一權威來源。
- 狀態 owner 是否清楚；同一語義不得同時散在 UI state、DB 欄位、worker local state、service return string、diagnostics JSON 而沒有同步規則。
- 若存在必要分散，必須說明語義差異，例如 app version 與 schema version 可分離、產品資料與 maintenance job state 可分離。

### 4. 資料模型、migration 與相容性

- schema bootstrap 與 migration chain 是否一致；已發布版本的 migration 不得被倒改成另一個歷史。
- 舊資料、缺欄、空值、異常值、使用者自訂名稱 / 設定是否會被覆蓋或破壞。
- 是否需要 backfill、節流、去重、FK cascade、index 或 revision trigger。

### 5. 正確性與邊界條件

- 成功、失敗、重試、取消、停止、target 不存在、登入失效、Facebook 暫時錯誤、網路錯誤、空資料、stale DOM / stale DB 狀態是否都有明確行為。
- 是否有 race condition、重複排程、重複通知、卡在 pending/running、資源未關閉或跨輪狀態污染。

### 6. 安全、隱私與信任邊界

- 是否處理外部輸入驗證、輸出 escaping、CSRF / auth / authorization、路徑 traversal、命令注入、SQL 注入、URL redirect、secret/token/cookie/profile/log 外洩。
- 是否新增第三方依賴、下載、解壓、更新套用、subprocess、browser profile 操作；若有，要提高審查等級。

### 7. 效能、可靠性與資源使用

- 是否造成每輪掃描額外重負擔、無界迴圈、無界 query、過度開頁、未關閉 browser page/context、過高 timeout、過度 polling。
- 是否對大量 targets、長時間 resident worker、低規格機器、失敗重試與 app shutdown 仍可靠。

### 8. 可維護性、可讀性與擴充性

- 名稱是否準確，抽象是否必要，重複是否合理，未來新增通道 / target kind / platform / status 是否需要到處改。
- 是否有半套 abstraction、過早 abstraction、過深 call chain 或難以測試的隱式副作用。
- `scripts/admin/complexity_report.py` 可作為大函式 / 大檔案 / CCN 排名入口，但 report 只提供人工審查線索，不是 pass/fail 規則；是否拆分仍以產品語義、狀態流程、交易邊界與測試風險判斷。

### 9. 可觀測性與診斷

- 重要行為是否能從 latest_scan metadata、worker log、runtime diagnostics、DB state 或 UI debug 看出 attempted / changed / before / after / reason / count / worker。
- 失敗是否保留可行動的 reason，而不是只留下 `False`、`None` 或泛用錯誤字串。

### 10. 測試與驗證

- 是否有覆蓋正常路徑、失敗路徑、stale/race、migration/backcompat、UI route/JS contract、worker end-to-end 的測試。
- 測試是否真的驗證語義，不只是確認函式被呼叫。
- 若未跑某類測試，必須說明原因與剩餘風險。

### 11. 文件、打包與操作

- 使用者操作、packaging、release、update、diagnostics、AGENTS 守則是否需要同步。
- 打包檔名、version resource、SHA256、GitHub tag、APP_VERSION、platform policy 是否仍對齊。

## 輸出格式

審查結果必須 findings first，依嚴重度排序。每個 finding 要包含：

- 嚴重度
- 檔案與行號
- 具體問題與可能後果
- 建議修正方向

若沒有阻塞問題，也要明確說明：

- 審查了哪些面向
- 哪些必要分散是可接受的
- 還有哪些低風險後續整理項目
- 驗證了哪些測試 / 指令

## Handoff 補充

完成一段功能後，handoff 必須包含：

1. 對照了哪些 Python 模組、資料模型、測試或文件契約。
2. 哪些語義已完整接通。
3. 哪些還沒完成。
4. 目前是完整功能、部分功能、還是只有殼。
5. 若有刻意偏離既有產品語義，原因是什麼。
