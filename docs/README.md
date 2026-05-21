# 文件索引

本頁定義公開文件職責邊界。若同一資訊需要在多處出現，原則是「一份主來源 + 其他文件短句連結」，避免 README、USAGE、ARCHITECTURE 彼此累積重複細節。

## 主文件

| 文件 | 職責 | 不應放 |
|---|---|---|
| `AGENTS.md` | 代理開工守則、禁止事項、文件索引、每次必看的高風險 guardrails | 目前進度、驗證結果、完整產品規格、完整審查清單 |
| `README.md` | GitHub 專案首頁：專案用途、核心能力、架構亮點、文件入口 | 詳細操作步驟、scripts 全索引、歷史批次 |
| `docs/README.md` | 公開文件職責邊界與更新規則 | 產品功能規格、逐次任務紀錄、最近驗證 |
| `docs/USAGE.md` | 詳細安裝、啟動、target 操作、通知、資料路徑與疑難排解 | 穩定架構總論、目前進度、工具全索引 |
| `docs/ARCHITECTURE.md` | 穩定架構事實、正式主路徑、模組邊界、不可回退產品語義、deferred 邊界 | 短期進度、最近驗證、逐次任務紀錄 |
| `docs/ENGINEERING_REVIEW.md` | 預設工程審查清單、review 輸出格式與 handoff 要求 | 產品功能規格、短期進度、最近驗證 |
| `docs/tooling.md` | scripts / CLI 工具角色、路徑、正式入口判定與常用命令 | 產品語義、功能進度 |
| `packaging/README.md` | EXE 打包前置、PyInstaller 與 frozen smoke checklist | source-mode 日常操作、目前進度 |
| `packaging/MACOS_HANDOFF.md` | macOS Apple Silicon 打包接手摘要、近期驗證與後續邊界 | 詳細打包指令、穩定使用者操作、Windows 打包規則 |

## 本機協作文件

- `docs/local/`：本機進度、交接摘要、長篇計畫與歷史推導，已由 `.gitignore` 排除，不上傳 GitHub。
- `docs/local/TASK_BREAKDOWN.md`：若存在，作為本機活狀態、下一步、風險與最近驗證的主來源。
- `docs/local/HANDOFF.md`：若存在，作為新對話或下一位 agent 接手的本機摘要。
- `docs/local/archive/`：若存在，保存歷史計畫、spike 與長篇推導，不作為目前狀態來源。

## 更新規則

- 目前進度、下一步、風險或最近驗證：若本機有 `docs/local/TASK_BREAKDOWN.md`，只更新該文件並保持摘要層級；不要新增公開追蹤的進度文件。
- 正式主路徑、資料語義、模組邊界或 deferred 邊界：更新 `docs/ARCHITECTURE.md`，必要時只在 `AGENTS.md` 保留短 guardrail 或文件索引。
- 工程審查範圍、review 輸出格式或 handoff 要求：更新 `docs/ENGINEERING_REVIEW.md`，必要時只在 `AGENTS.md` 保留短索引。
- 文件職責邊界或文件索引：更新本文件，並只在 README / AGENTS 保留必要入口。
- 安裝與日常操作：更新 README 的摘要與 `docs/USAGE.md` 的詳細步驟。
- scripts / CLI 搬移或新增：更新 `docs/tooling.md`。
- 歷史來源說明：更新 README 的「歷史來源」段落，連到外部 JS repo。
- frozen updater 已落地的架構邊界：更新 `docs/ARCHITECTURE.md#frozen-updater`。
- frozen updater 使用者操作：更新 `docs/USAGE.md#程式更新`。
- frozen updater release artifact、frozen smoke 與打包規則：更新 `packaging/README.md` 與 `docs/tooling.md`。
- release 驗證流程、scripts 指令或 sidebar 瀏覽器層手動 QA：更新 `docs/tooling.md`。
- EXE / macOS onedir 打包、frozen app smoke 或 distribution 前置：更新 `packaging/README.md`。
- secret 保存欄位、安全邊界或 key 行為：更新 `docs/ARCHITECTURE.md#notification-與-secret`。
- 長篇計畫、逐次驗證與歷史推導：完成後放入本機 `docs/local/archive/` 或交給 git history；本機 `TASK_BREAKDOWN` 只留結論。
