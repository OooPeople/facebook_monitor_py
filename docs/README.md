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
| `docs/WEB_UI_CONTRACT.md` | Web UI 呈現、互動一致性、sidebar layout 與 route / presenter 邊界 | target state、scheduler、worker、notification、dedupe 或 persistence 語義 |
| `docs/frontend-vendor.md` | Web UI vendored frontend files 的來源、版本、license、manifest checksum 主來源與更新流程 | Web UI 呈現契約、產品語義、npm 管線設計 |
| `docs/ENGINEERING_REVIEW.md` | 預設工程審查清單、review 輸出格式與 handoff 要求 | 產品功能規格、短期進度、最近驗證 |
| `docs/tooling.md` | scripts / CLI 工具角色、路徑、正式入口判定與常用命令 | 產品語義、release artifact 細節、功能進度 |
| `packaging/README.md` | Windows / macOS 打包、release artifact、manifest、frozen smoke checklist | source-mode 日常操作、目前進度 |

## 本機協作文件

- `docs/local/`：本機進度、交接摘要、長篇計畫與歷史推導，已由 `.gitignore` 排除，不上傳 GitHub。
- `docs/local/TASK_BREAKDOWN.md`：若存在，作為本機活狀態、下一步、風險與最近驗證的主來源。
- `docs/local/HANDOFF.md`：若存在，作為新對話或下一位 agent 接手的本機摘要。
- `docs/local/archive/`：若存在，保存歷史計畫、spike 與長篇推導，不作為目前狀態來源。
- root 底下 `review*.md`：若存在，視為本機 ignored 的審查工作筆記；可保留原始 review 內容，但最新狀態必須同步摘要到本機活狀態或文件表格，避免歷史建議被誤用為目前待辦。

## 更新規則

- 目前進度、下一步、風險或最近驗證：若本機有 `docs/local/TASK_BREAKDOWN.md`，只更新該文件並保持摘要層級；不要新增公開追蹤的進度文件。
- 本機 review 檔整理：先在檔案頂部維護最新狀態表或結論；下方原始 review 若保留，必須明確標成歷史參考，並讓 `TASK_BREAKDOWN.md` 反映真正下一步。
- 正式主路徑、資料語義、模組邊界或 deferred 邊界：更新 `docs/ARCHITECTURE.md`，必要時只在 `AGENTS.md` 保留短 guardrail 或文件索引。
- Web UI 呈現、互動一致性、sidebar layout 或 route / presenter 邊界：更新 `docs/WEB_UI_CONTRACT.md`；若會影響 target state、scheduler、notification、dedupe 或 persistence，主語義仍要更新 `docs/ARCHITECTURE.md`。
- Web UI vendored frontend file 來源、版本、license 或 checksum：更新 `src/facebook_monitor/webapp/static/vendor/frontend-vendor.manifest.json` 與 `docs/frontend-vendor.md`。
- 工程審查範圍、review 輸出格式或 handoff 要求：更新 `docs/ENGINEERING_REVIEW.md`，必要時只在 `AGENTS.md` 保留短索引。
- 文件職責邊界或文件索引：更新本文件，並只在 README / AGENTS 保留必要入口。
- 安裝與日常操作：更新 README 的摘要與 `docs/USAGE.md` 的詳細步驟。
- scripts / CLI 搬移或新增：更新 `docs/tooling.md`。
- 歷史來源說明：更新 README 的「歷史來源」段落，連到外部 JS repo。
- updater / release 的產品語義與不可回退邊界：更新 `docs/ARCHITECTURE.md#frozen-updater`。
- updater 的使用者操作與疑難排解：更新 `docs/USAGE.md#程式更新`。
- 打包、platform zip、manifest、artifact validation、frozen app smoke 或 distribution 前置：更新 `packaging/README.md`。
- scripts 角色、release validation 指令入口或 sidebar 瀏覽器層手動 QA：更新 `docs/tooling.md`，artifact 細節連回 `packaging/README.md`。
- secret 保存欄位、安全邊界或 key 行為：更新 `docs/ARCHITECTURE.md#notification-與-secret`。
- 長篇計畫、逐次驗證與歷史推導：完成後放入本機 `docs/local/archive/` 或交給 git history；本機 `TASK_BREAKDOWN` 只留結論。
