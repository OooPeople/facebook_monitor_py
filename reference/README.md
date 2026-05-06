# Facebook Group Refresh Monitor

用於 Facebook 社團頁面的 Tampermonkey userscript。

它的目標很單純：在你已登入 Facebook 的瀏覽器裡，保守地監看目前社團的新貼文，或單篇貼文頁中的留言，套用包含 / 排除關鍵字規則，並在找到符合條件的新項目時送出通知。

這個專案刻意不走大量爬取或背景服務，而是優先使用瀏覽器內 userscript、既有登入 session，以及可診斷的本地 debug 面板。

## 功能概要

- 監看 `https://www.facebook.com/groups/*` 社團頁與單篇貼文頁
- 支援包含關鍵字與排除關鍵字
- 支援 `;` 作為 OR、空格作為 AND
- 支援桌面通知、`ntfy` 與 Discord Webhook，三種通道可在設定中獨立勾選
- 社團貼文模式支援保守 refresh 與自動載入更多貼文
- 單篇貼文留言模式支援保守 refresh 與 scroll-only 自動載入更多留言
- 開始監控後可自動嘗試切到目前模式偏好的最新排序：社團貼文為 `新貼文`，單篇貼文留言為 `由新到舊`
- 例行掃描支援最上方項目快篩；最上方貼文或留言未變時可沿用上一輪完整掃描快取
- 支援 scan item 去重、通知紀錄與 debug 面板
- debug 面板會顯示收集策略、排序調整結果、快篩狀態、抽取摘要與通知狀態
- 關鍵字、通知設定與刷新設定依社團 ID 保存；baseline / seen 依 scan target scope 保存
- 可同時開啟多個不同社團視窗；同社團不同單篇貼文留言視窗共用設定，但 seen baseline 分開

## 快速開始

### 1. 安裝 Tampermonkey

