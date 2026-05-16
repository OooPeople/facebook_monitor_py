# Updater Plan

本文件是 Windows EXE updater 尚未封口前的暫時計畫。已落地的穩定架構邊界放在 `docs/ARCHITECTURE.md#windows-updater`；使用者操作放在 `docs/USAGE.md#程式更新`；打包與 release asset 規則放在 `packaging/README.md`。功能封口後，本文件應刪除或歸檔到 `docs/archive/`。

## 目前結論

- 發佈型態維持 PyInstaller Windows onedir portable zip。
- 更新流程只處理 app files；使用者資料、browser profile、DB、secrets 與 logs 不可被更新流程搬移、覆蓋或刪除。
- Web UI 已能檢查 GitHub stable Release、下載精確版本 Windows portable zip、驗證 SHA256、寫出 pending handoff、啟動獨立 updater 並重啟新版 app。
- updater 已補主要安全邊界：path containment、SHA256 重驗、zip safety limit、staging app root 檢查、app lock 等待、backup/restore、`data/` 保留、temp updater onedir copy、cleanup warning log。
- 尚未完成封口條件：Web UI + tray 真實手動更新 UX smoke、release validation 對 artifact 一致性的自動化補強、舊 backup 清理策略、發布者身分驗證。

## Release Artifact 約定

正式 Windows release asset 使用固定命名：

```text
facebook-monitor-{version}-windows-portable.zip
facebook-monitor-{version}-windows-portable.zip.sha256
```

`APP_VERSION`、Windows version resource、GitHub tag、zip 檔名與 `.sha256` 內容必須一致。若其中任一項不同，應視為 release validation 失敗。updater 不 fallback 到其他版本 zip。

## 封口前待辦

1. 完成 Web UI + tray 手動更新 UX smoke：
   - 用舊版或 rc 測試版檢查正式 stable Release。
   - 按「下載新版並套用更新」。
   - 確認 modal 狀態簡短且會動。
   - 確認目前頁面短暫失效後，新版 app 自動開啟新頁面。
   - 確認 `updater.log` 有 `status=applied` 與 `restart_status=launched`。
   - 確認 DB/profile/secrets/logs 保留。
   - 確認本次下載 zip、`.sha256` 與 pending handoff 已清除。
2. 將 release validation 補上 artifact 一致性檢查：
   - GitHub tag / app version / Windows version metadata。
   - zip 檔名與 `.sha256` 檔名。
   - `.sha256` 內容格式與 hash。
   - portable zip 內必要 onedir 檔案。
3. 補 frozen updater smoke 自動化入口，至少覆蓋：
   - fixture update zip 套用成功。
   - `data/` 保留。
   - app lock 未釋放時等待或拒絕。
   - cleanup warning 可診斷。
4. 設計舊 backup 清理策略：
   - 保留最近 N 份或最近 N 天。
   - 不在套用成功前清掉可 rollback 的 backup。
   - 清理失敗只記 log warning。
5. 決定是否加入發布者身分驗證：
   - signed manifest。
   - detached signature。
   - Authenticode signer 驗證。

## Velopack 評估門檻

先不要導入 Velopack。滿足以下條件後再評估：

- portable zip + SHA256 updater 至少跑過一輪真實 release。
- release asset 命名、版本 metadata 與 artifact validation 已穩定。
- 確認需要 delta update、標準 installer UX、rollback/channel 管理或 signed package workflow 至少兩項。
- 願意接受從 portable onedir zip 轉向 Velopack 包裝語義。
- 已清楚定義現有 `<app_base_dir>/data` 是否遷移，或如何保留。

評估時必須確認：

- PyInstaller onedir `_internal` 結構是否適合 Velopack 包裝。
- bundled Chromium 大檔案對 delta update 的實際效果。
- tray app lifecycle、instance lock 與 updater apply/restart 是否相容。
- code signing、Defender、SmartScreen 的改善程度與剩餘風險。
- 對朋友使用者而言，installer 模式是否比 portable zip 更容易。

## 永久安全邊界

更新流程永遠不可上傳、打包、覆蓋或刪除：

- `<data-dir>/profiles/`
- `app.db`
- `secrets.key`
- `<logs-dir>`
- session dumps、cookies、tokens、私人 debug logs

SHA256 是完整性檢查，不是發布者身分保證。未簽章 EXE 的 SmartScreen / Defender 風險仍存在，release note 必須明講。若未來加入簽章，驗證順序應是來源限制、SHA256、簽章/憑證、staging 結構檢查。

## 明確不做

- 不做靜默背景更新。
- 不做主程式執行中 hot swap。
- 不做差分更新。
- 不做跨平台 updater。
- 不做任意 URL 或任意 zip 套用。
- 不把 updater 狀態混進 target configs、scheduler runtime、worker scan pipeline 或 notification outbox。
- 不為 updater 修改 Facebook DOM helper、posts/comments pipeline 或 notification dispatch。
- 不做自動 schema migration rollback；schema migration 仍由新版 app 正常啟動流程負責。
