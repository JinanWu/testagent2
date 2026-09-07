"""Sealed composition facade for PostgreSQL admin audit-before-detail."""
from __future__ import annotations

from .PostgreSQL查詢投影 import PostgreSQL呼叫查詢投影
from .PostgreSQL稽核 import PostgreSQL稽核服務
from .查詢投影 import 管理員原始資料稽核閘門

class PostgreSQL管理稽核提供者:
    __slots__ = ("_投影", "_稽核", "_閘門")
    def __init__(self, 投影: PostgreSQL呼叫查詢投影, 稽核: PostgreSQL稽核服務,
                 閘門: 管理員原始資料稽核閘門) -> None:
        if (type(投影) is not PostgreSQL呼叫查詢投影
                or type(稽核) is not PostgreSQL稽核服務
                or type(閘門) is not 管理員原始資料稽核閘門):
            raise ValueError("PostgreSQL管理稽核提供者無效") from None
        self._投影, self._稽核, self._閘門 = 投影, 稽核, 閘門
    def 列出管理員安全呼叫(self, 條件, 位置, /):
        return self._投影.列出管理員安全呼叫(條件, 位置)
    def 查詢管理員原始資料(self, *參數):
        return self._閘門.查詢管理員原始資料(*參數)
