# 任務狀態

本文件只記活狀態、下一步、風險與最近驗證。穩定架構事實看 `docs/ARCHITECTURE.md`；操作方式看 `docs/USAGE.md`；工具命令看 `docs/tooling.md`；接手摘要看 `docs/HANDOFF.md`；歷史推導看 `docs/archive/` 或 git history。

## 目前狀態

- Web UI 是正式日常入口；背景 scheduler / resident main 隨 Web UI 啟動。
- posts / comments target、target-scoped config/state、match history、notification outbox、dashboard partial update、sidebar group/order/template 主路徑已落地。
- target 卡片「開始」會清該 target 的 `seen_items` scope 與 `notification_outbox` 去重 rows；停止後再開始會重新通知同一命中。`match_history` 持久保留。
- Source/dev smoke test 已完成。Windows PyInstaller onedir portable、同名 `.sha256`、release artifact validation 與 frozen updater smoke 已能支援後續打包維護；frozen CI 仍是 deferred。
- Source 版本已升至 `0.2.0`；既有 `dist` 內 frozen artifact 仍需重新打包後才會變成 `0.2.0`。
- Windows EXE updater 主路徑已落地：設定頁可手動查 GitHub stable Release metadata；若新版 release 含精確版本 Windows portable zip 與 `.sha256`，可用「下載新版並套用更新」流程下載、驗證、寫出 pending handoff、啟動獨立 updater、關閉 Web UI、替換 app files、保留 `data/`，並用原 data/db/profile/logs 路徑重啟新版 app。
- Updater 封口項目已收斂：Web UI + tray 真實手動更新 UX smoke 已由使用者確認可用；Release artifact 不再 fallback 到其他版本 zip；temp updater runtime copy 改用唯一暫存目錄並清理舊目錄；updater CLI 成功套用與 restart 使用同一份已讀 pending handoff；成功套用後清除本次下載 zip / `.sha256` / pending handoff，並以安全邊界保留最近 3 份 updater 產生的 backup。
- `scripts/admin/release_artifact_validation.py` 可驗證 zip、`.sha256`、zip 內 EXE version resource、必要 onedir 檔案、可選 tag 與可選 Authenticode signer；`scripts/admin/smoke_frozen_updater.py` 可用 frozen build 自動驗證 updater 套用、data 保留與 cleanup。

## 下一步

1. Updater 目前沒有封口阻塞項；後續打包時依 `packaging/README.md` 同步產生 `.sha256`，並跑 artifact validation 與 frozen updater smoke。
2. 若要降低 SmartScreen / 發布者身分風險，需要由使用者提供正式 code signing 憑證與預期 signer subject，再啟用 Authenticode validation。
3. 若後續補 frozen CI，至少覆蓋 onedir 啟動、`/health`、bundled Chromium lookup、instance lock、zip artifact、SHA256 檢查與 frozen updater smoke。

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
- SHA256 只能證明下載內容完整，不能證明發布者身分；尚未加入 signed manifest / detached signature。Authenticode signer validation hook 已有，但需要正式 code signing 憑證才有實際效果。
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

- 使用者已確認 Web UI + tray 真實手動更新 UX smoke 可成功套用更新。
- `.\scripts\uv.ps1 run python scripts\admin\smoke_frozen_updater.py`，通過；app files 被替換、data/profile 保留、pending/zip/sha256 清除、`updater.log` 含 applied。
- `.\scripts\uv.ps1 run python scripts\admin\release_validation.py --skip-sync --include-artifacts`，通過；`508 passed`、mypy/ruff/compileall/artifact validation/`git diff --check` 全部成功，僅 Git CRLF 換行提示。
- 升版至 `0.2.0` 後 `.\scripts\uv.ps1 run python scripts\admin\release_validation.py --skip-sync` 通過；`508 passed`、mypy/ruff/compileall/`git diff --check` 全部成功。因尚未重新打包 `0.2.0` artifact，本輪未跑預設 artifact validation。
