"""工作階段識別碼傳遞工具。

功能：
    提供類似 Hermes gateway/session_context 的輕量 ContextVar，讓 runtime、工具、
    log 與子程序環境變數在 compression session split 後能指向同一個 active
    session id。
"""

from __future__ import annotations

import os
from contextvars import ContextVar

目前工作階段識別碼: ContextVar[str | None] = ContextVar("目前工作階段識別碼", default=None)
目前工作階段資料庫路徑: ContextVar[str | None] = ContextVar("目前工作階段資料庫路徑", default=None)


def 設定目前工作階段識別碼(工作階段識別碼: str | None) -> None:
    """設定目前執行脈絡與環境變數中的 session id。

    參數：
        工作階段識別碼: 目前 active session id；None 或空字串會清除環境變數。

    返回值：None。此函式會同時更新 ContextVar 與 HERMES_SESSION_ID。
    """
    目前工作階段識別碼.set(工作階段識別碼)
    if 工作階段識別碼:
        os.environ["HERMES_SESSION_ID"] = 工作階段識別碼
    else:
        os.environ.pop("HERMES_SESSION_ID", None)


def 讀取目前工作階段識別碼() -> str | None:
    """讀取目前 ContextVar 內的 session id。

    參數：無。
    返回值：str | None。目前 active session id；尚未設定時回傳 None。
    """
    return 目前工作階段識別碼.get()


def 設定目前工作階段資料庫路徑(資料庫路徑: str | None) -> None:
    """設定目前執行脈絡與環境變數中的 session DB 路徑。

    參數：
        資料庫路徑: SQLite DB 路徑；None 或空字串會清除環境變數。

    返回值：None。工具 handler 可用此值自動搜尋目前 runtime 的 session store。
    """
    目前工作階段資料庫路徑.set(資料庫路徑)
    if 資料庫路徑:
        os.environ["TESTAGENT2_SESSION_DB"] = 資料庫路徑
    else:
        os.environ.pop("TESTAGENT2_SESSION_DB", None)


def 讀取目前工作階段資料庫路徑() -> str | None:
    """讀取目前 ContextVar 內的 session DB 路徑。

    參數：無。
    返回值：str | None。目前 session store path；尚未設定時回傳 None。
    """
    return 目前工作階段資料庫路徑.get()
