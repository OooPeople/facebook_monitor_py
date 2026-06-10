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
| Module SHA256 | `62375E4088A365131D262921A50367280717136598DE5F5C208137A919141A96` |
| License SHA256 | `E94DFC31E800D169257569DB270457C9F028440C9CCAE41E7EB78B2DB18F1298` |

更新流程：

1. 從 upstream release 或 npm package 取得目標版本的 ESM bundle 與 license。
2. 覆蓋 `src/facebook_monitor/webapp/static/vendor/sortablejs/` 內對應檔案；不要手改 minified / bundled 內容。
3. 重新計算 SHA256，更新本文件。
4. 跑 Web UI static contract tests 與 JavaScript syntax check。
