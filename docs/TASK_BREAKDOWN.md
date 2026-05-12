# 任務狀態

本文件只記活狀態、下一步、風險與最近驗證。穩定架構事實看 `docs/ARCHITECTURE.md`；接手摘要看 `docs/HANDOFF.md`；歷史推導與已完成計畫看 `docs/archive/` 或 git history。

## 目前狀態

- Web UI 是正式日常入口；背景 scheduler / resident main 隨 Web UI 啟動。
- target 卡片「開始 / 停止」是日常主操作；posts / comments target 均可透過 Facebook URL 建立。
- target-scoped config / seen / latest scan / runtime / history 與 notification outbox boundary 已落地。
- posts/comments scan 走正式 resident main queue；Web UI scan-once 只排入 resident request，不直接啟動 browser scan。
- dashboard read model、target card、設定 modal、命中紀錄 modal、collapsed summary、revision event stream 與 batch partial update 已完成。
- posts/comments extractor 保留各自 DOM、permalink、sort、load-more 與 target scope 邏輯；大型 DOM evaluate script 已拆到專責 script 模組。
- 架構審查 hardening 已落地：Web UI mutating route 有 CSRF token，target kind/scope 由 DB unique index 保護，notification outbox 不再被 startup cleanup 清除，fresh schema 不再建立 `group_configs`，notification 失敗診斷不回填 endpoint。

## 近期完成摘要

- Runtime / launcher：package entrypoints、預設 data-dir、port fallback、自動開瀏覽器、single-instance / resource locks、startup diagnostics 與 rotating logs 已接上。
- Web UI：dashboard 模組化、theme DB preference、target card settings / rename / collapse、hit records modal、keyword highlight、dirty state 與 partial update 已接上。
- Target 設定：正式 config owner 已收斂為 `target_configs[target_id]`；新增 target 預設 stopped；keyword rule 使用 NFKC normalization；exclude ignore phrases 已接到 UI、config 與 worker finalize。
- Scheduler / metadata：scheduler running 時新增 target 不搶 profile；先建立 fallback target name，再交給 resident metadata refresh 補齊名稱。
- Notifications：desktop / ntfy / Discord target-scoped settings 與 outbox boundary 已落地；Discord webhook sender 已補 429 rate-limit 診斷與短 `Retry-After` 重試。
- 文件：文件職責重新收斂，README 作為 GitHub 專案首頁，`docs/USAGE.md` 承接詳細操作，ARCHITECTURE 保留穩定事實，TASK_BREAKDOWN 保留活狀態，HANDOFF 保留接手摘要，tooling 保留工具索引。
- Architecture hardening：schema 升到 v17；全域 scheduler start/stop route module 已移除；comments target 顯示名稱保留 parent post scope；admin/debug/internal scripts 改走正式 runtime path resolver；admin target manager 改用正式 `TargetConfigPatch` 並 redaction webhook 輸出；notification sender/outbox/manual test 錯誤訊息已安全化。
- Test maintenance：拆分 Web UI 首頁巨型測試；one-shot dispatch fallback 測試移到專責檔；清掉過期 phase / reserved notification 測試命名。
- Modularity review：keyword 文字解析移到 core helper；Web UI notification form 轉換集中到 DTO；dashboard CSRF header 與 modal dismiss 行為改走共用 JS helper；primary / danger / icon button variant 收斂為 `button--*`；Web UI notification sender 測試 helper 已抽出。
- 文件與測試整理：README 已改為快速理解專案用途與架構亮點的 GitHub 首頁；詳細操作移到 `docs/USAGE.md`；Web UI 首頁 rendering tests 拆到專責檔；permalink 與 sort diagnostics 已補 golden fixture tests。
- Secret storage：新增 `cryptography` Fernet secret codec；正式 `SqliteApplicationContext` 會在 repository boundary 加密 `target_configs`、`global_notification_settings` 與 `notification_outbox.endpoint` 內的 notification secrets，UI 與 application model 維持明文。
- Quality tooling：新增 dev dependencies `mypy`、`hypothesis`、`pytest-cov`；mypy 已覆蓋整個 `src/facebook_monitor` package、`scripts` 與完整 `tests`，Hypothesis 已補 keyword / dedupe property tests，coverage 先作報告不設硬門檻。
- Architecture review follow-up：stale `QUEUED` runtime recovery、`testserver` CSRF bypass 移除、`--profile-dir` path 收斂、notification after-commit 新 context dispatch、secret-bearing repository 明確 codec、admin ntfy topic redaction、CI workflow、`ruff` / `pip-audit` dev dependencies 已落地。

