"""零 I/O、可安全匯入的交易儲存設定值與驗證。

本模組只依賴標準函式庫；匯入時不讀環境、不解析檔案路徑，也不執行任何 I/O。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlsplit

支援的交易儲存後端 = ("sqlite", "bigquery", "postgres")


@dataclass(frozen=True, slots=True)
class 交易儲存設定:
    """不可變的交易儲存選擇與 PostgreSQL 連線／pool 純設定。

    ``資料庫URL`` 刻意排除於 repr；本 Block 只驗證設定，不建立 pool 或連線。
    """

    後端: str = "sqlite"
    資料庫URL: str | None = field(default=None, repr=False)
    CloudSQL連線名稱: str | None = None
    Pool最小連線數: int = 1
    Pool最大連線數: int = 5
    Pool等待秒數: int = 10

    def __post_init__(self) -> None:
        """驗證後端、Cloud SQL Unix socket DSN 與有界 pool 純設定。"""
        if self.後端 not in 支援的交易儲存後端:
            raise ValueError("交易儲存設定無效")
        if self.後端 != "postgres":
            return
        if not self.資料庫URL:
            raise ValueError("PostgreSQL 儲存設定無效：缺少 DATABASE_URL")
        if not self.CloudSQL連線名稱:
            raise ValueError("PostgreSQL 儲存設定無效：缺少 CLOUD_SQL_INSTANCE_CONNECTION_NAME")
        if not re.fullmatch(
            r"[a-z][a-z0-9-]{0,62}:[a-z][a-z0-9-]{0,62}:[a-z][a-z0-9-]{0,62}",
            self.CloudSQL連線名稱,
        ):
            raise ValueError("PostgreSQL 儲存設定無效")
        if (
            type(self.Pool最小連線數) is not int or not 0 <= self.Pool最小連線數 <= 20
            or type(self.Pool最大連線數) is not int or not 1 <= self.Pool最大連線數 <= 50
            or self.Pool最小連線數 > self.Pool最大連線數
            or type(self.Pool等待秒數) is not int or not 1 <= self.Pool等待秒數 <= 120
        ):
            raise ValueError("PostgreSQL 儲存設定無效")
        try:
            拆解 = urlsplit(self.資料庫URL)
            查詢配對 = parse_qsl(
                拆解.query,
                keep_blank_values=True,
                strict_parsing=True,
            )
            if (
                拆解.scheme not in {"postgres", "postgresql"}
                or 拆解.hostname is not None
                or 拆解.port is not None
                or "#" in self.資料庫URL
                or not 拆解.path.removeprefix("/")
                or 查詢配對 != [("host", f"/cloudsql/{self.CloudSQL連線名稱}")]
            ):
                raise ValueError
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Exception:
            raise ValueError("PostgreSQL 儲存設定無效") from None
