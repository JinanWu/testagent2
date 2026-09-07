"""Published domain 的 PostgreSQL transaction boundary。

所有 repository 都只透過 :func:`交易連線` 取得 psycopg 連線；建構時凍結並
重新驗證 PostgreSQL 設定，匯入與建構皆不開啟連線。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from ..PostgreSQL連線 import 交易連線
from ..環境設定 import 交易儲存設定


class PostgreSQL工作單元錯誤(RuntimeError):
    """工作單元設定或使用方式不符合固定契約。"""


def 凍結PostgreSQL設定(設定: 交易儲存設定) -> 交易儲存設定:
    """複製 exact built-in 欄位，避免 repository 信任可被竄改的 frozen instance。"""
    if type(設定) is not 交易儲存設定:
        raise PostgreSQL工作單元錯誤("PostgreSQL 工作單元設定無效") from None
    try:
        凍結 = 交易儲存設定(
            設定.後端, 設定.資料庫URL, 設定.CloudSQL連線名稱,
            設定.Pool最小連線數, 設定.Pool最大連線數, 設定.Pool等待秒數,
        )
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except Exception:
        raise PostgreSQL工作單元錯誤("PostgreSQL 工作單元設定無效") from None
    if 凍結.後端 != "postgres":
        raise PostgreSQL工作單元錯誤("PostgreSQL 工作單元設定無效") from None
    return 凍結


class PostgreSQL工作單元:
    """可重用且無連線狀態的工作單元工廠。"""

    __slots__ = ("_設定",)

    def __init__(self, 設定: 交易儲存設定) -> None:
        self._設定 = 凍結PostgreSQL設定(設定)

    @property
    def 設定(self) -> 交易儲存設定:
        return self._設定

    @contextmanager
    def 交易(self) -> Iterator[Any]:
        """借用一條由共用 pool 管理且自動 commit/rollback 的交易連線。"""
        with 交易連線(self._設定) as 連線:
            yield 連線

    def __enter__(self) -> Any:
        raise PostgreSQL工作單元錯誤("請使用 PostgreSQL工作單元.交易()")

    def __exit__(self, *_: object) -> None:
        return None
