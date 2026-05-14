# Facebook Monitor Py

Facebook Monitor Py 是一個本機優先的 Facebook 社團監視工具，使用 Python、Playwright、FastAPI 與 SQLite 實作。它把成熟的瀏覽器 userscript 工作流，搬遷成可維護的本機 Web UI，透過獨立 automation profile 監視社團貼文與單篇貼文留言。

這個專案不只是「能跑的爬蟲」。它重點放在 target-scoped state、安全的本機操作、可恢復的通知發送、清楚的診斷資訊，以及從成熟 userscript 搬遷時避免半套移植的工程紀律。

## 專案用途

- 監視 Facebook 社團貼文列表與單篇貼文留言串。
- 使用者貼上 Facebook URL 後，系統自動建立 posts 或 comments target。
- 透過本機 Web UI 操作，Playwright 使用專用 automation profile 執行瀏覽器。
- 每個 target 都有自己的關鍵字、排除規則、刷新策略、runtime state、seen、latest scan、match history 與通知設定。
- 命中後可透過 desktop、ntfy、Discord 通知，並以 notification outbox 保留可重試邊界。
- 掃描診斷會保留排序調整、載入更多、抽取結果、停止原因與 runtime failure。

## 技術重點

本專案把原始 JavaScript userscript 視為功能語義來源，而不是逐行翻譯來源。Python 版依責任分層，讓系統可以演進，同時保留成熟行為：

- `core/`：資料模型、預設值、keyword 規則與純 policy。
- `application/`：target command、config transition 與 orchestration。
- `facebook/`：route detection、permalink、sort/load-more helper 與 DOM extraction。
- `worker/`：posts/comments scan pipeline 與 shared scan finalize。
- `notifications/`：sender、channel dispatch 與 outbox retry 規則。
- `persistence/`：SQLite schema、migration、repository 與 runtime data cleanup。
- `webapp/`：FastAPI routes、presenter、template、static module 與 form model。

## 架構亮點

- **Target-scoped model**：posts 與 comments target 各自擁有 scope、config、seen state、latest scan、match history 與 runtime state。
- **單一日常主開關**：target 卡片的「開始 / 停止」是使用者主操作；背景 scheduler 是 Web UI 內部服務。
- **Notification outbox boundary**：scan transaction 先寫入 match data 與 outbox，再執行外部 I/O；發送失敗的通知仍可重試。
- **本機安全預設**：launcher 預設只綁 loopback，mutating route 需要 CSRF token，且不使用使用者日常 Chrome profile。
- **Migration discipline**：SQLite schema 有明確版本；legacy `group_configs` 只作 migration 來源；新 schema 變更必須走明確 migration。
- **Diagnostics-first scanning**：sort、load-more、extractor 行為會記錄結構化 metadata，不把失敗壓成單一 `False` 或 `None`。

## 快速開始

本專案使用 `uv` 與 Python 3.13。Windows PowerShell 請優先使用專案 wrapper：

```powershell
.\scripts\uv.ps1 sync
.\scripts\uv.ps1 run playwright install chromium
.\scripts\uv.ps1 run facebook-monitor
```

初次登入或檢查 Facebook automation profile：

```powershell
.\scripts\uv.ps1 run facebook-monitor-login
```

macOS 或其他 shell 若已安裝 `uv`，可直接使用：

```bash
uv sync
uv run playwright install chromium
uv run facebook-monitor
```

詳細安裝、日常使用、命令參數、troubleshooting 與資料路徑，請看 [docs/USAGE.md](docs/USAGE.md)。

## 文件

- [使用說明](docs/USAGE.md)：安裝、啟動、target 建立、通知、資料路徑與 troubleshooting。
- [架構說明](docs/ARCHITECTURE.md)：穩定系統邊界與產品語義。
- [工具索引](docs/tooling.md)：admin、debug、internal scripts。
- [任務狀態](docs/TASK_BREAKDOWN.md)：目前狀態、風險與最近驗證。
- [交接摘要](docs/HANDOFF.md)：新對話或下一位 agent 接手用的最小上下文。
- [協作規則](AGENTS.md)：協作規則與 userscript parity policy。

## 驗證

常用完整驗證：

```powershell
.\scripts\uv.ps1 run pytest -q
.\scripts\uv.ps1 run python -m compileall -q src scripts tests
.\scripts\uv.ps1 run ruff check src scripts tests
git diff --check
```

## 安全注意事項

- 只使用 app data directory 內的專用 automation profile。
- 不 commit cookies、browser profiles、tokens、session dumps、本機 logs 或私人 runtime data。
- notification endpoint 屬於敏感診斷資料；sender exception 與 UI error message 在保存或顯示前都必須安全化。
- notification secrets 在 SQLite 內會加密保存；安全邊界記錄在 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#notification-與-secret)。
- Web UI 預設只供本機 loopback 使用，除非明確改用其他 host 設定。
