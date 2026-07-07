"""儲存後端工廠。

功能：
    集中「要用哪一種儲存後端」的決定。上層一律呼叫 `建立工作階段庫` /
    `建立使用者庫`，不需要知道底層是本機 SQLite 還是雲端 BigQuery。切換只需
    設定環境變數（可寫在專案 `.env`）：

        STORAGE_BACKEND=sqlite    # 地端開發（預設）
        STORAGE_BACKEND=bigquery  # 雲端

    這樣「換儲存方式」不會動到原本的 runtime 架構——建立點以外的程式碼一律不變。

環境變數：
    STORAGE_BACKEND: sqlite 或 bigquery，未設定時預設 sqlite。
"""

from __future__ import annotations

import os
from pathlib import Path

from .工作階段庫 import 工作階段庫
from .使用者 import 使用者庫
from .管理部_bigquery import 載入本機環境檔

支援的後端 = ("sqlite", "bigquery")


def 取得儲存後端() -> str:
    """讀取目前儲存後端名稱。

    參數：無。
    返回值：str。小寫後端名稱；未設定時回傳 sqlite。
    """
    載入本機環境檔()
    後端 = (os.getenv("STORAGE_BACKEND") or "sqlite").strip().lower()
    if 後端 not in 支援的後端:
        raise ValueError(f"未知的 STORAGE_BACKEND：{後端}（僅支援 {', '.join(支援的後端)}）")
    return 後端


def 建立工作階段庫(資料庫路徑: str | Path):
    """依儲存後端建立工作階段庫。

    參數：
        資料庫路徑: SQLite 檔案路徑；bigquery 模式下仍用它放本機鎖等暫時性資料。

    返回值：工作階段庫實作物件（SQLite 或 BigQuery），方法介面一致。
    """
    if 取得儲存後端() == "bigquery":
        from .BigQuery工作階段庫 import BigQuery工作階段庫
        return BigQuery工作階段庫(資料庫路徑)
    return 工作階段庫(資料庫路徑)


def 建立使用者庫(資料庫路徑: str | Path):
    """依儲存後端建立使用者庫。

    參數：
        資料庫路徑: SQLite 檔案路徑；bigquery 模式下仍用它放本機暫時性資料。

    返回值：使用者庫實作物件（SQLite 或 BigQuery），方法介面一致。
    """
    if 取得儲存後端() == "bigquery":
        from .BigQuery使用者庫 import BigQuery使用者庫
        return BigQuery使用者庫(資料庫路徑)
    return 使用者庫(資料庫路徑)
