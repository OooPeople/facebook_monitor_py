# 交接摘要

本文件只保存新對話或下一位 agent 接手所需的最小資訊。
目前狀態與下一步看 `docs/TASK_BREAKDOWN.md`；穩定架構事實看 `docs/ARCHITECTURE.md`。

## 接手順序

1. 讀 `AGENTS.md`，確認 JS 移植規則與專案協作規範。
2. 讀 `docs/TASK_BREAKDOWN.md`，取得目前狀態、下一步、風險與不做事項。
3. 需要判斷模組職責或主路徑時，讀 `docs/ARCHITECTURE.md`。
4. 涉及 JS 成熟語義時，依 `docs/REFERENCE_MAP.md` 對照 `reference/src/facebook_group_refresh.user.js`。

## 啟動

- Web UI：`.\scripts\uv.ps1 run python .\scripts\start\webui.py`
- profile 登入 / 檢查：`.\scripts\uv.ps1 run python .\scripts\start\setup_login.py`
- automation profile：`data/profiles/automation_default`
- Web UI 啟動時預設清除可重建 runtime/debug data；若要保留，加 `--keep-runtime-data-on-startup`。

## 日常語義

- Web UI 是正式日常入口；背景 scheduler 隨 Web UI 啟動。
- target 卡片「開始 / 停止」是主操作。
- 「開始」會清該 target seen scope、要求立即掃描並喚醒 scheduler。
- 「停止」只暫停排程，保留 seen/history。
- 新增 target 只貼 Facebook URL；系統自動判斷 posts/comments。
- keyword / refresh / notification 是 group-scoped config。
- seen、latest scan、history、notification events、runtime state 是 target-scoped。

## 主路徑提醒

- resident main worker 是正式產品主路徑。
- Web UI scheduler 與 scan-once 一律走 resident request / executor。
- one-shot 與 sync resident 只作 CLI/debug/fallback，不是正式產品 parity。
- 舊 `target_configs` 只作 migration fallback；正式路徑讀寫 `group_configs`。
- Python 預設值集中於 `core/defaults.py`。

## 高風險提醒

- 不要啟動 Web UI、background worker 或 browser 實測，除非使用者明確同意。
- 遇到 extractor 問題時，先看整輪 scan diagnostics 與單筆 item debug，再對照 JS 版小步修正。
- posts/comments 可共用文字片段合併 helper，但 selector、permalink、sort、load-more 與 target scope 不應硬合併。
- UI 小修應維持目前拆分邊界，不要把互動塞回 `index.html` inline script、`style.css` 單一大檔或胖 ViewModel。
- notification failed retry 必須走明確 retry API；一般 scan commit 只處理 pending outbox。
- `auto_load_more` posts 目前完成 scroll 模式，`loadMoreMode=wheel` 仍 deferred。
- Python resident main worker 目前是 polling，不宣稱 mutation relevance 已接上即時觸發。

## 驗證

最近驗證指令與結果看 `docs/TASK_BREAKDOWN.md#驗證`。