## 使用者已確認

- queue / running 顯示正常，未觀察到 stale queued / running。
- posts auto load more diagnostics 可判讀，且能穩定取得 10 篇貼文。
- 通知預設值與通知功能正常。
- `auto_adjust_sort` 功能正常。
- comments target 可用真實 Facebook 單篇貼文 URL 建立並掃描留言。
- Dashboard 更新策略維持短生命週期 revision event stream + batch partial update；不升級成真正長連線 SSE。

## 下一步

1. 跑本輪完整驗證：`pytest -q`、`mypy`、`ruff`、`compileall`、`pip-audit`、`git diff --check`。
2. 持續回歸 posts/comments extractor、通知內容與 dashboard partial update；遇到 JS 已有成熟語義的功能，先依 `docs/REFERENCE_MAP.md` 對照 `reference/src/facebook_group_refresh.user.js`。
3. 若 posts/comments extractor 繼續擴大，再拆 `feed_dom_*_script.py` / `comment_dom_*_script.py` 片段內部 helper；不要把 posts 與 comments DOM 邏輯硬合併。
4. 若要繼續提高測試品質，下一步評估是否針對核心 domain / persistence / worker 設 coverage gate；先不要對 Playwright-heavy path 設不切實際的全域門檻。

## 目前不做

- 不做多 profile orchestration。
- 不做 EXE 打包。
- 不搬 userscript 的頁內 panel UI。
- 不實作 top-item early-skip。
- 不宣稱 mutation relevance 已接上即時觸發；目前 Python resident main worker 仍是 polling。
- 不新增新的 `phase_*` script。
- 不保留 `scripts/start/webui.py` / `scripts/start/setup_login.py` 舊命令相容。
- 不把 posts/comments extractor、sort controls、scroll/load-more 細節硬抽成共用大函式。
- 不把 one-shot / sync resident fallback 包裝成正式產品 parity。
- 不把 dashboard revision stream 改成真正長連線 SSE。
- 不恢復 current-schema repair 平行路徑；既有 DB 欄位補齊一律走 `persistence/migrations.py`。
- 不把 notification outbox 改成獨立常駐 dispatcher。

## 主要風險

- Facebook 可能要求重新登入、checkpoint 或其他驗證。
- headless / headed DOM 可能不一致。
- selector / extractor 可能因 Facebook DOM 變動而不穩。
- resident worker、設定視窗、capture script 不能同時持有同一 automation profile；看到 `profile_locked` 時先找仍在執行的 Playwright context。
- DOM script 已拆成責任片段，但每個片段仍是 JS 字串；後續再提高可測性時，先補小粒度 helper 測試。
- notification topic / webhook 目前 UI 明文顯示且錯誤診斷已遮蔽；SQLite 內已加密保存，但 DB 與 `secrets.key` 同時外流時仍可解密。
- `--unsafe-profile-dir` 保留給 debug/test 外部 profile；使用時仍需避免把任何真實瀏覽器日常 profile 交給 automation。
- notification outbox 仍是 commit-after immediate dispatch，尚未拆成獨立常駐 background dispatcher。

## 驗證

常用完整驗證指令：

```powershell
.\scripts\uv.ps1 run pytest -q
.\scripts\uv.ps1 run mypy
.\scripts\uv.ps1 run pytest --cov=facebook_monitor --cov-report=term-missing -q
.\scripts\uv.ps1 run pytest tests\core --cov=facebook_monitor.core --cov-report=term-missing -q
.\scripts\uv.ps1 run python -m compileall -q src scripts tests
.\scripts\uv.ps1 run ruff check src scripts tests
.\scripts\uv.ps1 run pip-audit
git diff --check
```

