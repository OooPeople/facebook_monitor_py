# AGENTS.md

本檔是本專案的人類 / Codex / 其他代理開工守則。它只放每次協作都需要先看到的規則與文件索引；穩定產品語義、操作步驟、工具細節與審查清單應放在 `docs/` 或 `packaging/` 內，避免 AGENTS 變成第二份規格書。

---

## 核心原則

本專案是 Python + Playwright + FastAPI + SQLite 的本機 Facebook 監視工具。**Python 版是目前正式維護主體**，新功能與修正優先沿用本 repo 既有 domain / application / worker / Web UI 契約。

- 原始 userscript repo 只作為歷史背景與必要時的行為追溯來源：<https://github.com/OooPeople/facebook_group_refresh>。
- 不可只新增 UI、設定欄位、資料欄位或函式名稱，卻沒有接通 runtime 行為、結果寫回、diagnostics 與測試。
- 若刻意改變既有 Python 產品語義，必須在變更說明中明講原因、使用者或資料語義差異、風險，以及是否為暫時性實作。
- 真正優先順序永遠是：**語義完整 > 結構漂亮**。

---

## 絕對禁止事項

- 不可 commit 真實 browser profile、cookies、tokens、session dumps，或包含私人資料的 logs。
- 不可使用使用者日常 Chrome / Edge / Chromium profile。
- 不可把 profile 放到 runtime path resolver 管理的 `<data-dir>/profiles/` 以外。
- 不可把 runtime logs 放到 runtime path resolver 管理的 `<logs-dir>` 以外。
- 不可把 scheduler、target enable/disable、paused、baseline、seen、notification channel 等核心語義做成與目前 Python 產品語義相衝突、且未明講的 UX。
- 不可因為目前功能未完成，就在 review、handoff 或 commit 說明中把「暫時殼」描述成已完整完成。
- 若已有成熟第三方依賴可降低實作與維護風險，應先評估；但新增任何既有最小依賴以外的第三方依賴前，必須先詢問。

---

## 文件查找規則

- 穩定架構、產品語義、正式入口與不可回退邊界：`docs/ARCHITECTURE.md`
- Web UI 呈現、互動一致性與 sidebar layout 邊界：`docs/WEB_UI_CONTRACT.md`
- 使用者操作與疑難排解：`README.md`、`docs/USAGE.md`
- scripts / CLI 工具角色與常用指令：`docs/tooling.md`
- 打包、release zip、PyInstaller 與 frozen smoke：`packaging/README.md`
- 審查清單與 review 輸出規則：`docs/ENGINEERING_REVIEW.md`
- 活進度、下一步、風險與最近驗證摘要：本機 ignored 的 `docs/local/TASK_BREAKDOWN.md`
- 交接摘要：本機 ignored 的 `docs/local/HANDOFF.md`
- 長篇計畫、spike、逐次驗證與歷史推導：本機 ignored 的 `docs/local/archive/`

使用者說「整理文件」時，意思是刪掉太細節、太瑣碎、容易過期的內容，檢查文件職責是否重疊、邊界是否模糊，並把穩定事實放回正確文件；不是把近期改動補寫到每一份文件。

---

## 工作規則

- 本專案使用 `uv` 管理環境；PowerShell 指令優先走 `.\scripts\uv.ps1`。
- 驗證要分清楚「快速 / 聚焦檢查」、「本機上傳前完整檢查」、「release artifact 檢查」與「GitHub CI」。一般開發、文件整理與窄範圍修正預設跑快速 / 聚焦檢查；若改到 DB / migration、scheduler、worker、release/update、dependency、Web middleware 或其他高風險邊界，需自行升級到相對應的測試切片、ruff、mypy 或 audit。快速檢查回報時必須明講「只跑快速/局部檢查，不代表上傳完整驗證」。使用者提到「上傳」、「CI」、「GitHub checks」、「完整」、「所有上傳會跑的測試」時，必須先對照 `.github/workflows/ci.yml` 與 `docs/tooling.md#驗證分級與回報用語`，再跑對應完整驗證；不得把 target tests 或跳過 audit 的結果說成完整通過。
- 本機上傳前完整檢查入口是 `.\scripts\uv.ps1 run python scripts\admin\release_validation.py --skip-sync`（環境已同步時）；若 dependency、`uv.lock`、workflow 或驗證腳本本身有變更，改跑不帶 `--skip-sync` 的 release validation 或先執行 locked sync。`--skip-audit`、`--skip-release-validation`、`--skip-artifact-manifest` 只允許離線、臨時快速檢查或 pre-finalize build 階段使用；回報必須列出實際 command 與 skip flags，不得用來回覆上傳/CI 是否會過。
- 讀取或修改 `.md` 時使用 UTF-8。
- 需要新增 probe 或工具時，優先寫小而可測的 scripts。
- 不得新增新的 `phase_*` script；檔名必須反映角色與用途。
- 不得把 debug / internal 工具描述成正式日常入口；新功能預設先接 Web UI + async resident 正式主路徑。
- 每次 probe 失敗都要留下清楚分類：login/session、headless DOM、page load、selector/extractor、notification 或 unknown。
- headless 失敗時，先測 persistent-context 行為，再評估 headed compatibility mode。
- 不得為 speculative 功能提前建立正式 DB / repository / UI 架構；若需求已確認且需要持久狀態，必須走完整 schema / migration / repository / service / test 鏈。
- 新增或修改模組、類別、函式時，補繁體中文 docstring 或必要註解，說明職責即可，避免逐行解說。
- 測試 macOS / POSIX executable bit 時，不得在 Windows 上無條件 assert `Path.stat().st_mode & 0o111`；單元測試需使用平台 guard / helper，只在支援 POSIX mode 的平台檢查 executable bit。若要在 Windows 驗證 macOS release zip 權限，應檢查 zip metadata 或交由 artifact validation。
- 使用者要求 commit message 時，先遵守 `GIT_COMMIT_RULES.md`。
- 若問題長時間無法收斂，停止盲試，改查官方資料、外部資料或先回報阻塞點。

