# 使用說明

本文件說明安裝、啟動與日常操作。架構語義看 `docs/ARCHITECTURE.md`；scripts / CLI 工具索引看 `docs/tooling.md`。

## 需求

- Python 3.13，以 `.python-version` 為準。
- `uv`。
- Playwright Chromium。

Windows PowerShell：

```powershell
.\scripts\uv.ps1 sync
.\scripts\uv.ps1 run playwright install chromium
```

macOS / 其他 shell：

```bash
uv sync
uv run playwright install chromium
```

## 啟動 Web UI

Windows PowerShell：

```powershell
.\scripts\uv.ps1 run facebook-monitor
```

macOS / 其他 shell：

```bash
uv run facebook-monitor
```

launcher 預設先使用 port `4818`。若該 port 已被占用，且沒有指定固定 port，會自動挑選其他可用 port 並開啟瀏覽器。

首次啟動且尚未保存主題偏好時，Web UI 預設使用深色模式；使用者之後切換主題會保存到 app database。終端機顯示 `按 CTRL+C 停止。` 後，若收到 CTRL+C，launcher 會先輸出 `已收到停止指令，正在結束 Web UI...`，再等待 Web UI 與背景服務完成 graceful shutdown。

常用參數：

```powershell
.\scripts\uv.ps1 run facebook-monitor --data-dir "D:\fb_monitor_data"
.\scripts\uv.ps1 run facebook-monitor --port 4818 --no-open-browser
.\scripts\uv.ps1 run facebook-monitor --portable
```

Web UI 目前只允許綁定本機 loopback host。`--host` 若指定非 loopback 位址，launcher 會拒絕啟動；若未來要支援 LAN bind，需另做安全設計，不在目前 release 範圍。

## 設定 Facebook Automation Profile

本專案不使用你的日常 Chrome profile。它會在 app data directory 下建立專用 Playwright profile。

`facebook-monitor` 啟動時會先做不開瀏覽器的本機 profile 檢查：若找不到 Facebook session cookie，或前次掃描已標記需要重新登入，會先開 Facebook 首頁登入視窗；登入完成後視窗會自動關閉，再啟動 Web UI。這個檢查不會每次連到 Facebook 驗證 session，真正的 session 失效仍由掃描時偵測並標記。

`facebook-monitor-login` 保留為維運入口；需要手動檢查或重新登入同一個 automation profile 時可以直接開啟。

```powershell
.\scripts\uv.ps1 run facebook-monitor-login
```

自訂 data directory 或 profile name 時，Web UI 與登入工具必須使用同一組參數：

```powershell
.\scripts\uv.ps1 run facebook-monitor --data-dir "D:\fb_monitor_data" --profile-name automation_default
.\scripts\uv.ps1 run facebook-monitor-login --data-dir "D:\fb_monitor_data" --profile-name automation_default
```

同一時間只能有一個 process 持有 automation profile。若看到 `profile_locked`，先關閉登入視窗，或找出仍持有同一 profile 的 Playwright process。

## 建立 Targets

在 Web UI 貼上 Facebook URL：

- 社團首頁 URL：建立 posts target。
- 社團單篇貼文 URL：建立 comments target。

系統會依 URL 判斷 target type；使用者不需要手動選 target kind。

每張 target 卡片的主要操作：

- **開始**：清除該 target 的 seen scope 與通知去重紀錄、要求立即掃描，並喚醒背景 scheduler。
- **停止**：暫停排程，但保留 seen/history。
- **設定**：編輯 target-scoped keywords、刷新策略、掃描上限、排序/載入更多與通知設定。
- **命中紀錄**：查看該 target 保存的 match history。

target 卡片 header 會顯示模式、最近掃描與下次刷新；右側 panel 會顯示最近一輪掃描結果摘要。最近通知摘要不放在 target header，避免和掃描排程資訊混在一起。

