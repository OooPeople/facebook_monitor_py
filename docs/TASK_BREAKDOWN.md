# 任務狀態

本文件只記活狀態、下一步、風險與最近驗證。穩定架構事實看 `docs/ARCHITECTURE.md`；操作方式看 `docs/USAGE.md`；工具命令看 `docs/tooling.md`；接手摘要看 `docs/HANDOFF.md`；歷史推導看 `docs/archive/` 或 git history。

## 目前狀態

- Web UI 是正式日常入口；背景 scheduler / resident main 隨 Web UI 啟動。
- posts / comments target、target-scoped config/state、match history、notification outbox、dashboard partial update、sidebar group/order/template 主路徑已落地。
- target 卡片「開始」會清該 target 的 `seen_items` scope 與 `notification_outbox` 去重 rows；停止後再開始會重新通知同一命中。`match_history` 持久保留。
- Source/dev smoke test 已完成。Windows PyInstaller onedir portable 已能輸出 `facebook-monitor.exe`、`facebook-monitor-updater.exe`、portable zip 與 `.sha256`；Code signing 本輪不做；frozen CI 尚未封口。
- Windows EXE updater 主路徑已落地：設定頁可手動查 GitHub stable Release metadata；若新版 release 含精確版本 Windows portable zip 與 `.sha256`，可用「下載新版並套用更新」流程下載、驗證、寫出 pending handoff、啟動獨立 updater、關閉 Web UI、替換 app files、保留 `data/`，並用原 data/db/profile/logs 路徑重啟新版 app。
- 本輪 review 修正已完成：更新 modal 不會在後端成功前顯示「即將關閉並啟動新版」、Release asset 不再 fallback 到其他版本 zip、temp updater runtime copy 改用唯一暫存目錄並清理舊目錄、updater CLI 成功套用與 restart 使用同一份已讀 pending handoff、成功套用後 cleanup 失敗會寫入 `updater.log` warning。
- 前次已輸出正式 `dist/facebook-monitor-0.1.0-windows-portable.zip` 與同名 `.sha256`，checksum 為 `a879241be1250a25fb6db2d4c9f24a4dc77f765d860c83069bd28bb3a14ab96b`；但該打包產物不含本輪 review 修正。若要上傳新的 GitHub Release asset，需重新打包並重新產生 `.sha256`。
- 前次已建立測試專用 `dist/facebook-monitor-0.1.0-rc1-fixed-build/facebook-monitor/`，此 build 回報 `0.1.0-rc1` 但包含當時 updater UX 與 GitHub Release asset 302 redirect 修正；只用來測試 rc1 更新到正式 `0.1.0`，不要上傳為正式 release。

## 下一步

1. 若要發佈含本輪修正的版本，重新打包 Windows portable zip，並同時產生同名 `.sha256`。
2. 用舊版或 rc1 測試版跑 Web UI + tray 手動更新 UX smoke：按「下載新版並套用更新」、確認 modal 狀態簡短且會動、觀察 tray 退出、確認新版 app 重啟、檢查 `updater.log` 有 `status=applied` 與 `restart_status=launched`、資料保留、`<data-dir>/updates/<version>/` 下載 zip / `.sha256` 已清除。
3. 若準備 release tag，保存 `scripts/admin/release_validation.py --skip-sync` 與 frozen smoke 輸出摘要。
4. 發佈 Windows 版時使用整包 portable zip 與同名 `.sha256`；不要只發佈單一 EXE。
5. 若後續補 frozen CI，至少覆蓋 onedir 啟動、`/health`、bundled Chromium lookup、instance lock、zip artifact 與 SHA256 檢查。

## 目前不做

- 不在本輪做 Windows code signing、signed manifest、detached signature、背景靜默更新、主程式 hot swap、差分更新或 Velopack 導入。
- 不做多 profile orchestration。
- 不搬 userscript 的頁內 panel UI。
- 不把 one-shot / sync resident fallback 包裝成正式產品 parity。
- 不把 notification outbox 改成獨立常駐 dispatcher。
- 不宣稱 mutation relevance 已接上即時觸發；目前 Python resident main worker 仍是 polling。
- 不新增新的 `phase_*` script。

## 主要風險

- Windows 未簽章 EXE 可能觸發 SmartScreen / Defender 提示；release note 需明講。
- SHA256 只能證明下載內容完整，不能證明發布者身分；尚未加入 signed manifest / detached signature / Authenticode signer 驗證。
- 更新流程若 app base dir 判斷錯誤可能造成破壞；目前已有 pending path、app root、staging、容量限制與「不覆蓋 data dir」測試防護。
- Facebook 可能要求重新登入、checkpoint 或改版造成 selector / extractor 不穩；headless / headed DOM 也可能不一致。
- resident worker、登入視窗與 debug tool 不能同時持有同一 automation profile。
- notification topic / webhook 在 UI 明文顯示；SQLite 內已加密保存，但 DB 與 `secrets.key` 同時外流時仍可解密。
- target stop 會取消正在執行的 resident scan；若外部 Playwright/OS 層阻塞無法及時響應 cancellation，仍需依 runtime diagnostics 與下輪 stale recovery 判讀。

## 驗證

常用完整驗證指令：

```powershell
.\scripts\uv.ps1 run python scripts\admin\release_validation.py
.\scripts\uv.ps1 run pytest -q
.\scripts\uv.ps1 run mypy
.\scripts\uv.ps1 run python -m compileall -q src scripts tests
.\scripts\uv.ps1 run ruff check src scripts tests
.\scripts\uv.ps1 run pip-audit
git diff --check
```

最近驗證（2026-05-17）：

- `node --check src\facebook_monitor\webapp\static\dashboard\settings.js`，通過。
- `node --check src\facebook_monitor\webapp\static\dashboard\utils.js`，通過。
- `.\scripts\uv.ps1 run pytest tests\updates\test_release_check.py tests\updates\test_launcher.py tests\updates\test_apply.py tests\updates\test_updater_cli.py tests\webapp\test_app.py -q`，通過；`105 passed`。
- `.\scripts\uv.ps1 run mypy src\facebook_monitor\updates\release_check.py src\facebook_monitor\updates\launcher.py src\facebook_monitor\updates\apply.py src\facebook_monitor\updater.py src\facebook_monitor\webapp\routes\settings.py`，通過。
- `.\scripts\uv.ps1 run ruff check src\facebook_monitor\updates\release_check.py src\facebook_monitor\updates\launcher.py src\facebook_monitor\updates\apply.py src\facebook_monitor\updater.py src\facebook_monitor\webapp\routes\settings.py tests\updates\test_release_check.py tests\updates\test_launcher.py tests\updates\test_apply.py tests\updates\test_updater_cli.py tests\webapp\test_app.py`，通過。
- `.\scripts\uv.ps1 run python -m compileall -q src\facebook_monitor\updates\release_check.py src\facebook_monitor\updates\launcher.py src\facebook_monitor\updates\apply.py src\facebook_monitor\updater.py src\facebook_monitor\webapp\routes\settings.py tests\updates tests\webapp\test_app.py`，通過。
- `git diff --check`，通過；僅 Git 的 CRLF 換行提示。
