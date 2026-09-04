"""Explicit PostgreSQL redaction command provider."""
from __future__ import annotations
from .PostgreSQL遮蔽 import PostgreSQL不可逆遮蔽服務

class PostgreSQL管理遮蔽提供者:
    __slots__ = ("_服務",)
    def __init__(self, service: PostgreSQL不可逆遮蔽服務) -> None:
        if type(service) is not PostgreSQL不可逆遮蔽服務:
            raise ValueError("PostgreSQL管理遮蔽提供者無效") from None
        self._服務 = service
    def 執行(self, 管理員授權: bool, 遮蔽識別碼: str, 稽核事件識別碼: str,
             操作者識別碼: str, 請求識別碼: str, 呼叫識別碼: str,
             目標類型: str, 目標列識別碼: str, JSON路徑: str, 原因: str,
             發生時間: int | float, /):
        return self._服務.遮蔽(
            管理員授權, 遮蔽識別碼, 稽核事件識別碼, 操作者識別碼,
            請求識別碼, 呼叫識別碼, 目標類型, 目標列識別碼, JSON路徑, 原因,
            發生時間,
        )