最近完整驗證（2026-05-13）：

- `.\scripts\uv.ps1 run pytest -q`，`321 passed`
- `.\scripts\uv.ps1 run mypy`，通過，`196 source files`
- `.\scripts\uv.ps1 run pytest --cov=facebook_monitor --cov-report=term-missing -q`，`321 passed`，總 coverage `82%`，coverage mode 出現 3 個 SQLite ResourceWarning，測試通過
- `.\scripts\uv.ps1 run pytest tests\core --cov=facebook_monitor.core --cov-report=term-missing -q`，`24 passed`，core coverage report 可產生，總 coverage `82%`
- `.\scripts\uv.ps1 run python -m compileall -q src scripts tests`，通過
- `.\scripts\uv.ps1 run ruff check src scripts tests`，通過
- `.\scripts\uv.ps1 run pip-audit`，通過；本機 package 因未上 PyPI 被 skip，沒有已知漏洞
- `git diff --check`，通過，只有 CRLF warning

最近 focused 驗證：

- `.\scripts\uv.ps1 run pytest tests/runtime/test_paths.py tests/webapp/test_app.py::test_mutating_routes_require_csrf_token_for_loopback_host tests/webapp/test_app.py::test_mutating_routes_require_csrf_token_for_testserver_host tests/application/test_services.py::test_recover_stale_running_targets_marks_old_heartbeat_as_error tests/application/test_services.py::test_recover_stale_queued_targets_returns_to_idle_for_retry tests/scheduler/test_one_shot_loop.py::test_recover_stale_running_targets_marks_stale_target_error tests/scheduler/test_one_shot_loop.py::test_recover_stale_runtime_targets_requeues_stale_queued_target tests/persistence/test_secret_storage.py -q`，`16 passed`
- `.\scripts\uv.ps1 run pytest tests/cli/test_launcher_instance.py tests/cli/test_entrypoints.py -q`，`21 passed`
- `.\scripts\uv.ps1 run ruff check src scripts tests`，通過
- `.\scripts\uv.ps1 run mypy`，通過，`196 source files`
- `.\scripts\uv.ps1 run pytest tests/persistence/test_secret_storage.py -q`，`2 passed`
- `.\scripts\uv.ps1 run ruff check src tests`，通過
- `.\scripts\uv.ps1 run pytest tests/webapp/test_app.py tests/persistence/test_sqlite.py tests/persistence/test_migration_guards.py tests/cli/test_manage_targets.py tests/cli/test_entrypoints.py -q`，`83 passed`
- `.\scripts\uv.ps1 run pytest tests/notifications/test_ntfy.py tests/worker/test_scan_finalize.py tests/webapp/test_app.py -q`，`76 passed`
- `.\scripts\uv.ps1 run pytest tests/webapp/test_app.py tests/worker/test_posts_pipeline.py tests/worker/test_one_shot_dispatch.py tests/application/test_services.py tests/persistence/test_sqlite.py tests/scheduler/test_one_shot_loop.py tests/worker/test_sync_resident_fallback.py -q`，`132 passed`
- `.\scripts\uv.ps1 run pytest tests/webapp/test_static_dashboard_modules.py tests/webapp/test_app.py::test_settings_updates_tests_and_applies_global_notifications tests/webapp/test_app.py::test_target_settings_modal_can_test_notifications_without_saving tests/cli/test_manage_targets.py -q`，`16 passed`
- `.\scripts\uv.ps1 run pytest tests/webapp/test_dashboard_rendering.py tests/webapp/test_app.py tests/facebook/test_controls.py tests/facebook/test_feed_extractor.py -q`，`85 passed`
- `.\scripts\uv.ps1 run pytest tests\webapp\test_app.py tests\webapp\test_scheduler_session.py tests\helpers -q`，`57 passed`
- `.\scripts\uv.ps1 run pytest tests\core tests\facebook\test_comment_extractor.py tests\worker\test_scan_finalize.py tests\webapp\test_app.py tests\webapp\test_scheduler_session.py tests\cli\test_launcher_instance.py -q`，`115 passed`
- `.\scripts\uv.ps1 run ruff check src scripts tests`，通過
