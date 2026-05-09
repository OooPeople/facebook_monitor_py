# 參考檔案對照

`reference/` 目錄包含從既有 Tampermonkey userscript 專案複製過來的檔案。這些檔案是 Python 版移植功能語義時的參考輸入；不要在本專案內修改 `reference/` 來修 userscript。

本文件只負責 JS 參考資料對照。Python 版目前架構看 `docs/ARCHITECTURE.md`；目前進度看 `docs/TASK_BREAKDOWN.md`；早期 Python 遷移計畫與可行性 spike 紀錄已移到 `docs/archive/`。

## 檔案

- `reference/src/facebook_group_refresh.user.js`
  - 目前的單檔 Tampermonkey 實作。
  - 重要功能語義參考：target / scope model、keyword rules、dedupe、notification formatting、sort / scroll helper chain 與 extractor 想法。

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

- keyword parsing 語義已移植到 `src/facebook_monitor/core/keyword_rules.py`；後續調整請先改這個核心模組與測試。
- 移植 target / scope identity 概念。
- seen-item key / alias 想法已移植到 `src/facebook_monitor/core/dedupe.py`；後續不要退回單一 hash key。
- notification message 欄位已移植到 `src/facebook_monitor/notifications/payload.py`。
- refresh policy 已移植到 `src/facebook_monitor/core/refresh_policy.py`，Web UI 支援固定 / 浮動刷新設定。
- posts/comments DOM extraction 以 Playwright 重寫；可共用的文字片段合併語義集中於 `src/facebook_monitor/facebook/text_snippet_dom.py`，其餘 selector、permalink、sort、load-more 與 target scope 維持各自邏輯。

不要把 userscript 的 long-lived page observer、panel UI 或 route lifecycle model 複製到 Python worker。
