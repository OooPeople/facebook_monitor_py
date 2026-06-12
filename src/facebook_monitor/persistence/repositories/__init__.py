"""SQLite repository modules。

職責：提供依 aggregate 分檔的 repository 匯入點。application wiring 與測試應
直接依賴具體 repository module，避免重新形成大型 persistence facade。
"""
