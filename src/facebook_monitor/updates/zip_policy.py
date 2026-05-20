"""Release/update zip 安全限制。

職責：集中 updater 解壓與 release artifact validation 共用的 zip 大小與
member 數量上限，避免不同入口保護力道漂移。
"""

from __future__ import annotations


MAX_ZIP_ENTRIES = 50_000
MAX_ZIP_SINGLE_FILE_BYTES = 1024 * 1024 * 1024
MAX_ZIP_UNCOMPRESSED_BYTES = 3 * 1024 * 1024 * 1024
MAX_ZIP_SYMLINK_TARGET_BYTES = 4096
