# Updater Plan

本文件是 Windows EXE 更新功能開發期間的暫時計畫來源。等功能封口後，穩定事實應搬回 `packaging/README.md`、`docs/ARCHITECTURE.md` 或 `docs/USAGE.md`；本文件不作為長期正式文件保留，應刪除或歸檔到 `docs/archive/`。

目前發佈型態是 PyInstaller Windows onedir portable zip。更新功能必須保留這個前提：程式檔可以替換，但使用者資料、browser profile、DB、secrets 與 logs 不可被更新流程搬移、覆蓋或刪除。

## 目標

1. 先讓 Web UI 能檢查 GitHub Release 是否有新版。
2. 再讓 Web UI 能下載 Windows portable zip，完成 SHA256 驗證，並開啟下載所在資料夾。
3. 最後才做獨立 updater 套用更新，處理關閉主程式、替換 `_internal`、rollback 與重啟。
4. 若自製 updater 的維護成本變高，再評估 Velopack。

## 參考來源

- GitHub Release asset 與 latest release API：`https://docs.github.com/en/rest/releases`
- GitHub Release 管理概念：`https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases`
- GitHub CLI release upload：`https://cli.github.com/manual/gh_release_upload`
- Velopack Python getting started：`https://docs.velopack.io/getting-started/python`
- Velopack Windows packaging：`https://docs.velopack.io/packaging/operating-systems/windows`

## Release Artifact 約定

正式 Windows release asset 使用固定命名，讓 Web UI 與未來 updater 不需要猜測：

```text
facebook-monitor-{version}-windows-portable.zip
facebook-monitor-{version}-windows-portable.zip.sha256
```

若後續需要更穩定的查詢來源，可再新增 manifest：

```text
facebook-monitor-release.json
```

manifest 可包含：

- `version`
- `git_commit`
- `build_date`
- `packaging_mode`
- `asset_name`
- `asset_url`
- `sha256`
- `min_updater_version`
- `release_notes_url`
- `channel`，例如 `stable` 或 `preview`

`APP_VERSION`、Windows version resource、zip 檔名、GitHub tag、SHA256 檔與 manifest 必須一致。若其中任一項不同，release validation 應視為失敗。

## Phase 1：GitHub Release 更新檢查 UI

狀態：已落地。設定頁「程式更新」區塊只在使用者按下檢查時查 GitHub stable Release metadata；不下載、不解壓、不套用更新。使用者 UI 不提供 Preview / Stable 選擇，日常更新只走正式 release。

範圍：

- 在設定頁新增更新區塊，顯示目前版本、packaging mode、git commit、build date。
- 後端查 GitHub Release metadata，只判斷是否有可用 Windows portable zip，不下載檔案。
- 設定頁只顯示目前版本、最新版本、狀態摘要與必要操作按鈕；GitHub repo、asset 檔名、SHA256 檔名等細節不顯示給一般使用者。
- stable channel 固定只看 latest non-prerelease release；preview channel 保留在底層 service 測試與未來 debug 用，不暴露在一般設定頁。
- source mode 可顯示診斷資訊，但不把更新流程包裝成正式可用。

邊界：

- 不讀寫 profile、DB、cookies、tokens、secrets。
- 不在 scheduler、resident worker 或 scan pipeline 中自動檢查更新。
- 不把 GitHub release body 直接當 HTML 注入頁面；需要摘要時必須 escape。
- 離線、rate limit、asset 缺失、版本格式無法解析時，要顯示明確 reason，不可等同「沒有新版」。

驗證：

- unit test 覆蓋版本比較、stable / preview channel、asset 選擇與錯誤狀態。
- Web UI 測試覆蓋設定頁更新區塊。
- 使用 mocked GitHub response，不依賴真實網路。

## Phase 2：下載 + SHA256 驗證，不自動套用

狀態：已落地。設定頁在 Windows frozen / PyInstaller build 且 bundled updater 存在時，檢查到新版且 release asset 含 `.sha256` 才提供「下載更新」；後端會重新查 GitHub stable Release metadata，下載 zip 與 SHA256 到 `<data-dir>/updates/<version>/`，驗證通過後嘗試開啟下載資料夾。source mode 僅支援檢查更新。此階段仍不解壓、不覆蓋、不重啟。

範圍：

- Web UI 提供「下載更新」動作。
- 後端下載 release zip 到 runtime path resolver 管理的 `updates_dir`。
- 下載 `.sha256` 或讀 manifest 中的 `sha256`。
- 下載完成後計算實際 SHA256，與預期值比對。
- UI 顯示下載狀態、驗證狀態、expected / actual SHA256、檔案路徑與 failure reason。
- 驗證成功後只開啟下載資料夾或提示手動解壓，不在主程式內替換檔案。

邊界：

- 不接受任意 URL；只接受設定好的 GitHub repo release asset 或 trusted manifest。
- 驗證失敗的 zip 不可進入「可套用」狀態。
- 下載時檢查 `Content-Length` 與實際累計 bytes；超過上限的 zip / SHA256 asset 會中止並刪除 `.tmp` 半成品。
- 下載 cache 不可放在 `profiles/`、`logs/`、`exports/`。
- 不下載或保存 GitHub token；公開 release 應走無 token API。
- 不解壓到 app base dir，不覆蓋正在執行的 exe 或 `_internal`。

驗證：

- fixture zip 的 SHA256 pass / fail 測試。
- 下載中斷、HTTP error、asset 缺失、磁碟路徑不存在的錯誤分類。
- 確認 update cache 不包含 profile、DB、secrets、logs。

## Phase 3：獨立 Updater 套用更新

