"""以 endpoint_invocations.request_id 提供 PostgreSQL 冪等狀態投影。"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from 繁中代理.PostgreSQL連線 import 交易連線
from ..嚴格JSON import 建立正規JSON, 解析嚴格JSON
from .儲存庫 import 呼叫儲存錯誤


class 冪等狀態(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PostgreSQL冪等結果:
    狀態: 冪等狀態
    呼叫識別碼: str | None
    output_json: str | None = None
    error_json: str | None = None


class PostgreSQL呼叫冪等儲存庫:
    """只以現行 endpoint_invocations 表判定 request 的耐久狀態。"""
    __slots__ = ("_設定",)

    def __init__(self, 凍結設定: object) -> None:
        self._設定 = 凍結設定

    def 取得(self, request_id: str) -> PostgreSQL冪等結果:
        if type(request_id) is not str or not request_id.strip():
            raise 呼叫儲存錯誤("冪等狀態無法取得") from None
        try:
            with 交易連線(self._設定) as 連線:
                列 = 連線.execute(
                    "SELECT id,status,output,error FROM endpoint_invocations WHERE request_id=%s",
                    (request_id,)).fetchone()
            if 列 is None:
                return PostgreSQL冪等結果(冪等狀態.UNKNOWN, None)
            列 = _正規列(列, ("id", "status", "output", "error"))
            if type(列[0]) is not str:
                raise ValueError
            狀態 = _轉狀態(列[1])
            return PostgreSQL冪等結果(狀態, 列[0], _JSON(列[2]), _JSON(列[3]))
        except (KeyboardInterrupt, SystemExit, GeneratorExit): raise
        except BaseException:
            return PostgreSQL冪等結果(冪等狀態.UNKNOWN, None)

    def 鎖定取得(self, 連線: Any, request_id: str) -> PostgreSQL冪等結果:
        """供較大 caller-owned transaction 使用 SELECT FOR UPDATE。"""
        try:
            if type(request_id) is not str or not request_id.strip(): raise ValueError
            列 = 連線.execute(
                "SELECT id,status,output,error FROM endpoint_invocations "
                "WHERE request_id=%s FOR UPDATE", (request_id,)).fetchone()
            if 列 is None: return PostgreSQL冪等結果(冪等狀態.UNKNOWN, None)
            列 = _正規列(列, ("id", "status", "output", "error"))
            if type(列[0]) is not str:
                raise ValueError
            return PostgreSQL冪等結果(_轉狀態(列[1]), 列[0], _JSON(列[2]), _JSON(列[3]))
        except (KeyboardInterrupt, SystemExit, GeneratorExit): raise
        except BaseException: raise 呼叫儲存錯誤("冪等狀態無法取得") from None


def _正規列(列: object, 欄名: tuple[str, ...]) -> tuple[Any, ...]:
    if isinstance(列, Mapping):
        if set(列) != set(欄名): raise ValueError
        return tuple(列[名稱] for 名稱 in 欄名)
    if type(列) is tuple and len(列) == len(欄名): return 列
    raise ValueError


def _轉狀態(狀態: object) -> 冪等狀態:
    if 狀態 == "pending": return 冪等狀態.PENDING
    if 狀態 == "running": return 冪等狀態.RUNNING
    if 狀態 == "succeeded": return 冪等狀態.SUCCEEDED
    if 狀態 in ("failed", "rate_limited", "invalid_api_key"): return 冪等狀態.FAILED
    return 冪等狀態.UNKNOWN


def _JSON(值: object) -> str | None:
    if 值 is None:return None
    return 建立正規JSON(解析嚴格JSON(值) if type(值) is str else 值)
