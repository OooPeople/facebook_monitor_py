# AGENTS.md

本檔為本專案的人類／Codex／其他代理協作規則。所有實作、重構、搬遷與 review，皆應遵守本檔。

---

## 專案核心原則

本專案是把成熟的 Facebook userscript 行為，逐步搬遷成 Python + Playwright 的可維護版本。  
**JS 版本是功能語義來源，不是逐行翻譯來源。**

這代表：

- 不可機械式逐行翻譯 userscript。
- 也不可只抄一半成熟邏輯，留下只有外殼、沒有完整語義的 Python 實作。
- Python 版允許重新分層、重新命名、重新安排模組，但**功能語義、邊界、狀態模型、資料模型、UX 主語義**都必須對齊 JS 成熟版本。
- 若 Python 版刻意與 JS 版不同，必須在變更說明中明講：
  - 為何不同
  - 與 JS 版差在哪
  - 風險是什麼
  - 是否為暫時性實作

---

## 絕對禁止事項

- 不可 commit 真實 browser profile、cookies、tokens、session dumps，或包含私人資料的 logs。
- 不可使用使用者日常 Chrome profile。
- 不可把 profile 放到 runtime path resolver 管理的 `<data-dir>/profiles/` 以外。
- 不可把 runtime logs 放到 runtime path resolver 管理的 `<logs-dir>` 以外。
- 若已有成熟的第三方依賴可降低實作與維護風險，應積極尋找並優先評估可靠、社群仍持續維護且廣泛使用的第三方依賴；但新增任何既有最小依賴以外的第三方依賴前，依然必須先詢問。
- 不要機械式逐行翻譯 userscript；只能把它當作行為參考。
- 不可直接改寫 JS 版成熟常數、字串 label、判斷條件，只因為 Python 版「目前看起來也能動」。
- 不可在未對照 JS 成熟行為前，自行發明替代邏輯來填空。
- 不可只移植 feature 的 UI、設定欄位、資料欄位或函式名稱，卻不移植完整的行為鏈。
- 不可把 scheduler、target enable/disable、paused、baseline、seen、notification channel 等核心語義做成與 JS 版相衝突、且未明講的 UX。
- 不可因為目前功能未完成，就在 review 或 handoff 中把「暫時殼」描述成已完整移植。

---

## 工作規則

- 需要新增 probe 或工具時，優先寫小而可測的 scripts。
- 本專案使用 `uv` 管理環境；PowerShell 指令優先走 `.\scripts\uv.ps1`。
- 每次打包 Windows portable zip 時，必須同時產生同名 `.sha256` 檔，並確認 zip 檔名、GitHub tag、`APP_VERSION`、PyInstaller version resource 與 SHA256 檔內容互相對齊。
- 正式日常入口是 package entrypoint：`facebook-monitor`；profile 登入 / 檢查入口是 `facebook-monitor-login`。
- scripts 已依角色分層：低頻管理在 `scripts/admin/`，除錯工具在 `scripts/debug/`，內部工具在 `scripts/internal/`。
- 不得新增新的 `phase_*` script；檔名必須反映角色與用途。
- 不得把 debug / internal 工具描述成正式日常入口；新功能預設先接 Web UI + async resident 正式主路徑。
- 每次 probe 失敗都要留下清楚分類：login/session、headless DOM、page load、selector/extractor、notification 或 unknown。
- headless 失敗時，先測 persistent-context 行為，再評估 headed compatibility mode。
- 不要提前建立正式 DB / repository / UI 架構。
- 新增或修改模組、類別、函式時，補繁體中文 docstring 或必要註解，說明職責即可，避免逐行解說。
- 讀取或修改 `.md` 時使用 UTF-8。
- 若問題長時間無法收斂，停止盲試，改查官方資料、外部資料或先回報阻塞點。
- 正式 config store 是 `target_configs[target_id]`；`group_configs` 只保留為舊資料 migration 來源，不得作為正式 read/write path。
- 新增正式 target 建立流程時，不得使用 internal `_create_*` helper；正式入口一律走 `upsert_*`。
- Python 版刻意偏離 JS 的預設值必須集中於 `src/facebook_monitor/core/defaults.py`，不得在 Web UI、service 或 worker 另寫一套常數。
- UI 重構時不得順手修改 worker scan pipeline、notification outbox、scheduler runtime、persistence migration 或 Facebook DOM helper；若 UI 需要新資料，優先走 read model / presenter。

---

## 重要檔案

- `.python-version`：固定 uv / Python 工具優先使用 Python 3.13。
- `scripts/uv.ps1`：專案限定 uv wrapper。
- `pyproject.toml`：定義 `facebook-monitor` 與 `facebook-monitor-login` package entrypoints。
- `src/facebook_monitor/launcher.py`：正式 Web UI launcher。
- `src/facebook_monitor/profile_setup.py`：正式 profile 登入 / 檢查入口。
- `docs/tooling.md`：scripts / CLI 工具角色索引。

---

## 協作與 Commit

