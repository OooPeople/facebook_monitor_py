# Git Commit 訊息規範

本專案的 Git commit message 採用 Conventional Commits 風格：

```text
<type>(<scope>): <summary>
```

若不需要 `scope`，可省略為：

```text
<type>: <summary>
```

## 語言規範

- `type` 一律使用英文小寫關鍵字。
- `scope` 使用英文或專案內一致的模組名稱。
- `summary`、`body`、`footer` 使用繁體中文。
- 不要在同一類模組中混用不一致的中英文命名。

## 撰寫原則

- `summary` 應簡短明確，直接描述這次提交做了什麼。
- 不要在 `summary` 結尾加句號。
- 避免模糊訊息，例如 `update`、`fix bug`、`modify code`。
- 一次 commit 盡量聚焦單一職責。

## 常用 type

- `feat`：新增功能或明確的功能性增強。
- `fix`：修復錯誤或不正確行為。
- `docs`：文件變更。
- `test`：新增、修改或修正測試。
- `refactor`：重構，不新增功能也不修 bug。
- `chore`：工具、設定或維護類變更。
- `build`：建置系統或依賴變更。
- `ci`：CI / workflow 相關變更。

## 建議 scope

- `docs`
- `phase0`
- `scripts`
- `browser`
- `worker`
- `config`
- `deps`

## 範例

```text
docs(readme): 補充 uv 初始化指令
chore(config): 新增 Python 版本固定檔
feat(phase0): 新增 headless worker probe
fix(worker): 修正 profile 不存在時的錯誤訊息
```
