# Secret Storage

本文件記錄 notification secrets 的目前保存語義。

## 目前狀態

Web UI 會刻意以明文欄位顯示 ntfy topic 與 Discord webhook。這是日常操作需求：使用者需要能確認自己輸入的值是否正確。

SQLite 內的 notification secrets 已做 DB-at-rest 加密。application、worker 與 Web UI 使用的 domain model 仍維持明文；加解密只發生在 persistence boundary。

目前加密欄位：

- `target_configs.ntfy_topic`
- `target_configs.discord_webhook`
- `global_notification_settings.ntfy_topic`
- `global_notification_settings.discord_webhook`
- `notification_outbox.endpoint`

## 實作方式

- 使用 `cryptography` 的 Fernet authenticated encryption。
- 密文以 `enc:v1:` prefix 保存，讓 repository 能辨識新密文與舊版 plaintext rows。
- local encryption key 放在 DB 同層的 `secrets.key`，例如預設路徑是 `~/facebook_monitor_data/secrets.key`。
- `SqliteApplicationContext` 會依 DB 路徑載入或建立 key，正式 Web UI、worker 與 CLI 入口會共用同一套加解密行為。
- 會保存 secret 的 repositories 不提供隱性明文預設；測試或 legacy migration 若需要明文資料，必須明確傳入 `PlaintextSecretCodec`。
- 舊版 plaintext rows 仍可讀回；正常重新保存 target config / global notification settings 時會改寫成密文。既有 outbox row 不會只因讀取而改寫，新的 outbox endpoint 會以密文寫入。

## 安全邊界

- UI 顯示明文是刻意產品語義，不代表 DB 也保存明文。
- DB 檔案單獨外流時，notification topic / webhook 不再直接裸露。
- DB 與 `secrets.key` 同時外流時仍可解密 secrets；這是本機 app 加密的合理邊界，不是硬體保護或 OS keychain 等級的保護。
- runtime diagnostics、sender exception、manual test error、notification event message 與 outbox error 不得暴露 endpoint / token。這些路徑應持續使用安全化錯誤訊息。

## 不做事項

- 不預設隱藏 Web UI 內的值；這已確認會傷害日常操作。
- 不用可逆 obfuscation 冒充加密。
- 不在本 repo 自製 cryptography。
