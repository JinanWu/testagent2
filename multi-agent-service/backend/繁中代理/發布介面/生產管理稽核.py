"""A18 canonical Admin logs lifespan provider與generation-safe proxy。"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from threading import Condition, RLock
import secrets
import time
import uuid

from starlette.concurrency import run_in_threadpool

from .治理.查詢投影 import SQLite呼叫查詢投影, 管理員原始資料稽核閘門
from .治理.稽核 import SQLite稽核服務
from .治理.管理遮蔽治理 import 管理遮蔽治理權限, _管理遮蔽安裝能力
from .治理.遮蔽 import SQLite不可逆遮蔽服務
from .治理.遮蔽命令 import SQLite遮蔽命令服務
from .治理.管理查詢契約 import 管理員呼叫游標編解碼器, 管理員拒絕稽核收據權威
from .路由.管理稽核 import 建立管理稽核路由器

_控制流程例外 = (asyncio.CancelledError, KeyboardInterrupt, SystemExit, GeneratorExit)


class 延遲管理稽核服務:
    """以generation-safe lease提供lifespan-owned Admin list/detail provider。"""

    def __init__(self) -> None:
        """建立未安裝slot；不開啟SQLite或執行I/O。"""
        self._條件 = Condition(RLock())
        self._服務 = None
        self._世代 = 0
        self._目前世代 = None
        self._進行中 = 0
        self._停止中 = False
        self._排空服務 = None
        self._排空世代 = None
        self._拒絕收據權威 = 管理員拒絕稽核收據權威(secrets.token_bytes(32))

    def 安裝(self, 服務) -> int:
        """安裝具list/detail exact methods的startup provider並回傳generation。"""
        if (not callable(getattr(服務, "列出管理員安全呼叫", None))
                or not callable(getattr(服務, "查詢管理員原始資料", None))):
            raise ValueError("Published管理稽核服務無效") from None
        with self._條件:
            if self._服務 is not None or self._進行中:
                raise ValueError("Published管理稽核服務無效") from None
            self._世代 += 1
            self._目前世代 = self._世代
            self._停止中 = False
            self._服務 = 服務
            return self._世代

    @contextmanager
    def _租借(self):
        """完整操作期間保有provider，startup前與draining後固定拒絕。"""
        with self._條件:
            服務 = self._服務
            if 服務 is None or self._停止中:
                raise RuntimeError("Published管理稽核服務不可用") from None
            self._進行中 += 1
        try:
            yield 服務
        finally:
            with self._條件:
                self._進行中 -= 1
                if self._進行中 == 0:
                    self._條件.notify_all()

    def 清除(self, 服務, 世代: int) -> None:
        """只撤銷同identity與generation，並等待所有active leases。"""
        with self._條件:
            if self._排空服務 is 服務 and self._排空世代 == 世代:
                while self._排空服務 is 服務 and self._排空世代 == 世代:
                    self._條件.wait()
                return
            if self._服務 is 服務 and self._目前世代 == 世代:
                self._停止中 = True
                self._排空服務 = 服務
                self._排空世代 = 世代
                self._服務 = None
                self._目前世代 = None
                while self._進行中:
                    self._條件.wait()
                self._排空服務 = None
                self._排空世代 = None
                self._停止中 = False
                self._條件.notify_all()

    def _撤銷已發布服務(self, 服務) -> None:
        """Installer失敗時依module-owned identity解析世代並完成rollback drain。"""
        with self._條件:
            世代 = self._目前世代 if self._服務 is 服務 else None
        if type(世代) is int:
            _可信清除管理稽核服務(self, 服務, 世代)

    def 列出管理員安全呼叫(self, 條件, 位置, /):
        """租借並委派safe-list provider。"""
        with self._租借() as 服務:
            return 服務.列出管理員安全呼叫(條件, 位置)

    def 查詢管理員原始資料(self, *參數):
        """租借並委派audit-before-detail provider。"""
        with self._租借() as 服務:
            return 服務.查詢管理員原始資料(*參數)


_可信清除管理稽核服務 = 延遲管理稽核服務.清除


class 管理稽核提供者:
    """組合同一Published DB的safe list projection與audited detail gate。"""

    def __init__(self, 資料庫路徑: Path, 收據權威: 管理員拒絕稽核收據權威 | None) -> None:
        """建立request-local SQLite adapters，不開啟持久連線。"""
        投影 = SQLite呼叫查詢投影(str(資料庫路徑))

        def 讀取詳情(端點識別碼: str, 呼叫識別碼: str):
            """讓audit gate只持有exact function seam。"""
            return 投影.查詢管理員原始資料(True, 端點識別碼, 呼叫識別碼)

        def 配對存在(端點識別碼: str, 呼叫識別碼: str):
            """在audit前只判定exact pairing，不讀取raw payload。"""
            return 投影.管理員呼叫配對存在(端點識別碼, 呼叫識別碼)

        self._投影 = 投影
        self._閘門 = 管理員原始資料稽核閘門(
            SQLite稽核服務(str(資料庫路徑)), 讀取詳情, 配對存在,
            收據權威,
        )

    def 列出管理員安全呼叫(self, 條件, 位置, /):
        """委派不讀raw JSON的safe projection。"""
        return self._投影.列出管理員安全呼叫(條件, 位置)

    def 查詢管理員原始資料(self, *參數):
        """委派committed audit-before-detail gate。"""
        return self._閘門.查詢管理員原始資料(*參數)


class 管理稽核組合資源:
    """先撤銷Admin provider，再關閉既有Published runtime。"""

    def __init__(self, 主資源, 代理: 延遲管理稽核服務, 服務: 管理稽核提供者, 世代: int) -> None:
        """保存同一次startup的主資源、proxy、provider與generation。"""
        self._主資源, self._代理, self._服務, self._世代 = 主資源, 代理, 服務, 世代

    def __getattr__(self, 名稱: str):
        """將既有Published resource介面透明委派給被包裝的主資源。"""
        return getattr(self._主資源, 名稱)

    async def 關閉(self) -> None:
        """撤銷查詢authority後關閉主資源；控制流程優先。"""
        控制流程錯誤 = 普通錯誤 = None
        try:
            await run_in_threadpool(self._代理.清除, self._服務, self._世代)
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外): 控制流程錯誤 = 錯誤
            else: 普通錯誤 = 錯誤
        try:
            if isinstance(self._代理, 延遲管理稽核服務):
                await run_in_threadpool(
                    _可信清除管理稽核服務, self._代理, self._服務, self._世代,
                )
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外):
                if 控制流程錯誤 is None: 控制流程錯誤 = 錯誤
            elif 普通錯誤 is None: 普通錯誤 = 錯誤
        try:
            await self._主資源.關閉()
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外):
                if 控制流程錯誤 is None: 控制流程錯誤 = 錯誤
            elif 普通錯誤 is None: 普通錯誤 = 錯誤
        self._代理 = self._服務 = self._世代 = None
        if 控制流程錯誤 is not None: raise 控制流程錯誤
        if 普通錯誤 is not None: raise 普通錯誤


def 建立管理稽核權限():
    """建立per-app proxy與cursor authority，建構期零I/O。"""
    return 延遲管理稽核服務(), 管理員呼叫游標編解碼器(secrets.token_bytes(32))


def 建立管理稽核路由(代理: 延遲管理稽核服務, 游標, 目前工作階段相依):
    """以canonical current-session dependency建立Admin router。"""
    return 建立管理稽核路由器(
        代理, 代理, 游標, 目前工作階段相依,
        拒絕收據權威=代理._拒絕收據權威,
    )


async def 安裝管理稽核資源(主資源, 代理: 延遲管理稽核服務, 資料庫路徑: Path):
    """在主Published resource成功後安裝Admin provider；失敗時關閉主資源。"""
    服務 = None
    try:
        收據權威 = getattr(代理, "_拒絕收據權威", None)
        服務 = 管理稽核提供者(資料庫路徑, 收據權威)
        世代 = 代理.安裝(服務)
        原始同步清理 = getattr(主資源, "_執行關閉同步", None)
        if callable(原始同步清理):
            def 清除含管理稽核() -> None:
                """先撤銷Admin authority，再盡力完成既有Published同步清理。"""
                控制流程錯誤 = 普通錯誤 = None
                try:
                    代理.清除(服務, 世代)
                except BaseException as 錯誤:
                    if isinstance(錯誤, _控制流程例外): 控制流程錯誤 = 錯誤
                    else: 普通錯誤 = 錯誤
                try:
                    _可信清除管理稽核服務(代理, 服務, 世代)
                except BaseException as 錯誤:
                    if isinstance(錯誤, _控制流程例外):
                        if 控制流程錯誤 is None: 控制流程錯誤 = 錯誤
                    elif 普通錯誤 is None: 普通錯誤 = 錯誤
                try:
                    原始同步清理()
                except BaseException as 錯誤:
                    if isinstance(錯誤, _控制流程例外):
                        if 控制流程錯誤 is None: 控制流程錯誤 = 錯誤
                    elif 普通錯誤 is None: 普通錯誤 = 錯誤
                if 控制流程錯誤 is not None: raise 控制流程錯誤
                if 普通錯誤 is not None: raise 普通錯誤

            主資源._執行關閉同步 = 清除含管理稽核
            return 主資源
        return 管理稽核組合資源(主資源, 代理, 服務, 世代)
    except BaseException as 啟動錯誤:
        清理控制流程錯誤 = 清理普通錯誤 = None
        try:
            if 服務 is not None and isinstance(代理, 延遲管理稽核服務):
                延遲管理稽核服務._撤銷已發布服務(代理, 服務)
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外): 清理控制流程錯誤 = 錯誤
            else: 清理普通錯誤 = 錯誤
        try:
            if 服務 is not None and isinstance(代理, 延遲管理稽核服務):
                _可信清除管理稽核服務(代理, 服務, 代理._目前世代)
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外):
                if 清理控制流程錯誤 is None: 清理控制流程錯誤 = 錯誤
            elif 清理普通錯誤 is None: 清理普通錯誤 = 錯誤
        try:
            await 主資源.關閉()
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外):
                if 清理控制流程錯誤 is None: 清理控制流程錯誤 = 錯誤
            elif 清理普通錯誤 is None: 清理普通錯誤 = 錯誤
        if isinstance(啟動錯誤, _控制流程例外): raise
        if 清理控制流程錯誤 is not None: raise 清理控制流程錯誤
        raise


class 管理遮蔽組合資源:
    """使A20 authority成為Published composition最外層shutdown owner。"""

    def __init__(self, 主資源, 權限, 嘗試, 世代) -> None:
        self._主資源, self._權限, self._嘗試, self._世代 = 主資源, 權限, 嘗試, 世代
        self._已關閉 = False

    def __getattr__(self, 名稱: str):
        return getattr(self._主資源, 名稱)

    async def 關閉(self) -> None:
        if self._已關閉:
            return
        self._已關閉 = True
        控制流程錯誤 = 普通錯誤 = None
        try:
            await run_in_threadpool(self._權限.解除, self._世代)
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外): 控制流程錯誤 = 錯誤
            else: 普通錯誤 = 錯誤
        try:
            await run_in_threadpool(
                _管理遮蔽安裝能力.依安裝嘗試撤銷並等待,
                self._權限, self._嘗試,
            )
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外):
                if 控制流程錯誤 is None: 控制流程錯誤 = 錯誤
            elif 普通錯誤 is None: 普通錯誤 = 錯誤
        try:
            await self._主資源.關閉()
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外):
                if 控制流程錯誤 is None: 控制流程錯誤 = 錯誤
            elif 普通錯誤 is None: 普通錯誤 = 錯誤
        self._權限 = self._嘗試 = self._世代 = None
        if 控制流程錯誤 is not None: raise 控制流程錯誤
        if 普通錯誤 is not None: raise 普通錯誤


async def 安裝管理遮蔽資源(主資源, 權限: 管理遮蔽治理權限, 資料庫路徑: Path):
    """在同一Published DB安裝A20 services，失敗時完整收斂並關閉內層資源。"""
    if type(權限) is not 管理遮蔽治理權限 or type(資料庫路徑) is not type(Path()):
        raise ValueError("管理遮蔽生產組裝無效") from None
    嘗試 = _管理遮蔽安裝能力.建立安裝嘗試(權限)
    try:
        服務 = SQLite不可逆遮蔽服務(str(資料庫路徑))
        命令 = SQLite遮蔽命令服務(
            遮蔽識別碼工廠=lambda: f"redaction-{uuid.uuid4().hex}",
            稽核事件識別碼工廠=lambda: f"audit-{uuid.uuid4().hex}",
            請求識別碼工廠=lambda: f"request-{uuid.uuid4().hex}",
            時鐘=time.time,
        )
        _管理遮蔽安裝能力.準備安裝(權限, 嘗試, 服務, 命令)
        世代 = _管理遮蔽安裝能力.發布已準備安裝(權限, 嘗試)
        原始同步清理 = getattr(主資源, "_執行關閉同步", None)
        if callable(原始同步清理):
            def 清除含管理遮蔽() -> None:
                """先撤銷A20 authority，再執行既有Published同步清理。"""
                控制流程錯誤 = 普通錯誤 = None
                for 清理 in (
                    lambda: 權限.解除(世代),
                    lambda: _管理遮蔽安裝能力.依安裝嘗試撤銷並等待(權限, 嘗試),
                    原始同步清理,
                ):
                    try:
                        清理()
                    except BaseException as 錯誤:
                        if isinstance(錯誤, _控制流程例外):
                            if 控制流程錯誤 is None: 控制流程錯誤 = 錯誤
                        elif 普通錯誤 is None: 普通錯誤 = 錯誤
                if 控制流程錯誤 is not None: raise 控制流程錯誤
                if 普通錯誤 is not None: raise 普通錯誤

            主資源._執行關閉同步 = 清除含管理遮蔽
            return 主資源
        return 管理遮蔽組合資源(主資源, 權限, 嘗試, 世代)
    except BaseException:
        try:
            await run_in_threadpool(_管理遮蔽安裝能力.依安裝嘗試撤銷並等待, 權限, 嘗試)
        finally:
            await 主資源.關閉()
        raise
