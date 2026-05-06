# 參考檔案對照

`reference/` 目錄包含從既有 Tampermonkey userscript 專案複製過來的檔案。這些檔案只能作為參考輸入，不要把它們當作 userscript 的 source of truth 來修改。

## 檔案

- `reference/src/facebook_group_refresh.user.js`
  - 目前的單檔 Tampermonkey 實作。
  - 用於行為參考：target / scope model、keyword rules、dedupe、notification formatting 與 extractor 想法。

- `reference/README.md`
  - 既有 userscript 的使用者導向概覽。

- `reference/docs/USAGE.md`
  - 目前使用者行為與設定說明。

- `reference/docs/ARCHITECTURE_PLAN.md`
  - 既有 userscript 架構與 runtime 邊界。

- `reference/docs/TASK_BREAKDOWN.md`
  - 既有驗證與任務拆解。

- `reference/scripts/smoke_check_userscript.js`
  - 目前 userscript 的 smoke test。可作為未來 Python 版重新驗證行為時的 checklist。

## 移植指引

優先移植概念，不要移植檔案本身：

- 移植 keyword parsing 語義。
- 移植 target / scope identity 概念。
- 移植 seen-item key 想法。
- 移植 notification message 欄位。
- 針對 Playwright 重寫 browser interaction 與 DOM extraction。

不要把 userscript 的 long-lived page observer、panel UI 或 route lifecycle model 複製到 Python worker。
