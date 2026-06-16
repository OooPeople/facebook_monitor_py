# Frontend Vendor Provenance

本文件記錄 Web UI 直接 vendored 的第三方前端檔案來源、版本、授權與更新方式。機器可驗證的 checksum 主來源是 `src/facebook_monitor/webapp/static/vendor/frontend-vendor.manifest.json`；release validation 會讀該 manifest 驗證 repository 內實際保存的第三方檔案。Web UI 呈現契約仍看 `docs/WEB_UI_CONTRACT.md`。

## SortableJS

| 項目 | 內容 |
|---|---|
| 用途 | Sidebar target / group drag sorting |
| Upstream | <https://github.com/SortableJS/Sortable> |
| Package | `sortablejs` |
| Version | `1.15.6` |
| License | MIT |
| Local modifications | None |
| Vendored module | `src/facebook_monitor/webapp/static/vendor/sortablejs/sortable.esm.js` |
| Vendored license | `src/facebook_monitor/webapp/static/vendor/sortablejs/LICENSE` |
| Checksum source | `src/facebook_monitor/webapp/static/vendor/frontend-vendor.manifest.json` |

更新流程：

1. 從 upstream release 或 npm package 取得目標版本的 ESM bundle 與 license。
2. 覆蓋 `src/facebook_monitor/webapp/static/vendor/sortablejs/` 內對應檔案；不要手改 minified / bundled 內容。
3. 重新計算 checkout bytes SHA256，更新 `src/facebook_monitor/webapp/static/vendor/frontend-vendor.manifest.json`。
4. 跑 `scripts/admin/check_frontend_vendor_manifest.py`、Web UI static contract tests 與 JavaScript syntax check。
