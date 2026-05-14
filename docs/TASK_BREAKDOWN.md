# 任務狀態

本文件只記活狀態、下一步、風險與最近驗證。穩定架構事實看 `docs/ARCHITECTURE.md`；操作方式看 `docs/USAGE.md`；接手摘要看 `docs/HANDOFF.md`；歷史推導看 `docs/archive/` 或 git history。

## 目前狀態

- Web UI 是正式日常入口；背景 scheduler / resident main 隨 Web UI 啟動。
- target 卡片「開始 / 停止」是日常主操作；posts / comments target 均可透過 Facebook URL 建立。
- target-scoped config、seen、latest scan、runtime、history 與 notification outbox boundary 已落地。
- posts/comments scan 走正式 resident main queue；Web UI scan-once 只排入 resident request。
- dashboard read model、target card、設定 modal、命中紀錄 modal、collapsed summary、revision event stream 與 batch partial update 已完成。
- sidebar group、排序與 group template 已接上；layout 保存走 application service 單一 transaction，read model 不寫入 placement。
- group template 只在使用者明確套用時批次複製到 `target_configs[target_id]`，不作為 target config fallback owner。
- Web UI 確認/輸入彈窗與內容型 modal 已收斂為共用 helper；不使用瀏覽器原生 `confirm/prompt/alert`。
- posts/comments extractor 保留各自 DOM、permalink、sort、load-more 與 target scope 邏輯。
- comments extractor 已加入非破壞性 DOM settle 觀察；settle 失敗只寫入診斷，不阻斷原本留言抽取。
- posts feed 已加入保守 seen-stop：僅在排序確認為「新貼文」時，從最上方開始連續 4 篇 seen 即停止深度掃描。
- seen-stop 提早停止時，最近掃描 snapshot 會沿用上一輪項目補足可檢視清單，但 scan 診斷仍保留本輪實際掃描數。
- keyword matcher 使用 compiled Aho-Corasick 多模式比對；同一內容命中多組 include keywords 時會全部保存並高亮。
- Web UI mutating route 有 CSRF token，target kind/scope 由 DB unique index 保護，notification 失敗診斷不回填 endpoint。
- target metadata refresh 已有 pending / resolved / failed 狀態；新增 target 時可先顯示抓取中，成功後回填名稱，失敗後提示手動改名。

## 近期重點

- Scheduler 執行中新增 target 不搶 profile；DB 會記錄 pending metadata job，由 resident metadata refresh 消化。
- Secret storage 已接上 `cryptography` Fernet；SQLite 內加密保存 notification secrets，UI 與 application model 維持明文。
- Quality tooling 已接上 `mypy`、`hypothesis`、`pytest-cov`、`ruff`、`pip-audit` 與 GitHub Actions CI。
- 最近通知摘要可依通道顯示各自最新狀態，沿用既有 outbox / notification_events，不新增直接 dispatch path。
- dashboard row 與 sidebar 順序依 sidebar placement 顯示；`TargetRepository.list_all()` 維持 created_at 語義，scheduler 掃描順序不受 UI layout 影響。
- Web UI 控制圖示已收斂為 inline SVG；按鈕外觀以共用 button modifier 為基礎，局部 class 只保留尺寸、位置或狀態差異。
- sidebar 排序模式使用 vendored SortableJS；拖曳把手只在排序模式顯示，確認後才保存。
- Web UI 預設啟動重置 runtime data 時會清除可重建 scan/debug 資料與 `seen_items`，但保留 `notification_outbox`。
- target 卡片「開始」會清該 target 的 `seen_items` scope 與 `notification_outbox` 去重 rows，確保停止後再開始可重新通知同一命中。
- 文件職責已收斂：README 作為專案首頁，`USAGE` 承接操作，`ARCHITECTURE` 保留穩定語義，`TASK_BREAKDOWN` 只保留活狀態。

## 使用者已確認

- queue / running 顯示正常，未觀察到 stale queued / running。
- posts auto load more diagnostics 可判讀，且能穩定取得 10 篇貼文。
- 通知預設值與通知功能正常。
- `auto_adjust_sort` 功能正常。
- comments target 可用真實 Facebook 單篇貼文 URL 建立並掃描留言。
- Dashboard 更新策略維持短生命週期 revision event stream + batch partial update；不升級成真正長連線 SSE。
- sidebar group 與排序功能目前實測正常；SortableJS 的交換門檻受套件 hit-testing 行為限制，暫時維持現狀。

## 下一步

1. 回歸 GitHub Actions：`pytest -q`、`mypy`、`ruff`、`pip-audit`。
2. 以瀏覽器實測 sidebar 排序模式、group CRUD、group template 儲存與分區套用。
3. 持續回歸 posts/comments extractor、通知內容與 dashboard partial update。
4. 遇到 JS 已有成熟語義的功能，先依 `docs/REFERENCE_MAP.md` 對照 `reference/src/facebook_group_refresh.user.js`。
5. 若 posts/comments extractor 繼續擴大，再拆 DOM script 片段內部 helper；不要把 posts 與 comments DOM 邏輯硬合併。

## 目前不做

- 不做多 profile orchestration。
- 不做 EXE 打包。
- 不搬 userscript 的頁內 panel UI。
- 不實作 top-item early-skip。
- 不把置頂/管理員貼文情境下可能漏掃的 top-item shortcut 移植到 Python 版。
- 內建操作說明小視窗暫緩；目前只保留排除字忽略片語旁的 `?` 說明。
- 不宣稱 mutation relevance 已接上即時觸發；目前 Python resident main worker 仍是 polling。
- 不新增新的 `phase_*` script。
- 不把 one-shot / sync resident fallback 包裝成正式產品 parity。
- 不把 notification outbox 改成獨立常駐 dispatcher。

## 主要風險

- Facebook 可能要求重新登入、checkpoint 或其他驗證。
- headless / headed DOM 可能不一致。
- selector / extractor 可能因 Facebook DOM 變動而不穩。
- resident worker、設定視窗、debug tool 不能同時持有同一 automation profile。
- notification topic / webhook 在 UI 明文顯示；SQLite 內已加密保存，但 DB 與 `secrets.key` 同時外流時仍可解密。
- notification outbox 仍是 commit-after immediate dispatch，尚未拆成獨立常駐 background dispatcher。
- sidebar group template 是破壞性批次覆蓋操作；目前以前端確認視窗與 application transaction 防止誤套用與半套用。

## 驗證

常用完整驗證指令：

```powershell
.\scripts\uv.ps1 run pytest -q
.\scripts\uv.ps1 run mypy
.\scripts\uv.ps1 run python -m compileall -q src scripts tests
.\scripts\uv.ps1 run ruff check src scripts tests
.\scripts\uv.ps1 run pip-audit
git diff --check
```

最近驗證（2026-05-14）：

- `.\scripts\uv.ps1 run pytest tests/application/test_sidebar_layout.py tests/webapp/test_static_dashboard_modules.py tests/webapp/test_app.py -q`，`97 passed`
- `.\scripts\uv.ps1 run pytest tests/persistence/test_sqlite.py -q`，`22 passed`
- `.\scripts\uv.ps1 run ruff check src/facebook_monitor/application/sidebar_layout_service.py src/facebook_monitor/webapp/query_service.py src/facebook_monitor/webapp/routes/sidebar.py tests/application/test_sidebar_layout.py tests/webapp/test_app.py tests/webapp/test_static_dashboard_modules.py`，通過
- `node --check`：`api.js`、`dialogs.js`、`main.js`、`sidebar_layout.js`、`sidebar_sorting.js`，通過
