# Facebook 監視器 Python Spike

本工作區是 Facebook 社團 / 貼文監視器的 Python + Playwright Phase 0 可行性驗證專案。

Phase 0 目標刻意很小：先證明有視窗的 setup session 可以登入並選定監視目標，接著無頭背景 worker 可以重用專用 automation profile，而不需要維持前景 Facebook 視窗。

從 userscript 專案複製過來的參考資料放在 `reference/`。專案計劃與交接文件放在 `docs/`。

## uv 指令

本專案使用 `uv` 管理環境。Windows PowerShell 請優先使用專案 wrapper：

```powershell
.\scripts\uv.ps1 sync
.\scripts\uv.ps1 run playwright install chromium
```

執行有視窗 setup：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase0_setup_login.py
```

登入 Facebook 並切到目標社團後，在 terminal 按 Enter 關閉 setup。

執行無頭 worker probe：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase0_worker_probe.py "https://www.facebook.com/groups/<group_id>"
```

## Phase 0 檔案

- `docs/facebook_python_migration_plan.md`：遷移與實作計劃。
- `docs/PHASE0_SPIKE.md`：Phase 0 立即執行計劃。
- `docs/HANDOFF.md`：交接與目前狀態。
- `docs/REFERENCE_MAP.md`：userscript 參考檔案說明。
- `scripts/uv.ps1`：專案限定 uv wrapper。
- `scripts/phase0_setup_login.py`：有視窗登入 / setup probe。
- `scripts/phase0_worker_probe.py`：無頭 worker probe。
- `data/profiles/`：專用 automation browser profiles，不可 commit 真實 profile data。
- `logs/`：本機執行紀錄。

## 規則

- 不使用使用者日常 Chrome profile。
- 不把 cookies、tokens、session dumps 或 profile data 放進 git。
- Phase 0 先專注於一個社團貼文監視目標，再建立完整 app 架構。
