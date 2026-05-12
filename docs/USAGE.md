# 使用說明

本文件說明日常操作方式。穩定架構邊界看 `docs/ARCHITECTURE.md`；admin / debug scripts 看 `docs/tooling.md`。

## 需求

- Python 3.13，以 `.python-version` 為準。
- `uv`。
- 透過專案環境安裝 Playwright Chromium。

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
.\scripts\uv.ps1 run facebook-monitor --data-dir "D:\fb_monitor_data" --port 4818 --no-open-browser
.\scripts\uv.ps1 run facebook-monitor --portable
```

Web UI 預設是本機 loopback 服務。若要綁定非 loopback host，必須明確使用 launcher 選項，且只應在可信任網路中使用。

## 設定 Facebook Automation Profile

本專案不使用你的日常 Chrome profile。它會在 app data directory 下建立並使用專用 Playwright profile。

Windows PowerShell：

```powershell
.\scripts\uv.ps1 run facebook-monitor-login
```

macOS / 其他 shell：

```bash
uv run facebook-monitor-login
```

若使用自訂 data directory 或 profile name，Web UI 與 profile setup 必須傳入同一組參數：

```powershell
.\scripts\uv.ps1 run facebook-monitor --data-dir "D:\fb_monitor_data" --profile-name automation_default
.\scripts\uv.ps1 run facebook-monitor-login --data-dir "D:\fb_monitor_data" --profile-name automation_default
```

同一時間只能有一個 process 持有 automation profile。若看到 `profile_locked` diagnostic，先關閉 profile setup 視窗，或找出仍持有同一 profile 的 Playwright process。

## 建立 Targets

在 Web UI 貼上以下任一類 URL：

- Facebook 社團首頁 URL：建立 posts target。
- Facebook 社團單篇貼文 URL：建立 comments target。

Web route 會依 URL 判斷 target type。使用者不需要、也不應手動選 target kind；URL 是正式來源。

每張 target 卡片都有日常操作：

- **開始**：清除該 target 的 seen scope、要求立即掃描，並喚醒內部 resident scheduler。
- **停止**：暫停排程，但保留 seen/history。
- **設定**：編輯 target-scoped keywords、scan interval、max items、auto-load-more、auto-adjust-sort 與 notification settings。
- **命中紀錄**：查看該 target 保存的 match history。

背景 scheduler 會跟著 Web UI 啟動；日常使用沒有另一個全域 scheduler 主開關。

## Keywords 與規則

target 設定包含：

- Include keywords。
- Exclude keywords。
- Exclude-ignore phrases。

include / exclude matching 依專案 keyword rule model 執行。exclude-ignore phrases 只會在 exclude matching 前遮罩特定片語，不會讓整筆 item 自動通過。

## Notifications

target 可啟用下列通知通道：

- Desktop notification。
- ntfy topic。
- Discord webhook。

設定頁可保存全域 notification defaults，並套用到所有 target configs。個別 target 仍可覆寫這些值。

手動測試通知按鈕會使用目前表單值，但不會保存 target；除非另外提交設定表單。

desktop notification 目前偏 Windows 使用情境。在不支援的平台上，sender 會回傳結構化失敗結果，而不是讓 scan crash。

## 資料路徑

預設 data directory：

```text
~/facebook_monitor_data
```

data directory 下常見路徑：

- `app.db`：SQLite database。
- `secrets.key`：notification secret 加密 key，需與 `app.db` 一起備份；兩者同時外流時可解密 topic / webhook。
- `profiles/automation_default`：Playwright automation profile。
- `logs/app.log`：app log。
- `logs/error.log`：error log。
- `logs/startup.log`：startup diagnostics。
- `runtime/`：app instance lock 與 server metadata。

launcher 與 profile setup 共用同一套 runtime path resolver。請優先使用 `--data-dir`、`--profile-name` 等 entrypoint 參數，不要直接修改 scripts 內的路徑。

## 疑難排解

先看 Web UI runtime diagnostics 與 target scan diagnostics。這些資訊會顯示：

- worker name。
- runtime status。
- candidate / collected counts。
- sort before / after labels。
- load-more mode 與 stop reason。
- extractor failure reason。
- latest scan item debug metadata。

常見情況：

- `profile_locked`：另一個 process 正持有同一個 automation profile。
- login / checkpoint failure：開啟 `facebook-monitor-login` 完成 Facebook 驗證。
- empty extractor：查看 latest scan diagnostics，確認 Facebook 是否變更 layout，或內容是否被 login/checkpoint 擋住。
- notification failure：查看安全化後的 notification result 與 channel config。

notification topic 與 webhook 會在 UI 明文顯示，方便使用者確認自己輸入的值。SQLite 內會以 DB-at-rest 加密保存；細節記錄於 `docs/SECRET_STORAGE.md`。

## 驗證

交付或給他人測試前，建議跑完整驗證：

```powershell
.\scripts\uv.ps1 run pytest -q
.\scripts\uv.ps1 run mypy
.\scripts\uv.ps1 run pytest tests\core --cov=facebook_monitor.core --cov-report=term-missing -q
.\scripts\uv.ps1 run python -m compileall -q src scripts tests
.\scripts\uv.ps1 run ruff check src scripts tests
.\scripts\uv.ps1 run pip-audit
git diff --check
```

## Admin 與 Debug 工具

日常使用只走 package entrypoints：

```powershell
.\scripts\uv.ps1 run facebook-monitor
.\scripts\uv.ps1 run facebook-monitor-login
```

低頻 admin / debug / internal tools 放在 `scripts/`，索引在 `docs/tooling.md`。它們不是正式日常產品入口。
