"""Scan failure reason constants.

職責：集中跨 worker、scheduler 與 Web UI 共用的 scan failure reason，
避免不同層各自硬寫同一組狀態字串。
"""

CONTENT_UNAVAILABLE_REASON = "content_unavailable"

