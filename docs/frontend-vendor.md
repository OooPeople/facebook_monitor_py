# Frontend Vendor Provenance

本文件記錄 Web UI 直接 vendored 的第三方前端檔案來源、版本、授權、checksum 與更新方式。只列 repository 內實際保存的第三方檔案；Web UI 呈現契約仍看 `docs/WEB_UI_CONTRACT.md`。

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
| Repository module SHA256 (LF checkout bytes) | `6C9B20F666B97A0D3577088EED380B9D2A522C61DAAC2BE464C8C13672A7B2F2` |
| Repository license SHA256 (LF checkout bytes) | `199071E94A4D6BA6F634ACD6020842EFC55161B9FB639A432C50DA687781219D` |

更新流程：

1. 從 upstream release 或 npm package 取得目標版本的 ESM bundle 與 license。
2. 覆蓋 `src/facebook_monitor/webapp/static/vendor/sortablejs/` 內對應檔案；不要手改 minified / bundled 內容。
3. 重新計算 SHA256，更新本文件。
4. 跑 Web UI static contract tests 與 JavaScript syntax check。
