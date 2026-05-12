# 交接摘要

本文件只保留新對話或下一位 agent 接手所需的最小資訊。完整啟動方式看 README；工具索引看 `docs/tooling.md`；穩定架構看 `docs/ARCHITECTURE.md`；目前狀態看 `docs/TASK_BREAKDOWN.md`。

## 接手順序

1. 讀 `AGENTS.md`，確認協作規則、JS 移植規則與 UI 重構邊界。
2. 讀 `docs/TASK_BREAKDOWN.md`，取得目前狀態、下一步、風險與最近驗證。
3. 需要正式主路徑、資料語義或模組職責時，讀 `docs/ARCHITECTURE.md`。
4. 需要 scripts / CLI 命令時，讀 `docs/tooling.md`。
5. 涉及 JS 成熟語義時，依 `docs/REFERENCE_MAP.md` 對照 `reference/src/facebook_group_refresh.user.js`。

## 最小啟動

```powershell
.\scripts\uv.ps1 run facebook-monitor
.\scripts\uv.ps1 run facebook-monitor-login
```

預設使用 `~/facebook_monitor_data`、先嘗試 port `4818`，並開啟瀏覽器。自訂資料目錄或 profile 時，Web UI 與登入工具必須使用同一組 `--data-dir` / `--profile-name`。

## 接手提醒

- Web UI 是正式日常入口；resident main worker 是正式產品主路徑。
- `--profile-dir` 只能指向 `<data-dir>/profiles/` 底下；外部測試 profile 才使用 `--unsafe-profile-dir`，不可指向日常 Chrome / Edge profile。
- target 卡片「開始 / 停止」是主操作；scheduler 是背景服務，不是第二個主開關。
- Web UI 啟動時預設停止 targets，不自動恢復上次掃描。
- scheduler running 時新增 target 不同步搶 profile；名稱解析交給 resident metadata refresh。
- 正式 config 路徑只讀寫 `target_configs[target_id]`；`group_configs` 只保留為 migration 來源。
- Dashboard 更新維持短生命週期 revision event stream + batch partial update；不要描述成真正長連線 SSE。
- keyword highlight 由 `webapp/highlight.py` 產生 text segments；template / JS 不應改回 `innerHTML` 字串替換。
- Python 預設值集中於 `core/defaults.py`。
- 不要啟動 Web UI、background worker 或 browser 實測，除非使用者明確同意。
- UI 小修維持現有拆分邊界，不要把互動塞回 `index.html` inline script、`style.css` 單一大檔或胖 ViewModel。

## 驗證

最近驗證指令與結果看 `docs/TASK_BREAKDOWN.md#驗證`。
