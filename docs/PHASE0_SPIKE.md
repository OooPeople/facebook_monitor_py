# Phase 0 Spike

## 目的

Phase 0 只回答一個問題：

Python + Playwright worker 能否在不需要前景 Facebook 視窗的情況下，於背景監視一個 Facebook 社團貼文 target？

如果答案是否定的，暫時不要建立完整 Python 架構。

## 測試形狀

這個 spike 必須刻意保持很小：

- 一個專用 automation profile。
- 一個 group posts target。
- headed setup 負責登入與手動切到目標頁面。
- headless worker probe 使用同一個 profile。
- 最小抽取內容：目前 URL、title、body size；加入 selector 後，再抽取少量可見且類似貼文的 items。
- 最小 log 輸出，放在 `logs/`。

在可行性被證明前，不要加入 comments monitoring、SQLite、管理 UI 或 multi-target scheduling。

## uv 初始指令

本專案使用 `uv` 管理環境。Windows PowerShell 請優先使用專案 wrapper：

```powershell
.\scripts\uv.ps1 sync
.\scripts\uv.ps1 run playwright install chromium
```

執行 headed setup：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase0_setup_login.py
```

登入並切到目標社團頁面後，在 terminal 按 Enter 關閉 setup。

執行 headless probe：

```powershell
.\scripts\uv.ps1 run python .\scripts\phase0_worker_probe.py "https://www.facebook.com/groups/<group_id>"
```

## 通過標準

Phase 0 通過條件：

- headless probe 可以重用 headed setup profile。
- setup 關閉後，target page 仍可被開啟。
- worker 重新啟動後，仍可開啟 target page。
- 後續版本的 probe 可以連續執行 2-4 小時，且不需要前景 Facebook 視窗。
- logs 提供足夠資訊，可用來診斷失敗原因。

## 失敗分類

每次執行失敗時，請分類為以下其中之一：

- `profile_missing`
- `profile_locked`
- `login_required`
- `checkpoint_or_verification`
- `headless_dom_mismatch`
- `page_load_timeout`
- `extractor_empty`
- `notification_failed`
- `unknown`

在主要失敗模式被理解前，不要進入完整架構實作。
