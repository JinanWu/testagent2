"""A19 lifespan-owned Owner 觀測 provider 與 generation-safe proxy。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import Condition, RLock

from starlette.concurrency import run_in_threadpool

from .治理.觀測供應器 import SQLite端點觀測查詢服務
from .路由.Owner觀測 import 建立Owner觀測路由器

_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)


class 延遲Owner觀測服務:
    """以 generation-safe lease 委派 lifespan-owned SQLite 觀測服務。"""

    def __init__(self) -> None:
        """建立未安裝的generation-safe服務slot；不執行I/O。"""
        self._條件 = Condition(RLock())
        self._服務 = self._排空服務 = None
        self._世代 = 0
        self._目前世代 = self._排空世代 = None
        self._進行中 = 0
        self._停止中 = False

    def 安裝(self, 服務: SQLite端點觀測查詢服務) -> int:
        """安裝exact startup provider並回傳新generation。"""
        if type(服務) is not SQLite端點觀測查詢服務:
            raise ValueError("Published Owner觀測服務無效") from None
        with self._條件:
            if self._服務 is not None or self._進行中:
                raise ValueError("Published Owner觀測服務無效") from None
            self._世代 += 1
            self._目前世代 = self._世代
            self._停止中 = False
            self._服務 = 服務
            return self._世代

    @contextmanager
    def _租借(self):
        """在完整操作期間保有provider並拒絕draining後的新租借。"""
        with self._條件:
            服務 = self._服務
            if 服務 is None or self._停止中:
                raise RuntimeError("Published Owner觀測服務不可用") from None
            self._進行中 += 1
        try:
            yield 服務
        finally:
            with self._條件:
                self._進行中 -= 1
                if self._進行中 == 0:
                    self._條件.notify_all()

    def 清除(self, 服務: SQLite端點觀測查詢服務, 世代: int) -> None:
        """只撤銷相同identity與generation並等待active leases。"""
        with self._條件:
            if self._排空服務 is 服務 and self._排空世代 == 世代:
                while self._排空服務 is 服務 and self._排空世代 == 世代:
                    self._條件.wait()
                return
            if self._服務 is 服務 and self._目前世代 == 世代:
                self._停止中 = True
                self._排空服務, self._排空世代 = 服務, 世代
                self._服務 = self._目前世代 = None
                while self._進行中:
                    self._條件.wait()
                self._排空服務 = self._排空世代 = None
                self._停止中 = False
                self._條件.notify_all()

    def _撤銷已發布服務(self, 服務: SQLite端點觀測查詢服務) -> None:
        """startup partial failure時解析目前generation並完成撤銷。"""
        with self._條件:
            世代 = self._目前世代 if self._服務 is 服務 else None
        if type(世代) is int:
            _可信清除Owner觀測(self, 服務, 世代)

    def 讀取端點指標(self, **參數):
        """租借並委派owner metrics provider。"""
        with self._租借() as 服務:
            return 服務.讀取端點指標(**參數)

    def 列出端點診斷(self, **參數):
        """租借並委派owner diagnostics provider。"""
        with self._租借() as 服務:
            return 服務.列出端點診斷(**參數)


_可信清除Owner觀測 = 延遲Owner觀測服務.清除


def 建立Owner觀測路由(代理: 延遲Owner觀測服務, 目前工作階段相依):
    """以 canonical session dependency 建立固定 Owner routes。"""
    return 建立Owner觀測路由器(代理, 目前工作階段相依)


async def 安裝Owner觀測資源(主資源, 代理: 延遲Owner觀測服務, 資料庫路徑: Path, 游標金鑰: bytes):
    """主 Published startup 成功後安裝 request-local provider，並接入唯一 shutdown owner。"""
    服務 = None
    try:
        服務 = SQLite端點觀測查詢服務(str(資料庫路徑), 游標簽章金鑰=游標金鑰)
        世代 = 代理.安裝(服務)
        原始同步清理 = getattr(主資源, "_執行關閉同步", None)
        if not callable(原始同步清理):
            raise ValueError("Published Owner觀測資源無效") from None

        def 清除含Owner觀測() -> None:
            """先撤銷Owner authority，再執行既有Published同步清理。"""
            控制流程錯誤 = 普通錯誤 = None
            for 操作 in (
                lambda: 代理.清除(服務, 世代),
                lambda: _可信清除Owner觀測(代理, 服務, 世代),
                原始同步清理,
            ):
                try:
                    操作()
                except BaseException as 錯誤:
                    if isinstance(錯誤, _控制流程例外):
                        if 控制流程錯誤 is None:
                            控制流程錯誤 = 錯誤
                    elif 普通錯誤 is None:
                        普通錯誤 = 錯誤
            if 控制流程錯誤 is not None:
                raise 控制流程錯誤
            if 普通錯誤 is not None:
                raise 普通錯誤

        主資源._執行關閉同步 = 清除含Owner觀測
        return 主資源
    except BaseException as 啟動錯誤:
        清理控制 = None
        try:
            if 服務 is not None and isinstance(代理, 延遲Owner觀測服務):
                延遲Owner觀測服務._撤銷已發布服務(代理, 服務)
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外):
                清理控制 = 錯誤
        try:
            await 主資源.關閉()
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外) and 清理控制 is None:
                清理控制 = 錯誤
        if isinstance(啟動錯誤, _控制流程例外):
            raise
        if 清理控制 is not None:
            raise 清理控制
        raise


__all__ = ("延遲Owner觀測服務", "建立Owner觀測路由", "安裝Owner觀測資源")