### 讀取正式 SQLite DB

需要臨時讀取正式 SQLite DB（`<data-dir>\app.db`，例如 `C:\Users\ooo\facebook_monitor_data\app.db`）時，優先用專案 venv Python 直接執行唯讀查詢：

```powershell
& .\.venv\Scripts\python.exe -c $code
```

連線使用：

```python
sqlite3.connect("file:/.../app.db?mode=ro", uri=True, timeout=1)
```

不要用系統 Python / Anaconda，也不要把 heredoc 腳本 pipe 給 `.\scripts\uv.ps1 run python -`，避免 sqlite DLL 或 stdin 等待問題造成排查變慢。

---

## 必守產品邊界

詳細產品語義以 `docs/ARCHITECTURE.md` 為主；AGENTS 只列最容易誤改、需要每次先看到的 guardrails。

- 正式日常入口是 package entrypoint：`facebook-monitor`；profile 登入 / 檢查入口是 `facebook-monitor-login`。
- async resident worker 是唯一正式產品主路徑；one-shot mode 與 sync resident worker 只作 fallback / debug tooling。
- 正式 config store 是 `target_configs[target_id]`；`group_configs` 只保留為舊資料 migration 來源，不得作為正式 read/write path。
- 新增正式 target 建立流程時，不得使用 internal `_create_*` helper；正式入口一律走 `upsert_*`。
- 跨層產品預設值必須集中於 `src/facebook_monitor/core/defaults.py`；模組內部演算法常數可留在該 module，但不得形成跨層重複來源。
- UI 重構不得順手修改 worker scan pipeline、notification outbox、scheduler runtime、persistence migration 或 Facebook DOM helper；若 UI 需要新資料，優先走 read model / presenter。
- Target cover image refresh 的細節以 `docs/ARCHITECTURE.md#target-與-state` 與 `docs/ARCHITECTURE.md#web-ui-語義` 為準。不得未經討論新增主動低頻 refresh，也不得讓 image-only refresh 覆蓋 target 顯示名稱。
- `auto_adjust_sort`、`auto_load_more`、notification channels、comments target 都是高風險語義；修改前先查 `docs/ARCHITECTURE.md#facebook-行為邊界`，不得只補表面入口就宣稱完成。

---

## 功能完成定義

只有同時滿足下面條件，才可說某功能已完成：

1. 資料模型能承載完整產品語義。
2. 設定、runtime、實際執行、結果寫回、debug / diagnostics / latest scan 整條行為鏈已接通。
3. 失敗語義保留可行動的 reason / status / result。
4. 常數、label、enum 與既有契約對齊；若偏離，已說明原因。
5. 至少有一條驗證證據，例如測試、probe、log、diagnostic 或截圖。

禁止用「已支援 auto load more」、「已完成 notification」、「已完成 comments」這類模糊描述包裝半套實作。

---

## Review / Handoff

- 使用者只要說「審查」、「review」、「架構審查」、「幫我看這次變更」或類似要求，除非明確限定只看某一項，預設都要依 `docs/ENGINEERING_REVIEW.md` 做完整工程審查。
- 審查結果必須 findings first，依嚴重度排序；沒有阻塞問題也要明確說明審查面向、可接受的必要分散、剩餘低風險項目與已跑驗證。
- 每次完成一段功能後，handoff 必須包含：對照了哪些模組 / 資料模型 / 測試 / 文件契約、哪些語義已完整接通、哪些還沒完成、目前是完整功能 / 部分功能 / 只有殼，以及是否刻意偏離既有產品語義。
- 若本次實作只完成部分語義，commit / handoff / review 中必須清楚寫「已完成」與「未完成」邊界，不得混寫。

---

## 遇到不確定時

若不確定 Python 版目前應該照哪個產品語義：

1. 停止自行猜測。
2. 先找 Python 版現有 domain / application / worker / Web UI 契約與測試。
3. 確認資料模型、常數、結果模型、失敗語義。
4. 必要時才查外部歷史 repo 追溯設計來源。
5. 在變更說明中寫明對照點。

本專案最大的風險不是「程式不能跑」，而是功能表面看起來有了，但只接了半套產品語義。寧可明確標示「尚未完成」，也不要把半完成版本寫成已完成。
