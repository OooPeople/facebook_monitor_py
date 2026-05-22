# Facebook Monitor Py

Facebook Monitor Py 是一個本機優先的 Facebook 社團監視工具，使用 Python、Playwright、FastAPI 與 SQLite 實作。它透過獨立 automation profile 監視社團貼文與單篇貼文留言，並以本機 Web UI 管理 target、掃描狀態與通知。

這個專案不只是「能跑的爬蟲」。它重點放在 target-scoped state、安全的本機操作、可恢復的通知發送、清楚的診斷資訊，以及可長期維護的 Python 應用邊界。

## 專案用途

- 監視 Facebook 社團貼文列表與單篇貼文留言串。
- 使用者貼上 Facebook URL 後，系統自動建立 posts 或 comments target。
- 透過本機 Web UI 操作，Playwright 使用專用 automation profile 執行瀏覽器。
- 每個 target 都有自己的關鍵字、排除規則、刷新策略、runtime state、seen、latest scan、match history 與通知設定。
- 命中後可透過 desktop、ntfy、Discord 通知，並以 notification outbox 保留可重試邊界。
- 掃描診斷會保留排序調整、載入更多、抽取結果、停止原因與 runtime failure。

## 技術重點

Python 版依責任分層，讓掃描、通知、持久化與 Web UI 可以各自演進：

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
- **Frozen portable updater**：Windows portable 與 macOS Apple Silicon onedir 版可從 GitHub stable Release 檢查、下載、驗證 signed manifest / SHA256，並透過獨立 updater 替換 app files、保留 `data/`。
- **Migration discipline**：SQLite schema 有明確版本；legacy `group_configs` 只作 migration 來源；新 schema 變更必須走明確 migration。
- **Diagnostics-first scanning**：sort、load-more、extractor 行為會記錄結構化 metadata，不把失敗壓成單一 `False` 或 `None`。

## 歷史來源

本專案最初參考成熟的 Facebook userscript 工作流，原始 JS 專案位於 [OooPeople/facebook_group_refresh](https://github.com/OooPeople/facebook_group_refresh)。目前 Python 版已作為獨立專案維護；該 repo 只作為歷史背景與必要時的行為追溯來源，不再於本 repo 內保存副本。

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

- [文件索引](docs/README.md)：公開文件職責邊界與更新規則。
- [使用說明](docs/USAGE.md)：安裝、啟動、target 建立、通知、資料路徑與 troubleshooting。
- [架構說明](docs/ARCHITECTURE.md)：穩定系統邊界與產品語義。
- [工程審查指南](docs/ENGINEERING_REVIEW.md)：預設 review 範圍、輸出格式與 handoff 要求。
- [工具索引](docs/tooling.md)：admin、debug、internal scripts。
- [打包說明](packaging/README.md)：Windows portable EXE、macOS Apple Silicon onedir、Release asset 與 frozen smoke checklist。
- [協作規則](AGENTS.md)：代理開工守則、禁止事項與文件查找索引。

## 驗證

常用完整驗證：

```powershell
.\scripts\uv.ps1 run python scripts\admin\release_validation.py
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
- ntfy / Discord 通知會把通知內容送到對應第三方服務；不要把不想外送的私人內容放進通知 payload。
- 需要提供診斷資料時，優先用 Settings 下載 redacted support bundle；不要直接分享 SQLite DB、browser profile、cookies、secrets、logs 或完整 webhook。
- 自動更新使用免費 Ed25519 signed manifest 驗證 release 內容來源；Windows Authenticode、macOS Developer ID signing 與 notarization 目前未做，可能仍會看到 SmartScreen、Defender 或 Gatekeeper 提示。
- Facebook 可能因登入失效、checkpoint、權限變更、版面改版或自動化偵測而讓掃描暫停或失敗；本工具會保留診斷與失敗 reason，但不能保證 Facebook DOM 長期穩定。
