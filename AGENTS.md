# facebook_monitor_py

本專案是 Facebook 社團 / 貼文監視器 Python + Playwright 版的 Phase 0 spike。它是從既有 Tampermonkey userscript 轉換執行模型的獨立專案，不要把它當成 userscript 專案本身的一部分。

目前最重要的目標不是建立完整應用架構，而是先證明：

- headed setup session 可以用專用 automation profile 登入。
- headless worker 可以重用同一個 profile。
- 一個 Facebook 社團貼文 target 可以在不維持前景 Facebook 視窗的情況下被監視。

Phase 0 未通過前，不要展開完整 app 架構。

## 文件索引

不要每次完整讀完所有文件；依任務類型只讀必要文件：

- 專案入口與基本使用：`README.md`
- 完整遷移計劃與後續架構：`docs/facebook_python_migration_plan.md`
- Phase 0 立即執行計劃：`docs/PHASE0_SPIKE.md`
- 交接、目前狀態、下一步：`docs/HANDOFF.md`
- userscript 參考檔案對照：`docs/REFERENCE_MAP.md`

若任務跨多個面向，先讀 `docs/HANDOFF.md` 快速定位，再補讀相關文件。

## Phase 0 範圍

允許在 Phase 0 做：

- 最小 Playwright setup 與 worker probe。
- 一個 group feed posts target。
- 最小 include keyword matching。
- 最小 seen-item dedupe。
- log 輸出與可選的 ntfy 測試通知。
- 專用 automation profile，位置固定在 `data/profiles/`。

Phase 0 不做：

- 多 target worker orchestration。
- comments target 支援。
- FastAPI 或桌面 UI。
- SQLite repository 架構。
- EXE 打包。
- stealth automation、CAPTCHA bypass、OCR 或帳號自動操作。
- 使用者日常 Chrome profile。

## 絕對禁止事項

- 不可 commit 真實 browser profile、cookies、tokens、session dumps，或包含私人資料的 logs。
- 不可使用使用者日常 Chrome profile。
- 不可把 profile 放到 `data/profiles/` 以外的位置；此路徑除了 `.gitkeep` 以外應維持 git ignored。
- 不可把 runtime logs 放到 `logs/` 以外的位置；此路徑除了 `.gitkeep` 以外應維持 git ignored。
- Phase 0 最小依賴以外的新第三方套件，新增前必須先詢問。
- 不要機械式逐行翻譯 userscript；只能把它當作行為參考。

## 工作規則

- Phase 0 通過前，優先寫小而可測的 scripts。
- 本專案使用 `uv` 管理環境；PowerShell 指令優先走 `.\scripts\uv.ps1`。
- 每次 probe 失敗都要留下清楚分類：login/session、headless DOM、page load、selector/extractor、notification 或 unknown。
- headless 失敗時，先測 persistent-context 行為，再評估 headed compatibility mode。
- 不要提前建立正式 DB / repository / UI 架構。
- 讀取或修改 `.md` 時使用 UTF-8。

## 重要檔案

- `scripts/uv.ps1`：專案限定 uv wrapper。
- `scripts/phase0_setup_login.py`：headed login / setup probe。
- `scripts/phase0_worker_probe.py`：headless worker probe。