背景 scheduler 會跟著 Web UI 啟動；日常使用沒有另一個全域 scheduler 主開關。

## Sidebar 群組與調整順序

左側 sidebar 可把 targets 分組，方便瀏覽較長的監看清單。

- 監看清單標題旁的選單可新增群組或進入「調整順序」模式。
- 調整順序模式下才會顯示拖曳把手；拖曳完成後按「確認」才保存。
- 群組可重新命名、刪除空群組，或開啟群組設定模板。
- 群組設定模板只在你按下套用時覆蓋該群組內 targets；不會作為日後 target 設定的隱性 fallback。
- 新增群組時會帶入當下的全域 keyword defaults；之後全域設定變更不會靜默改掉既有群組模板。

## Keywords 與規則

target 設定包含 include keywords、exclude keywords 與 exclude-ignore phrases。

exclude-ignore phrases 只會在排除判斷前遮罩特定片語，不會讓整筆 item 自動通過。例如排除字是 `收`，忽略片語是 `全收` 時，`全收` 裡的 `收` 不觸發排除，但其他位置的 `收` 仍會照常判斷。

## Notifications

target 可啟用：

- Desktop notification。
- ntfy topic。
- Discord webhook。

設定頁可保存全域 notification defaults，並套用到所有 target configs；個別 target 仍可覆寫。

日常通知由 scan commit 後的 notification outbox 發送；Web UI 不提供 direct dispatch 作為一般操作入口。

desktop notification 目前偏 Windows 使用情境。在不支援的平台上，sender 會回傳結構化失敗結果，而不是讓 scan crash。

## 資料路徑

預設 data directory：

```text
~/facebook_monitor_data
```

常見檔案：

- `app.db`：SQLite database。
- `secrets.key`：notification secret 加密 key，需與 `app.db` 一起備份。
- `profiles/automation_default`：Playwright automation profile。
- `logs/`：app、error、startup logs。
- `runtime/`：app instance lock、server metadata 與本機 Web UI runtime token。

launcher 與 profile setup 共用同一套 runtime path resolver。請優先使用 `--data-dir`、`--profile-name` 等 entrypoint 參數，不要直接修改 scripts 內的路徑。

## 備份與搬移

完整備份至少要保留：

- `app.db`
- `secrets.key`
- `profiles/automation_default` 或你實際使用的 profile name

`app.db` 與 `secrets.key` 必須視為同一組；只備份 DB、不備份 key，搬到新電腦後可能無法解密 ntfy topic 或 Discord webhook。`logs/` 與 `runtime/` 可用來診斷，但不是還原設定的必要資料。

搬移到新電腦時，先關閉 Web UI 與登入視窗，再複製整個 data directory。重新啟動後若找不到登入資料或 Facebook 要求驗證，`facebook-monitor` 會先開登入視窗；也可用 `facebook-monitor-login` 開啟同一個 `--data-dir` / `--profile-name` 手動檢查。

## 疑難排解

先看 Web UI runtime diagnostics 與 target scan diagnostics。常見情況：

- `profile_locked`：另一個 process 正持有同一個 automation profile。
- login / checkpoint failure：Web UI 會顯示需要重新登入；關閉並重新啟動 `facebook-monitor` 後會先開登入視窗。也可用 `facebook-monitor-login` 手動完成 Facebook 驗證。
- empty extractor：確認 Facebook 是否變更 layout，或內容是否被 login/checkpoint 擋住。
- notification failure：查看安全化後的 notification result 與 channel config。

notification topic 與 webhook 會在 UI 明文顯示，方便使用者確認輸入值。SQLite 內會加密保存，安全邊界看 `docs/ARCHITECTURE.md#notification-與-secret`。

## 驗證與工具

日常開發驗證與低頻 admin/debug/internal tools 看 `docs/tooling.md`。目前最近一次驗證結果看 `docs/TASK_BREAKDOWN.md#驗證`。