- 若問題長時間無法收斂，停止盲試，改查官方資料、外部資料或先回報阻塞點。
- 更新進度時，同步維護 `docs/TASK_BREAKDOWN.md`。
- `docs/TASK_BREAKDOWN.md` 只保留活狀態、下一步、風險與最近驗證摘要；不要累積逐次 focused command 或歷史 passed 數。
- 使用者說「整理文件」時，意思是刪掉太細節、太瑣碎、容易過期的內容，檢查文件職責是否重疊、邊界是否模糊，並把穩定事實放回正確文件；不是把近期改動補寫到每一份文件。
- 使用者要求 commit message 時，先遵守 `GIT_COMMIT_RULES.md`。
- 若本次實作只完成部分語義，commit / handoff / review 中必須清楚寫「已完成」與「未完成」邊界，不得混寫。

---

## JS 成熟邏輯移植規則（最重要）

### 1. JS 版是唯一功能語義基準
遇到以下任何功能，**一律先對照 JS 版成熟實作**，再決定 Python 寫法：

- target-scoped config
- target-scoped baseline / seen
- posts/comments scan target
- include/exclude matcher
- notification channels
- latest scan / latest notification model
- auto adjust sort
- auto load more
- permalink / canonical URL / postId / commentId 抽取
- comment-specific extractor
- observer / mutation relevance
- panel / UI 控制語義

### 2. 禁止只移植 feature 表面
以下情況都視為**不完整移植**，不得宣稱 feature 已完成：

- 只新增設定欄位，但 worker / runtime 不使用
- 只新增按鈕或 API，但沒有接到完整行為
- 只做 `window.scrollBy(...)`，但 JS 版成熟邏輯其實還有 scroll target 選擇、fallback、snapshot/restore
- 只支援單一通知通道，但 JS 版其實是多通道模型
- 只搬 target kind / scopeId 欄位，但後續 seen/history/latest scan 沒跟著分流
- 只抄 selector 或常數的一部分，未搬完整判斷鏈
- 只做 auto-adjust-sort 的表面點擊，但沒有 before/after label、result、reason、suppression 一整條語義鏈

### 3. 「成熟邏輯移植完成」的定義
只有同時滿足下面條件，才可說某功能已移植：

1. **資料模型對齊**  
   Python 版的 config / state / persistence 結構，能承載 JS 同等語義。

2. **行為鏈完整**  
   不是只有入口函式，而是從：
   - 設定
   - runtime
   - 實際執行
   - 結果寫回
   - debug / diagnostic / latest scan
   這整條都接通。

3. **失敗語義完整**  
   要保留 JS 成熟版的重要 failure reason / status / result，而不是失敗時全部只回傳 `False` 或 `None`。

4. **常數與 label 對齊**  
   不得自行改成另一套字串或 enum，除非有明確理由並記錄。

5. **至少有一條驗證證據**  
   要有 probe、log、diagnostic、截圖、或明確測試記錄，證明這條功能不只是「看起來有接上」。

---

## 高風險功能專門規則

### A. auto_adjust_sort
`auto_adjust_sort` 不是單純「去點排序」。  
移植時必須完整對齊 JS 版的成熟語義：

- target kind 決定 preferred sort label
- 正確的 feed/comment sort labels 常數
- 找 control
- 找 option
- before_label / after_label
- attempted / changed / reason
- mutation suppression
- 結果進 latest_scan / diagnostic / debug

如果上述任一段缺失，視為**半移植**，不可宣稱已完成。

### B. auto_load_more
`auto_load_more` 不是單純 `scrollBy`。  
若 JS 版已有成熟的：

- scroll target 選擇
- nested scrollable ancestor 判斷
- scroll fallback
- snapshot / restore
- comments / posts 差異

則 Python 版不得只做最表面的 window scroll，就宣稱功能完成。

### C. notification channels
JS 版若已是多通道模型，Python 版不得只保留其中一個通道，卻把資料欄位／UI 偽裝成多通道已完成。

### D. comments target
comments 不是 posts 換個 selector 而已。  
若 JS 成熟版已有：

- comment-specific scopeId
- comment extractor
- comment permalink / id canonicalization
- comment mutation relevance
- comment-specific sort
- comment-specific cache / latest scan
- comment text cleanup

則 Python 版要逐一對照，不可只搬「能抽到留言文字」就算完成。

---

## UI / UX 語義保護規則

### 1. 單一主開關語義
本專案 Web UI 的**日常使用主開關**，應以 target 卡片的開始 / 停止為準。  
scheduler 不應再以「啟動自動掃描 / 停止自動掃描」形式暴露給一般使用者，避免產生雙主開關語義。

### 2. scheduler 行為
scheduler 應視為 Web UI 啟動後的內部背景服務：

- 預設跟著 Web UI 啟動
- 不作為日常使用者主要操作開關
- 不可讓使用者誤解為：target 已啟用，但還需要再開另一個「真正的總開關」