狀態：程式碼路徑已落地，並已補上第一輪安全邊界。已有 pending update handoff、source entrypoint `facebook-monitor-updater`、PyInstaller `facebook-monitor-updater.exe` 打包設定，以及 updater 核心：主程式關閉後重驗 SHA256、解壓 staging、驗證 onedir 結構、備份 app files、替換並保留 `data/`。Web UI 也可啟動 temp updater；Windows tray / launcher server path 會提供 shutdown hook，讓 updater 等待 app lock 釋放後套用。套用成功後會以 pending update 內的 data/db/profile/logs 路徑重啟新版 app。已完成非互動 frozen updater smoke；尚未完成 Web UI + tray 手動更新 UX smoke。

範圍：

- 新增獨立 updater，例如 `facebook-monitor-updater.exe`，與主程式一起打進 onedir。
- 主程式只負責下載、驗證與寫入 handoff file，例如 `<data-dir>/runtime/pending_update.json`。（已落地）
- updater 負責確認主程式已退出、再次驗證 SHA256、解壓 staging、驗證 staging 結構、備份舊版、替換 app files。（已落地）
- updater log、從 Web UI 啟動 temp updater、等待 app lock 釋放、launcher shutdown hook 與套用後重啟新版 app 已落地。
- pending update 會驗證 `zip_path` 必須在 `<data-dir>/updates/`、`runtime_dir` 必須是 `<data-dir>/runtime`、DB/profile 路徑必須留在 data tree 內，且 pending 檔本身必須位於對應 runtime dir。
- updater 套用前會驗證目前 app root 與 staging app root 都含必要 frozen onedir 檔案與 bundled updater，避免 pending 指向過寬或錯誤目錄。
- zip 解壓前會檢查 path traversal、entry 數量、單檔大小與展開後總量。
- `--restart` 遇到壞 pending 檔時會走一般失敗結果與 updater log，不會在正式錯誤處理前 crash。
- temp updater 必須複製 `facebook-monitor-updater.exe` 與同層 `_internal/`；PyInstaller onedir 的 updater 不能只複製單一 exe，否則會找不到 `python313.dll`。
- PyInstaller spec 必須明確把 runtime hooks 與對應 entry script 分別放進 `facebook-monitor.exe` / `facebook-monitor-updater.exe`，不可用錯誤的 `a.scripts[0]` / `a.scripts[1]` 假設。
- 非互動 frozen updater smoke 已通過：updater 從 temp onedir runtime 啟動，套用 staged zip，替換 app files，保留 `data/app.db` 與 profile marker，寫入 `updater.log status=applied`。
- Web UI + tray 手動更新 UX smoke 仍待補。

staging 必須至少驗證：

- `facebook-monitor.exe`
- `facebook-monitor-updater.exe`
- `_internal/browser/chrome.exe`
- `_internal/assets/facebook-monitor.ico`
- `_internal/assets/facebook-monitor-tray.ico`
- Web UI templates/static 存在

替換規則：

- updater 只處理 app files。
- 若使用者以 `--portable` 啟動，`app_base_dir / "data"` 可能含 DB/profile/logs，替換時必須保留。
- 不在主程式仍執行時 hot swap。
- instance lock 未釋放時，updater 應停止並提示使用者從 tray 完整退出。
- rollback directory 或備份策略必須先驗證可還原，再允許正式套用。

診斷：

- updater log 應寫到 `<logs-dir>/updater.log`。
- GUI subsystem 沒有 console；錯誤要能從 UI 或 log 讀到。
- pending update JSON 不可包含 secrets、cookies、tokens 或任意執行命令。

## Phase 4：Release / CI 驗證補強

範圍：

- release validation 增加 portable zip、SHA256、manifest schema 與 metadata 一致性檢查。
- frozen smoke 增加 mocked update check、fixture download、SHA256 pass / fail、staging 結構檢查。
- 若實作 updater，加入「不覆蓋 data dir」與「主程式未退出時拒絕替換」測試。
- frozen CI 尚未封口前，先把這些檢查放進本機 admin script。

## Velopack 評估門檻

先不要一開始導入 Velopack。滿足以下條件後再評估：

- portable zip + SHA256 下載流程至少跑過一輪真實 release。
- release asset 命名、版本 metadata 與 manifest 已穩定。
- 需要 delta update、標準 installer UX、rollback / channel 管理或 signed package workflow 至少兩項。
- 願意接受從目前 portable onedir zip 轉向 Velopack 包裝語義。
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

目前尚未完成的安全項目：

- 尚未加入 signed manifest / detached signature / Authenticode signer 驗證。若 GitHub release 發布權限被盜，攻擊者仍可能同時替換 zip 與 `.sha256`。
- 尚未做 Web UI + tray 手動更新 UX smoke；目前通過的是 source-mode unit / route 測試、updater core 測試與非互動 frozen updater smoke。
- 尚未建立舊版 backup 自動清理策略；更新 zip、staging 與 backup 仍可能累積，需要後續設定保留數量或保留天數。
- temp updater 目前會複製完整 `_internal/`，包含 bundled Chromium，較慢但安全；若後續想縮短等待時間，可評估把 updater 做成獨立 onefile 或 slim onedir。

## 明確不做

- 不做靜默背景更新。
- 不做主程式執行中 hot swap。
- 不做差分更新。
- 不做跨平台 updater。
- 不做任意 URL 或任意 zip 套用。
- 不把 updater 狀態混進 target configs、scheduler runtime、worker scan pipeline 或 notification outbox。
- 不為 updater 修改 Facebook DOM helper、posts/comments pipeline 或 notification dispatch。
- 不做自動 schema migration rollback；schema migration 仍由新版 app 正常啟動流程負責。
