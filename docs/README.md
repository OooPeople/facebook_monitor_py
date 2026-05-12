# 文件索引

本目錄文件分成「協作規則、專案首頁、操作指南、穩定架構、目前狀態、交接摘要、工具索引、參考對照、歷史紀錄」。若資訊看起來重複，以本頁的職責邊界判斷應更新哪一份。

## 主文件

| 文件 | 職責 | 不應放 |
|---|---|---|
| `AGENTS.md` | 協作規則、禁止事項、JS 移植守則與 UI 重構邊界 | 目前進度、驗證結果 |
| `README.md` | GitHub 專案首頁：專案用途、核心能力、架構亮點、文件入口 | 詳細操作步驟、scripts 全索引、歷史批次 |
| `docs/USAGE.md` | 詳細安裝、啟動、target 操作、通知、資料路徑與疑難排解 | 穩定架構總論、目前進度、工具全索引 |
| `docs/ARCHITECTURE.md` | 穩定架構事實、正式主路徑、模組邊界、不可回退產品語義、deferred 邊界 | 短期進度、最近驗證、逐次任務紀錄 |
| `docs/TASK_BREAKDOWN.md` | 活狀態、近期完成摘要、下一步、風險、最近驗證 | 長篇推導、已完成批次細節、架構總論 |
| `docs/HANDOFF.md` | 新對話或下一位 agent 接手的最小摘要 | README/tooling 可查的完整命令、ARCHITECTURE 可查的完整架構 |
| `docs/tooling.md` | scripts / CLI 工具角色、路徑、正式入口判定與常用命令 | 產品語義、功能進度 |
| `docs/REFERENCE_MAP.md` | `reference/` 內 JS userscript 參考資料索引 | Python 架構進度、任務狀態 |
| `docs/SECRET_STORAGE.md` | notification secret 加密保存語義、key 位置與安全邊界 | 一般操作步驟、目前進度 |

## 歷史文件

- `docs/archive/`：已完成計畫、review、spike 與長篇推導。這些文件可查背景，但不作為目前狀態來源。
- `docs/ui_refactor/reference_ui.html`：dashboard 視覺參考。它是設計參考，不是可直接覆蓋目前專案的 template。

## 更新規則

- 修改目前進度、近期摘要、下一步或最近驗證：更新 `docs/TASK_BREAKDOWN.md`，保持摘要層級。
- 改變正式主路徑、資料語義、模組邊界或 deferred 邊界：更新 `docs/ARCHITECTURE.md`，必要時同步 `AGENTS.md`。
- 新增 / 搬移 scripts：更新 `docs/tooling.md`。
- 改變 local app 啟動方式：更新 README、`docs/USAGE.md` 與 `docs/tooling.md`；若改變穩定路徑語義，另更新 `docs/ARCHITECTURE.md`。
- 改變打包前置策略或下一步：更新 `docs/TASK_BREAKDOWN.md`；長篇推導完成後移到 `docs/archive/`。
- 新增 JS 參考或完成重要語義對照：更新 `docs/REFERENCE_MAP.md`。
- 長篇計畫或逐次驗證完成後：保留結論於 `docs/TASK_BREAKDOWN.md`，把需要追溯的細節放入 `docs/archive/` 或交給 git history。
- 若同一段資訊在三份以上文件重複，優先保留單一來源，其他文件改成短句加連結。
