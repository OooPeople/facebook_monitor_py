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

常用參數：

```powershell
.\scripts\uv.ps1 run facebook-monitor --data-dir "D:\fb_monitor_data"
.\scripts\uv.ps1 run facebook-monitor --port 4818 --no-open-browser
.\scripts\uv.ps1 run facebook-monitor --portable
```

Web UI 預設只供本機 loopback 使用。若要綁定非 loopback host，必須明確使用 launcher 選項，且只應在可信任網路中使用。

## 設定 Facebook Automation Profile

本專案不使用你的日常 Chrome profile。它會在 app data directory 下建立專用 Playwright profile。

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

背景 scheduler 會跟著 Web UI 啟動；日常使用沒有另一個全域 scheduler 主開關。

## Sidebar 群組與排序

左側 sidebar 可把 targets 分組，方便瀏覽較長的監看清單。

- 監看清單標題旁的選單可新增群組或進入排序模式。
- 排序模式下才會顯示拖曳把手；拖曳完成後按「確認」才保存。
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

手動測試通知會使用目前表單值，但不會保存 target；除非另外提交設定表單。

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
- `runtime/`：app instance lock 與 server metadata。

launcher 與 profile setup 共用同一套 runtime path resolver。請優先使用 `--data-dir`、`--profile-name` 等 entrypoint 參數，不要直接修改 scripts 內的路徑。

## 疑難排解

先看 Web UI runtime diagnostics 與 target scan diagnostics。常見情況：

- `profile_locked`：另一個 process 正持有同一個 automation profile。
- login / checkpoint failure：開啟 `facebook-monitor-login` 完成 Facebook 驗證。
- empty extractor：確認 Facebook 是否變更 layout，或內容是否被 login/checkpoint 擋住。
- notification failure：查看安全化後的 notification result 與 channel config。

notification topic 與 webhook 會在 UI 明文顯示，方便使用者確認輸入值。SQLite 內會加密保存，安全邊界看 `docs/ARCHITECTURE.md#notification-與-secret`。

## 驗證與工具

日常開發驗證與低頻 admin/debug/internal tools 看 `docs/tooling.md`。目前最近一次驗證結果看 `docs/TASK_BREAKDOWN.md#驗證`。
