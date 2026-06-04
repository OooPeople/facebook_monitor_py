# macOS Apple Silicon Packaging Handoff

本文件只保留 macOS 打包接手所需的「目前狀態、最近驗證與後續邊界」。
穩定打包命令、zip / SHA256 產生方式與 frozen smoke checklist 的主來源是
`packaging/README.md`；使用者操作看 `docs/USAGE.md`；updater 架構語義看
`docs/ARCHITECTURE.md#frozen-updater`。

## Current Status

- 目前只支援 macOS Apple Silicon / arm64；Intel Mac 不在目前 artifact 範圍。
- macOS artifact 是 PyInstaller onedir zip，內含 `Facebook Monitor.app`
  Finder / Dock native launcher。
- `Facebook Monitor.app` 是 Dock 母程序，會啟動同一個 onedir 內的
  `facebook-monitor` child process；Dock Quit 會終止 child process。
- 若舊版 updater 或 Finder 直接啟動 root `facebook-monitor` binary，新版
  frozen launcher 會自動轉交給 `.app` native launcher，避免 Dock item 消失。
- macOS Web UI 已支援 stable Release 檢查、下載、signed manifest / SHA256
  驗證、handoff、temp updater 套用與重啟。
- 尚未做 Developer ID signing / notarization；測試或使用者環境可能仍需處理
  Gatekeeper / quarantine。

## Recent Verification

- 2026-05-20 已發布 macOS `v0.3.2` release，包含
  `facebook-monitor-0.3.2-macos-arm64-onedir.zip` 與同名 `.sha256`。
- 2026-05-20 使用者已實機驗證：測試用 `0.3.1` frozen app 可透過設定頁
  「檢查更新 / 下載並套用」更新到 `0.3.2`。
- 同次實機驗證確認：更新後 Dock 圖示會持續顯示，且從 Dock Quit 可成功關閉
  主程式。
- 本地驗證曾覆蓋 artifact validation、frozen updater smoke、direct root
  binary self-redirect 與 `.app` launcher / child process lifecycle。
- 目前 release / runtime 驗證也覆蓋 signed manifest / `.sig`、macOS staging
  app root 的 arm64 Mach-O、executable bit、Info.plist version / Dock visibility、
  私密 runtime data 排除、安全的 tree-internal symlink 保留，以及 temp updater
  目錄 symlink / ownership / permission 防護。

## Build Notes

- macOS build machine 需先有 Playwright Chromium cache，或用
  `FACEBOOK_MONITOR_BUNDLED_CHROMIUM_DIR` 指到含 `.app` browser executable 的目錄。
- 目前 Mac 實測 Playwright cache 形狀是
  `chromium-*/chrome-mac-arm64/Google Chrome for Testing.app`。
- `packaging/pyinstaller/facebook_monitor_macos.spec` 會在 `dist/facebook-monitor/`
  內建立 `.app` native launcher，圖示來源為
  `packaging/assets/facebook-monitor.png`。
- release 前必跑：
  - `scripts/admin/release_artifact_validation.py --platform macos-arm64 --require-manifest`
  - `scripts/admin/smoke_frozen_updater.py --built-app dist/facebook-monitor`

## Deferred

- Developer ID signing / notarization 尚未導入。
- Intel Mac / universal binary artifact 尚未納入範圍。
- Windows release asset 可在 macOS updater release 之後由人工補上，但正式 release
  asset 命名仍必須與 tag version 對齊。
