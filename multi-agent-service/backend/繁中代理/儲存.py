"""儲存後端工廠。

集中交易型儲存選擇。SQLite 為預設；BigQuery 保留既有相容模式；PostgreSQL 在
Block 1 只有完整設定與 global readiness gate，adapter 尚未接線時絕不 fallback。
"""

from __future__ import annotations

from pathlib import Path

from .工作階段庫 import 工作階段庫
from .使用者 import 使用者庫
from .交易儲存設定 import 支援的交易儲存後端, 交易儲存設定
from .環境設定 import 讀取交易儲存設定

支援的後端 = 支援的交易儲存後端


def 取得儲存後端() -> str:
    """由中央交易儲存設定讀取 exact backend；預設為 sqlite。"""
    return 讀取交易儲存設定().後端


def 建立工作階段庫(資料庫路徑: str | Path | None, 交易設定: 交易儲存設定 | None = None):
    """依交易儲存設定建立工作階段庫；未就緒 PostgreSQL 一律 fail closed。"""
    解析設定 = 交易設定 if 交易設定 is not None else 讀取交易儲存設定()
    後端 = 解析設定.後端
    if 後端 == "postgres":
        from .PostgreSQL工作階段庫 import PostgreSQL工作階段庫
        return PostgreSQL工作階段庫(解析設定)
    if 後端 == "bigquery":
        from .BigQuery工作階段庫 import BigQuery工作階段庫
        return BigQuery工作階段庫(資料庫路徑)
    return 工作階段庫(資料庫路徑)


def 建立使用者庫(資料庫路徑: str | Path | None, 交易設定: 交易儲存設定 | None = None):
    """依交易儲存設定建立使用者庫；未就緒 PostgreSQL 一律 fail closed。"""
    解析設定 = 交易設定 if 交易設定 is not None else 讀取交易儲存設定()
    後端 = 解析設定.後端
    if 後端 == "postgres":
        from .PostgreSQL使用者庫 import PostgreSQL使用者庫
        return PostgreSQL使用者庫(解析設定)
    if 後端 == "bigquery":
        from .BigQuery使用者庫 import BigQuery使用者庫
        return BigQuery使用者庫(資料庫路徑)
    from .發布介面.規劃.權限協調 import SQLite發布權限協調器
    return 使用者庫(資料庫路徑, SQLite發布權限協調器())
