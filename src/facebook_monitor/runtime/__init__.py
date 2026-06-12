"""Runtime package marker。

職責：讓呼叫端直接依賴 `runtime.paths`、`runtime.instance_lock` 等明確模組，
避免 package-level re-export 形成第二組 public surface。
"""