先在瀏覽器安裝 [Tampermonkey](https://chromewebstore.google.com/detail/dhdgffkkebhmkfjojejmpbldmpobfkfo?utm_source=item-share-cb)。

安裝後，建議把它釘選到瀏覽器工具列，之後比較容易確認腳本是否啟用。

### 2. 建立 userscript

1. 點開瀏覽器右上角的 Tampermonkey
2. 進入 `控制台`
3. 建立新的腳本
4. 清掉預設內容
5. 將 [`src/facebook_group_refresh.user.js`](./src/facebook_group_refresh.user.js) 全文貼上
6. 儲存腳本

### 3. 進入 Facebook 社團頁

此腳本只會在下列網址格式啟用：

```text
https://www.facebook.com/groups/<group-id>/
https://www.facebook.com/groups/<group-id>/posts/<post-id>
https://www.facebook.com/groups/<group-id>/permalink/<post-id>
```

進入社團頁後，建議手動重新整理一次，讓腳本完整初始化。

如果正常啟用，右上角會出現控制面板。

### 4. 開始使用

1. 輸入包含關鍵字與排除關鍵字
2. 按 `儲存`
3. 按 `開始`

如果你想看完整操作與通知設定，請再讀：

- [`docs/USAGE.md`](./docs/USAGE.md)

## 詳細使用說明

README 只保留快速導覽。完整操作請看：

- [`docs/USAGE.md`](./docs/USAGE.md)

內容包含：

- 主面板按鈕與設定說明
- 關鍵字規則與範例
- 自動載入更多項目、自動排序與每次目標掃描項目數
- 通知通道勾選、`ntfy` 設定步驟與 Discord Webhook 設定步驟
- debug 面板可診斷的欄位
- 通知、scan target 與去重邏輯
- 常見不通知原因與使用注意事項

## 專案結構

```text
facebook_group_refresh/
├─ src/
│  └─ facebook_group_refresh.user.js
├─ scripts/
│  └─ smoke_check_userscript.js
├─ docs/
│  ├─ USAGE.md
│  ├─ ARCHITECTURE_PLAN.md
│  ├─ TASK_BREAKDOWN.md
│  ├─ HANDOFF_PLAN.md
│  ├─ SCRIPT_TEMPLATE_GUIDE.md
│  └─ archive/
│     ├─ V1_SPEC.md
│     ├─ REFACTOR_PLAN.md
│     └─ STATE_REFACTOR_PLAN.md
├─ AGENTS.md
├─ GIT_COMMIT_RULES.md
├─ .editorconfig
├─ .gitignore
└─ README.md
```

### 你實際只需要的檔案

如果你只是要安裝使用，實際上只需要下面這個檔案：

- [`src/facebook_group_refresh.user.js`](./src/facebook_group_refresh.user.js)
  這是唯一需要貼進 Tampermonkey 的腳本主程式。一般使用者只要這個檔案就能運行。

### 其他檔案都是開發 / 測試 / 文件用途

- [`scripts/smoke_check_userscript.js`](./scripts/smoke_check_userscript.js)
  Node smoke test，用來檢查 userscript metadata 與穩定純邏輯 helper。
- [`docs/ARCHITECTURE_PLAN.md`](./docs/ARCHITECTURE_PLAN.md)
  目前架構、runtime 邊界、掃描流程與後續變更邊界。
- [`docs/TASK_BREAKDOWN.md`](./docs/TASK_BREAKDOWN.md)
  後續任務拆解、任務分類、手動驗證與完成定義。
- [`docs/HANDOFF_PLAN.md`](./docs/HANDOFF_PLAN.md)
  任務交接文件；目前保留空白，等下一個具體任務再填。
- [`docs/SCRIPT_TEMPLATE_GUIDE.md`](./docs/SCRIPT_TEMPLATE_GUIDE.md)
  說明如何把這份腳本作為其他單站監視腳本模板。
- [`docs/archive/`](./docs/archive/)
  已完成的歷史規格與重構紀錄。
- [`AGENTS.md`](./AGENTS.md)
  這個 repo 的 agent / AI 協作規則。
- [`GIT_COMMIT_RULES.md`](./GIT_COMMIT_RULES.md)
  Git commit message 規範。
- [`.editorconfig`](./.editorconfig)
  編輯器格式設定。
- [`.gitignore`](./.gitignore)
  Git 忽略規則。

## 目前行為與限制

- 只支援已登入 Facebook 的瀏覽器環境
- 只在 `www.facebook.com/groups/*` 啟用
- 以保守 refresh、溫和捲動與最小頁面互動為原則
- 不處理登入、自動留言、按讚、發文、加入社團或任何互動
- 留言模式只做目前 DOM 與 scroll-only 載入更多，不主動點擊「查看更多留言」或「查看先前留言」
- 支援多個不同社團同時監視，但社團貼文 feed 建議固定為一個視窗只跑一個社團
- 同一社團的多個單篇貼文留言視窗會共用社團設定，但各自使用不同 seen baseline
- 不要同時開兩個以上視窗監視同一個社團貼文 feed
- Facebook DOM 與貼文型態會變動，少數貼文可能仍抓不到穩定 permalink 或 `postId`
- `timestampText` / `timestampEpoch` 目前保留欄位形狀，但暫不做貼文時間解析

## 隱私與安全

- 腳本在本地瀏覽器執行
- 關鍵字、設定、去重資料與通知紀錄保存在本機
- 若有勾選 `ntfy` 或 Discord Webhook 並填入端點，通知內容只會送往你自行設定的端點

## 開發驗證

repo 內提供最小 smoke test：

```powershell
node .\scripts\smoke_check_userscript.js
```

目前 smoke test 已涵蓋：

- userscript metadata 與 test mode 載入
- text normalization / keyword matcher
- config normalization 與 refresh payload builder
- permalink canonicalization / postId extraction
- scan item dedupe / seen-stop / history merge
- comment target、comment sort、observer root 與 mutation relevance policy helper
- notification formatting
- top-item shortcut eligibility
- panel drag / position helper
- scan / notification runtime 的純邏輯 helper

## 相關文件

- [`docs/USAGE.md`](./docs/USAGE.md)
- [`docs/ARCHITECTURE_PLAN.md`](./docs/ARCHITECTURE_PLAN.md)
- [`docs/TASK_BREAKDOWN.md`](./docs/TASK_BREAKDOWN.md)
- [`docs/HANDOFF_PLAN.md`](./docs/HANDOFF_PLAN.md)
- [`docs/SCRIPT_TEMPLATE_GUIDE.md`](./docs/SCRIPT_TEMPLATE_GUIDE.md)
- [`docs/archive/V1_SPEC.md`](./docs/archive/V1_SPEC.md)
- [`docs/archive/REFACTOR_PLAN.md`](./docs/archive/REFACTOR_PLAN.md)
- [`docs/archive/STATE_REFACTOR_PLAN.md`](./docs/archive/STATE_REFACTOR_PLAN.md)
