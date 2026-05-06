# 交接狀態

## 目前狀態

這是一個新建立的 Python + Playwright 工作區，用於 Facebook 監視器重寫 / spike。

目前專案只包含：

- Phase 0 規劃文件。
- 最小 Playwright setup 與 worker probe scripts。
- 從既有 userscript 專案複製過來的參考檔案。

目前已使用 `.\scripts\uv.ps1 sync` 建立 / 同步 uv 環境並產生 `uv.lock`。尚未從這個工作區下載或執行 Playwright browser。

## 已完成變更

- 建立 `facebook_monitor_py`，作為 `facebook_group_refresh` 的同層獨立專案。
- 將遷移計劃移到 `docs/facebook_python_migration_plan.md`。
- 新增根目錄 `AGENTS.md`，供後續 agent 參考。
- 新增 Phase 0 文件：
  - `docs/PHASE0_SPIKE.md`
  - `docs/HANDOFF.md`
  - `docs/REFERENCE_MAP.md`
- 新增初始 Phase 0 scripts：
  - `scripts/uv.ps1`
  - `scripts/phase0_setup_login.py`
  - `scripts/phase0_worker_probe.py`
- 將 userscript 參考資料複製到 `reference/`。

## 下一步建議

將此資料夾作為 Codex workspace 開啟：

```text
E:\P3\xx\ticket\facebook_monitor_py
```

然後依序執行 Phase 0：

1. 執行 `.\scripts\uv.ps1 sync` 建立 / 同步 uv 環境。
2. 執行 `.\scripts\uv.ps1 run playwright install chromium` 安裝 Playwright browser。
3. 執行 `.\scripts\uv.ps1 run python .\scripts\phase0_setup_login.py`。
4. 在 headed browser 中登入 Facebook，並切到目標社團頁面。
5. 使用該社團 URL 執行 `.\scripts\uv.ps1 run python .\scripts\phase0_worker_probe.py "https://www.facebook.com/groups/<group_id>"`。
6. 記錄 headless mode 是否能重用 profile。

## 現階段不要做

- 不要建立 FastAPI UI。
- 不要新增 SQLite repositories。
- 不要實作 comments monitoring。
- 不要移植完整 userscript。
- 不要使用使用者日常 Chrome profile。

## 關鍵決策點

目前最重要的未解風險不是程式結構，而是 Facebook 能否在 Playwright-controlled headless worker 中，使用獨立 automation profile 被穩定監視。
