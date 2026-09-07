"""Canonical PostgreSQL lifespan owner: pool open, readiness, and shutdown."""
from __future__ import annotations

from starlette.concurrency import run_in_threadpool

from .. import PostgreSQL連線
from ..PostgreSQL健康 import 檢查PostgreSQL就緒
from ..交易儲存設定 import 交易儲存設定


class PostgreSQL資源:
    """The sole process resource owning the shared PostgreSQL pool."""
    def __init__(self, 設定: 交易儲存設定) -> None:
        if type(設定) is not 交易儲存設定 or 設定.後端 != "postgres":
            raise ValueError("PostgreSQL 資源設定無效")
        self.設定 = 設定
        self._已關閉 = False

    async def 關閉(self) -> None:
        if self._已關閉:
            return
        self._已關閉 = True
        await run_in_threadpool(PostgreSQL連線.關閉共用連線池)


async def 建立PostgreSQL資源(設定: 交易儲存設定) -> PostgreSQL資源:
    """Open one pool and verify SELECT 1 plus the exact Alembic head."""
    pool = await run_in_threadpool(PostgreSQL連線.啟動共用連線池, 設定)
    try:
        def check() -> None:
            with pool.connection() as connection:
                檢查PostgreSQL就緒(connection)
        await run_in_threadpool(check)
    except BaseException:
        await run_in_threadpool(PostgreSQL連線.關閉共用連線池)
        raise
    return PostgreSQL資源(設定)
