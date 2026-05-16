# 文件索引

本頁定義文件職責邊界。若同一資訊需要在多處出現，原則是「一份主來源 + 其他文件短句連結」，避免 README、USAGE、ARCHITECTURE、TASK_BREAKDOWN 彼此累積重複細節。

## 主文件

| 文件 | 職責 | 不應放 |
|---|---|---|
| `AGENTS.md` | 協作規則、禁止事項、JS 移植守則與 UI 重構邊界 | 目前進度、驗證結果 |
| `README.md` | GitHub 專案首頁：專案用途、核心能力、架構亮點、文件入口 | 詳細操作步驟、scripts 全索引、歷史批次 |
| `docs/USAGE.md` | 詳細安裝、啟動、target 操作、通知、資料路徑與疑難排解 | 穩定架構總論、目前進度、工具全索引 |
| `docs/ARCHITECTURE.md` | 穩定架構事實、正式主路徑、模組邊界、不可回退產品語義、deferred 邊界 | 短期進度、最近驗證、逐次任務紀錄 |
| `docs/TASK_BREAKDOWN.md` | 活狀態、下一步、風險、最近驗證 | 長篇推導、已完成批次細節、架構總論、穩定操作說明 |
| `docs/HANDOFF.md` | 新對話或下一位 agent 接手的最小摘要 | README/tooling 可查的完整命令、ARCHITECTURE 可查的完整架構 |
| `docs/tooling.md` | scripts / CLI 工具角色、路徑、正式入口判定與常用命令 | 產品語義、功能進度 |
| `docs/REFERENCE_MAP.md` | `reference/` 內 JS userscript 參考資料索引 | Python 架構進度、任務狀態 |
| `docs/UPDATER_PLAN.md` | Windows EXE 更新功能開發期間的暫時計畫；功能封口後刪除或歸檔 | 已封口的穩定使用說明、逐次驗證紀錄 |
| `packaging/README.md` | EXE 打包前置、PyInstaller 與 frozen smoke checklist | source-mode 日常操作、目前進度 |

## 歷史文件

- `docs/archive/`：已完成計畫、review、spike 與長篇推導。這些文件可查背景，但不作為目前狀態來源。

## 更新規則

- 目前進度、下一步、風險或最近驗證：只更新 `docs/TASK_BREAKDOWN.md`，保持摘要層級。
- 正式主路徑、資料語義、模組邊界或 deferred 邊界：更新 `docs/ARCHITECTURE.md`，必要時同步 `AGENTS.md`。
- 安裝與日常操作：更新 README 的摘要與 `docs/USAGE.md` 的詳細步驟。
- scripts / CLI 搬移或新增：更新 `docs/tooling.md`。
- JS 參考來源或重要語義對照：更新 `docs/REFERENCE_MAP.md`。
- Windows EXE updater 尚未封口前的階段規劃、安全邊界與替代方案：更新 `docs/UPDATER_PLAN.md`。
- release 驗證流程、scripts 指令或 sidebar 瀏覽器層手動 QA：更新 `docs/tooling.md`。
- EXE 打包、frozen app smoke 或 distribution 前置：更新 `packaging/README.md`。
- secret 保存欄位、安全邊界或 key 行為：更新 `docs/ARCHITECTURE.md#notification-與-secret`。
- 長篇計畫、逐次驗證與歷史推導：完成後放入 `docs/archive/` 或交給 git history；`TASK_BREAKDOWN` 只留結論。
