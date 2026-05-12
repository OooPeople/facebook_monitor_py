# 參考檔案對照

`reference/` 目錄包含從既有 Tampermonkey userscript 專案複製過來的檔案。這些檔案是 Python 版移植功能語義時的參考輸入；不要在本專案內修改 `reference/` 來修 userscript。

本文件只負責 JS 參考資料索引。文件職責邊界看 `docs/README.md`；Python 版穩定架構看 `docs/ARCHITECTURE.md`；目前進度看 `docs/TASK_BREAKDOWN.md`。

## 主要參考

- `reference/src/facebook_group_refresh.user.js`
  - 目前的單檔 Tampermonkey 實作。
  - 重要功能語義來源：target / scope model、keyword rules、dedupe、notification formatting、sort / scroll helper chain、posts/comments extractor 想法。

## 輔助參考

- `reference/README.md`：既有 userscript 的使用者導向概覽。
- `reference/docs/USAGE.md`：目前使用者行為與設定說明。
- `reference/docs/ARCHITECTURE_PLAN.md`：既有 userscript 架構與 runtime 邊界。
- `reference/docs/TASK_BREAKDOWN.md`：既有驗證與任務拆解。
- `reference/scripts/smoke_check_userscript.js`：目前 userscript 的 smoke test，可作為未來 Python 版重新驗證行為時的 checklist。

## 已對照概念

- keyword parsing：`src/facebook_monitor/core/keyword_rules.py`
- seen-item key / alias：`src/facebook_monitor/core/dedupe.py`
- notification message 欄位：`src/facebook_monitor/notifications/payload.py`
- refresh policy：`src/facebook_monitor/core/refresh_policy.py`
- posts/comments 共用文字片段合併：`src/facebook_monitor/facebook/text_snippet_dom.py`

## 使用原則

- 優先移植功能語義，不逐行翻譯 userscript。
- posts/comments 可共用純文字片段合併語義；selector、permalink、sort、load-more 與 target scope 維持各自邏輯。
- 不把 userscript 的 long-lived page observer、panel UI 或 route lifecycle model 直接複製到 Python worker。
