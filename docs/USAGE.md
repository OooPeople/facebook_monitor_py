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

target 社團縮圖是顯示輔助，不影響掃描與通知。若縮圖 URL 過期，dashboard 會先退回文字 avatar，並在背景排一次只更新封面 URL 的 maintenance job；這條自動修復不會改 target 顯示名稱。設定內的「重新抓取名稱與封面」是另一個手動操作，按下後允許用 Facebook 抓到的社團名稱覆蓋目前 target 顯示名稱。

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

ntfy topic 與 Discord webhook 都視為敏感 endpoint。SQLite 內會加密保存；錯誤訊息與診斷摘要會遮罩 token / URL credential。啟用 ntfy 或 Discord 時，通知標題、摘要與連結會送到對應第三方服務，若 target 內容不適合外送，請只使用 desktop notification 或停用外部通知。

Settings 頁會顯示 notification outbox 的待送、處理中、失敗與已結束數量。failed outbox 不會被一般 scan commit 自動重試；需要時可在 Settings 使用「重試 failed 通知」，系統會重新 claim failed rows 並套用目前 target 通知設定後再送出。

## 程式更新

Windows portable EXE 與 macOS Apple Silicon onedir 版本可在設定頁的「程式更新」區塊手動檢查 GitHub stable Release。source mode 只提供檢查資訊，不提供自動套用更新。macOS zip 解壓後請從 `facebook-monitor/Facebook Monitor.app` 啟動；它會在執行期間顯示在 Dock，可從 Dock Quit 關閉，並避免 Finder 直接開 Unix executable 時跳出 Terminal。若舊版 updater 直接啟動新版 root binary，新版會自動轉交給 `.app` launcher。

一般使用流程：

1. 在設定頁按「檢查更新」。
2. 若有新版，按「下載新版並套用更新」。
3. 彈窗會顯示下載、驗證與準備更新狀態；進入重啟階段後，目前分頁短暫失效是正常現象。
4. 當新版 app 自動開出新頁面後，舊分頁可以關閉。

更新流程會先驗證 GitHub Release 內的 Ed25519 signed manifest / detached signature，再交叉檢查 release zip 的 SHA256 與大小；`.sha256` 檔保留作人工驗證與相容用途。這是免費的 release 內容簽章，不等同 Windows Authenticode 或 macOS Developer ID signing / notarization，因此 OS 仍可能顯示 SmartScreen、Defender 或 Gatekeeper 提示。

更新流程會保留 `data/` 內的 DB、profile、secrets 與 logs。下載檔會放在 `<data-dir>/updates/<version>/`，套用成功後會清除本次下載 zip、同名 `.sha256`、signed manifest / `.sig` 與 pending handoff。若套用或清理失敗，先看 `<logs-dir>/updater.log`。

若更新後沒有自動重啟：

- 確認 Windows tray 內的舊程式是否仍在執行，必要時從 tray 選單 Exit 後再重試。
- 查看 `<logs-dir>/updater.log` 是否有 `status=applied`、`restart_status=launched` 或 `cleanup_warning`。
- 確認 GitHub Release asset 是整包 `facebook-monitor-{version}-windows-portable.zip` 與同名 `.sha256`，不是單一 EXE。
- macOS 請確認使用的是 Apple Silicon onedir zip：`facebook-monitor-{version}-macos-arm64-onedir.zip` 與同名 `.sha256`。未簽章 / 未 notarize 的 build 仍可能需要使用者允許 Gatekeeper 或清除 quarantine。

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

## 資料清除與支援診斷包

target 卡片的更多操作提供 target-scoped 清除：

- 清除 baseline：刪除該 target 的 seen items 並重置 scan scope baseline；下一輪會重新建立 baseline，可能暫時抑制通知。
- 清除命中紀錄：刪除該 target 的 match history，不改 seen baseline 與 target 設定。
- 清除通知資料：刪除該 target 的 notification events 與 outbox rows；pending 或 failed 通知也會被移除。

Settings 頁的「下載支援診斷包」會建立 redacted zip，內容只含 runtime 診斷、路徑摘要、table counts、notification outbox summary 與 DB invariant 檢查結果。診斷包刻意不包含 SQLite DB、browser profile、cookies、secrets、logs、完整貼文/留言內容或完整 webhook。

## 限制與隱私邊界

- 本工具只支援社團貼文列表與單篇貼文留言 target；其他 Facebook 物件類型尚未定義正式產品語義。
- Facebook 可能因 checkpoint、權限、登入狀態、DOM 改版或自動化偵測而使掃描失敗。遇到 login / checkpoint failure 時，先用 `facebook-monitor-login` 處理登入；遇到 extractor 類失敗時，保留 diagnostics 供後續調整 selector。
- Web UI 預設只綁 loopback，適合單機使用；不要把它暴露到 LAN 或公開網路。
- data directory 內的 `app.db`、`secrets.key` 與 automation profile 合在一起代表完整本機信任邊界；需要分享診斷時優先使用 Settings 產生的支援診斷包，不要直接分享 DB、profile、cookies、secrets、完整貼文內容或完整 webhook。
- 未簽章 / 未 notarize 的 frozen build 可能需要使用者在 OS 層手動允許；這不代表 signed manifest 驗證失敗，而是 OS 發布者身分驗證尚未導入。

## 疑難排解

先看 Web UI runtime diagnostics 與 target scan diagnostics。常見情況：

- `profile_locked`：另一個 process 正持有同一個 automation profile。
- login / checkpoint failure：Web UI 會顯示需要重新登入；關閉並重新啟動 `facebook-monitor` 後會先開登入視窗。也可用 `facebook-monitor-login` 手動完成 Facebook 驗證。
- empty extractor：確認 Facebook 是否變更 layout，或內容是否被 login/checkpoint 擋住。
- notification failure：查看安全化後的 notification result 與 channel config；Discord webhook 格式錯誤時，系統不會送出 HTTP request。
- 社團縮圖沒有恢復：先確認 dashboard 是否重新整理過；自動修復只在瀏覽器載圖失敗時觸發，不會定期主動開 Facebook 更新所有 targets。若要查排程狀態，可用唯讀 SQLite 查 `target_cover_image_refresh_state` 的 `status`、`last_result`、`error`、`last_reported_url` 與 `last_resolved_url`。

notification secrets 的保存邊界看 `docs/ARCHITECTURE.md#notification-與-secret`。

## 驗證與工具

日常開發驗證與低頻 admin/debug/internal tools 看 `docs/tooling.md`。最近一次驗證結果屬於本機進度筆記或 release 記錄，不放在公開使用文件。
