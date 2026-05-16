# 任務狀態

本文件只記活狀態、下一步、風險與最近驗證。穩定架構事實看 `docs/ARCHITECTURE.md`；操作方式看 `docs/USAGE.md`；工具命令看 `docs/tooling.md`；接手摘要看 `docs/HANDOFF.md`；歷史推導看 `docs/archive/` 或 git history。

## 目前狀態

- Web UI 是正式日常入口；背景 scheduler / resident main 隨 Web UI 啟動。
- posts / comments target、target-scoped config/state、match history、notification outbox、dashboard partial update 與 sidebar group/order/template 主路徑已落地。
- target 卡片「開始」會清該 target 的 `seen_items` scope 與 `notification_outbox` 去重 rows；停止後再開始會重新通知同一命中。`match_history` 持久保留。
- 新增 target 與空白 group template 預設使用浮動刷新；設定頁刷新模式 radio 顯示順序為浮動刷新在左、固定刷新在右。
- Target metadata refresh 已接上 resident worker，可重新抓取名稱與封面；成功後會覆蓋 target 標題，comments target 標題只使用社團名稱。
- Sidebar group template 只在使用者明確套用時覆蓋群組內 target configs，不作為 config fallback owner；套用前有批次影響確認，套用後保留 scroll。
- Sidebar layout 缺失 placement 採 read-model lazy fallback 顯示在未分組區；排序保存才寫入 placement。
- Source/dev smoke test 已完成。Windows PyInstaller onedir portable 已封到 `0.1.0-rc1`，包含 bundled Chromium、GUI subsystem、system tray、Windows version metadata、正式 icon 與 portable zip；真實 profile / 引導登入 / posts-comments scan / desktop / ntfy / Discord smoke 已完成。Code signing 本輪不做；frozen CI 尚未封口。

## 下一步

1. 若準備 release tag，保存 `scripts/admin/release_validation.py --skip-sync` 與 frozen smoke 輸出摘要。
2. 發佈 Windows 版時使用整包 `dist/facebook-monitor-0.1.0-rc1-windows-portable.zip`；不要只發佈單一 EXE。
3. 若後續補 frozen CI，至少覆蓋 onedir 啟動、`/health`、bundled Chromium lookup、instance lock 與 zip artifact 檢查。
4. 遇到 JS 已有成熟語義的功能，先依 `docs/REFERENCE_MAP.md` 對照 `reference/src/facebook_group_refresh.user.js`。

## 目前不做

- 不在本輪做 Windows code signing。
- 不做多 profile orchestration。
- 不搬 userscript 的頁內 panel UI。
- 不把 one-shot / sync resident fallback 包裝成正式產品 parity。
- 不把 notification outbox 改成獨立常駐 dispatcher。
- 不宣稱 mutation relevance 已接上即時觸發；目前 Python resident main worker 仍是 polling。
- 不新增新的 `phase_*` script。

## 主要風險

- Facebook 可能要求重新登入、checkpoint 或改版造成 selector / extractor 不穩。
- headless / headed DOM 可能不一致。
- resident worker、登入視窗與 debug tool 不能同時持有同一 automation profile。
- Windows 未簽章 EXE 可能觸發 SmartScreen / Defender 提示；release note 需明講。
- notification topic / webhook 在 UI 明文顯示；SQLite 內已加密保存，但 DB 與 `secrets.key` 同時外流時仍可解密。
- notification outbox 仍是 commit-after immediate dispatch，尚未拆成獨立常駐 background dispatcher。
- target stop 會取消正在執行的 resident scan；若外部 Playwright/OS 層阻塞無法及時響應 cancellation，仍需依 runtime diagnostics 與下輪 stale recovery 判讀。
- sidebar group template 是破壞性批次覆蓋操作；目前以前端批次影響確認視窗與 application transaction 防止誤套用與半套用。

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

最近驗證（2026-05-16）：

- `.\scripts\uv.ps1 run python scripts\admin\release_validation.py --skip-sync`，通過；`434 passed`，mypy / ruff / compileall / `git diff --check` 通過。
- `.\scripts\uv.ps1 run pytest tests\webapp\test_app.py tests\webapp\test_static_dashboard_modules.py tests\webapp\test_dashboard_rendering.py -q`，通過；`119 passed`，包含 settings/new target/target modal/sidebar 共用設定表單 partial。
- `.\scripts\uv.ps1 run pytest tests\automation\test_browser_runtime.py tests\runtime\test_build_metadata.py tests\runtime\test_windows_tray.py tests\runtime\test_csrf_token.py tests\cli\test_launcher_instance.py -q`，通過；覆蓋 bundled Chromium lookup、PyInstaller metadata、Windows tray、GUI stream 修補與 runtime CSRF token。
- `.\scripts\uv.ps1 run python -m PyInstaller packaging\pyinstaller\facebook_monitor.spec --clean --noconfirm`，通過；產出 `dist\facebook-monitor\facebook-monitor.exe` 與 `dist\facebook-monitor-0.1.0-rc1-windows-portable.zip`。
- frozen manual smoke 通過：isolated Web layer、既有真實 profile、guided login、metadata refresh、posts/comments scan、desktop / ntfy / Discord notification、bundled Chromium、GUI subsystem、system tray 與 instance lock。
- `git diff --check`，通過（僅 Git 換行提示）。