### 3. 正式主路徑
async resident worker 是唯一正式產品主路徑。one-shot mode 與 sync resident worker 只作 fallback / debug tooling。

- 新功能預設只要求 async resident 完整接上。
- 不得為了「看起來支援」而在 fallback/debug path 補半套 parity。
- 若使用者或 review 明確要求 fallback/debug path parity，必須作為獨立完整任務處理並寫明範圍。

### 4. 若 UI 暫時與 JS 版不同
若因為過渡期需要保留不同 UX，必須在 review / handoff 中明講：

- 差異點
- 暫時原因
- 最終要回到哪個語義

### 5. UI 重構邊界
進入 UI 重構時，改動範圍應限制在：

- `src/facebook_monitor/webapp/routes/*`
- `src/facebook_monitor/webapp/templates/*`
- `src/facebook_monitor/webapp/static/*`
- `src/facebook_monitor/webapp/query_service.py`
- `src/facebook_monitor/webapp/*_presenter.py`
- 必要的 `form_models` / `schemas`
- 必要的 application command DTO

UI 重構不得順手重寫下列核心線：

- `src/facebook_monitor/worker/scan_finalize.py`
- `src/facebook_monitor/worker/posts_pipeline.py`
- `src/facebook_monitor/worker/comments_pipeline.py`
- `src/facebook_monitor/worker/resident_main*`
- `src/facebook_monitor/notifications/outbox_service.py`
- `src/facebook_monitor/persistence/repositories/notification_outbox.py`
- `src/facebook_monitor/facebook/feed_dom.py`
- `src/facebook_monitor/facebook/comment_dom.py`
- scheduler runtime / queue / recovery

若 UI 需求看似需要修改上述核心線，必須先把原因、風險與替代方案講清楚，再取得使用者確認。

UI 重構不得讓已封口的架構邊界回歸：

- Web UI 不得重新暴露 one-shot mode。
- 不得新增全域 scheduler 日常主開關。
- 不得新增 direct notification dispatch path。
- 不得把 failed outbox retry 接回一般 scan commit。
- 不得把 `group_configs` 重新變成正式設定來源。

### 6. UI 設計參考檔狀態
舊版 `docs/ui_refactor/reference_ui.html` 已移除，不再作為 dashboard 視覺參考。

後續 dashboard UI 調整必須以目前 FastAPI + Jinja template + vanilla CSS/JS 實作為準，並保留既有 endpoint、Jinja partial、partial update、card collapse、hit records modal、sidebar state 與 `data-*` 互動契約。

---

## Review / Handoff 規則

每次完成一段功能後，review / handoff 內容必須包含：

1. **對照了哪個 JS 模組或函式**
2. **哪些語義已完整搬過去**
3. **哪些還沒搬完**
4. **目前是完整移植、部分移植、還是只有殼**
5. **若有刻意偏離 JS，原因是什麼**

禁止出現這種模糊描述：

- 「已支援 auto load more」
- 「已完成 notification」
- 「已完成 comments」

除非真的已滿足本檔前述的「成熟邏輯移植完成」定義。

---

## 模組設計偏好

Python 版優先維持這種方向：

- `domain/`：純資料模型、規則、常數、matcher
- `application/`：scan orchestration、state transition、use cases
- `infrastructure/`：Playwright、storage、notifications、logging
- `ui/`：Web UI / API / 設定頁
- `scripts/`：probe、migration、manual tools

但這只是分層偏好。  
**真正優先順序永遠是：語義完整 > 結構漂亮。**

---

## 初期專案優先順序

若功能尚未完整，實作優先順序應遵守：

1. 先讓 posts target 的語義完整
2. 再補 auto_adjust_sort / auto_load_more 這種目前已露出的半移植功能
3. 再補 notification 多通道完整化
4. 維持 comments target end-to-end 行為，不因後續修改退化
5. 最後才補 polish / UI 美化 / 次要便利功能

---

## 日誌與診斷規則

對於任何「看起來有做，但實測沒成功」的功能，必須把結果放入可檢查的診斷輸出，例如：

- latest_scan metadata
- worker log
- structured diagnostic JSON
- panel debug section

至少要能看到：

- attempted
- changed
- before / after
- reason
- round count / candidate count / target count
- stop reason
- worker name

若沒有這些資訊，後續 debug 成本會非常高。

---

## 遇到不確定時的處理方式

若實作者不確定 Python 版目前應該照哪個 JS 邏輯：

1. 停止自行猜測
2. 先找 JS 最終成熟版對應函式
3. 確認資料模型、常數、結果模型、失敗語義
4. 再實作 Python 版
5. 在變更說明中寫明對照點

**禁止在不確定時自行補一個「大概差不多」的版本。**

---

## 最後原則

本專案最大的風險不是「程式不能跑」，而是：

**功能表面看起來有了，但只移植了半套成熟邏輯。**

因此所有代理在本專案中的第一優先事項是：

> **避免半移植。**
>
> 寧可明確標示「尚未完成」，也不要把半完成版本寫成已完成。
