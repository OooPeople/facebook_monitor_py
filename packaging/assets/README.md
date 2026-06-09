# Packaging Assets

正式 EXE icon 的輸入檔請放在這個目錄。

- 建議原始圖：`facebook-monitor.png`
- 建議尺寸：`1024x1024` 或至少 `512x512`，正方形，背景可透明或不透明
- PyInstaller 使用檔：`facebook-monitor.ico`
- Windows tray 專用檔：`facebook-monitor-tray.ico`
- `.ico` 應包含常用 Windows icon sizes：`16, 20, 24, 32, 40, 48, 64, 128, 256`

`packaging/pyinstaller/facebook_monitor.spec` 會在 `packaging/assets/facebook-monitor.ico` 存在時自動寫入 EXE。
`facebook-monitor-tray.ico` 若存在，右下角 system tray 與 Windows desktop notification 會優先使用它；若要讓 EXE 檔案圖示與 tray / notification 圖示共用，只要把 `facebook-monitor.ico` 複製成 `facebook-monitor-tray.ico`。Tray 實際常顯示 16px/20px/24px/32px，desktop notification 目前會載入 64px icon，因此不要只輸出單一 256x256 icon。
