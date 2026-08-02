"""CP3 Web Agent 的 production Chat／session／skills composition。

本模組只在 lifespan resource factory 中建立 SQLite repositories 與 runtime；
匯入模組、建立 router 或建立 FastAPI app 都不會建立資料庫。
"""

from __future__ import annotations

from contextlib import contextmanager
from threading import Condition, RLock
from typing import cast

from starlette.concurrency import run_in_threadpool

from .Web代理服務 import Web代理服務, Web服務不可用, Web執行階段, Web工作階段庫
from .相依項 import 發布介面相依項
from .設定 import 生產設定
from .資料庫 import 初始化發布介面資料庫
from .路由.聊天 import 建立聊天路由器
from .路由.工作階段 import 建立工作階段路由器
from .路由.技能 import 建立技能路由器
from 繁中代理.代理執行階段 import 代理執行階段
from 繁中代理.使用者 import 使用者庫
from 繁中代理.工作階段庫 import 工作階段庫
from 繁中代理.模型供應商 import GeminiADC供應商, 假模型供應商

_允許模型供應器 = frozenset({"fake", "gemini-adc"})



class 延遲Web代理服務:
    """讓routes在app construction固定identity，並於lifespan後取得真實服務。"""

    def __init__(self) -> None:
        """建立尚未啟動且每個app獨立的服務slot。"""
        self._鎖 = RLock()
        self._條件 = Condition(self._鎖)
        self._服務: Web代理服務 | None = None
        self._進行中 = 0
        self._正在停止 = False

    def 安裝(self, 服務: Web代理服務) -> None:
        """在startup exact-once安裝真實服務。

        參數：
            服務: 已完成repositories與runtime factory組裝的Web代理服務。
        返回值：None。
        例外：slot已安裝或型別不符時拋ValueError。
        副作用：後續HTTP handlers可取得此服務。
        """
        if type(服務) is not Web代理服務:
            raise ValueError("Web代理生產組裝無效")
        with self._鎖:
            if self._服務 is not None or self._進行中:
                raise ValueError("Web代理生產組裝無效")
            self._正在停止 = False
            self._服務 = 服務

    def 清除(self, 服務: Web代理服務) -> None:
        """只清除仍對應本次resource的服務identity。"""
        with self._條件:
            if self._服務 is 服務:
                self._服務 = None
                self._正在停止 = True
                while self._進行中:
                    self._條件.wait()

    @contextmanager
    def _租借服務(self):
        """在完整委派期間持有active lease，draining後拒絕新呼叫。"""
        with self._條件:
            服務 = self._服務
            if 服務 is None or self._正在停止:
                raise Web服務不可用
            self._進行中 += 1
        try:
            yield 服務
        finally:
            with self._條件:
                self._進行中 -= 1
                if not self._進行中:
                    self._條件.notify_all()

    def 聊天(self, 使用者識別碼: str, 訊息: str, 工作階段識別碼: str | None = None):
        """委派聊天；不在proxy保存request state。"""
        with self._租借服務() as 服務:
            return 服務.聊天(使用者識別碼, 訊息, 工作階段識別碼)

    def 列出工作階段(self, 使用者識別碼: str, 數量上限: int = 20):
        """委派工作階段列表。"""
        with self._租借服務() as 服務:
            return 服務.列出工作階段(使用者識別碼, 數量上限)

    def 讀取工作階段(self, 使用者識別碼: str, 根工作階段識別碼: str):
        """委派工作階段詳情。"""
        with self._租借服務() as 服務:
            return 服務.讀取工作階段(使用者識別碼, 根工作階段識別碼)

    def 列出技能(self, 使用者識別碼: str):
        """委派技能列表。"""
        with self._租借服務() as 服務:
            return 服務.列出技能(使用者識別碼)

    def 讀取技能(self, 使用者識別碼: str, 技能識別碼: str):
        """委派技能詳情。"""
        with self._租借服務() as 服務:
            return 服務.讀取技能(使用者識別碼, 技能識別碼)


class 生產Web代理資源:
    """擁有兩個SQLite repositories並在shutdown exact-once關閉。"""

    def __init__(
        self,
        延遲服務: 延遲Web代理服務,
        服務: Web代理服務,
        工作階段庫物件: 工作階段庫,
        使用者庫物件: 使用者庫,
    ) -> None:
        """保存lifespan-owned resources；不再執行I/O。"""
        self._延遲服務 = 延遲服務
        self._服務 = 服務
        self._工作階段庫 = 工作階段庫物件
        self._使用者庫 = 使用者庫物件
        self._已關閉 = False
        self._關閉鎖 = RLock()

    async def 關閉(self) -> None:
        """先停止新委派，再關閉使用者與工作階段SQLite connections。"""
        with self._關閉鎖:
            if self._已關閉:
                return
            self._已關閉 = True
        await run_in_threadpool(self._延遲服務.清除, self._服務)
        控制錯誤, 一般錯誤 = await run_in_threadpool(
            _關閉兩個連線, self._使用者庫, self._工作階段庫,
        )
        if 控制錯誤 is not None:
            raise 控制錯誤
        if 一般錯誤 is not None:
            raise 一般錯誤


class 生產Web代理建構器:
    """以canonical current-session／CSRF hooks建立CP3附加composition。"""

    def 建立附加相依項(self, 設定: 生產設定, 目前工作階段相依, CSRF相依) -> 發布介面相依項:
        """建立lazy service routers與單一lifespan resource factory。

        參數：
            設定: 已驗證生產設定。
            目前工作階段相依: auth composition提供的canonical dependency。
            CSRF相依: auth composition提供的single-use dependency。
        返回值：
            Chat、sessions、skills routers及一個resource factory。
        例外：
            ValueError: provider或注入契約不受支援。
        副作用：
            無資料庫或provider I/O；只建立routers、closures與lazy proxy。
        """
        if (
            type(設定) is not 生產設定
            or 設定.模型供應器 not in _允許模型供應器
            or not callable(目前工作階段相依)
            or not callable(CSRF相依)
        ):
            raise ValueError("Web代理生產組裝無效")
        延遲服務 = 延遲Web代理服務()
        路由器清單 = (
            建立聊天路由器(延遲服務, 目前工作階段相依, CSRF相依),
            建立工作階段路由器(延遲服務, 目前工作階段相依),
            建立技能路由器(延遲服務, 目前工作階段相依),
        )

        async def 建立資源() -> 生產Web代理資源:
            """在lifespan startup建立schema、repositories與request-local runtime factory。"""
            return await run_in_threadpool(_建立生產Web代理資源, 設定, 延遲服務)

        return 發布介面相依項(路由器清單, (建立資源,))


def _建立生產Web代理資源(設定: 生產設定, 延遲服務: 延遲Web代理服務) -> 生產Web代理資源:
    """建立startup-owned repositories、migration與Web代理服務；失敗時全清理。"""
    工作階段庫物件: 工作階段庫 | None = None
    使用者庫物件: 使用者庫 | None = None
    try:
        工作階段庫物件 = 工作階段庫(設定.資料庫路徑)
        使用者庫物件 = 使用者庫(設定.資料庫路徑)
        初始化發布介面資料庫(設定.資料庫路徑)

        def 建立執行階段(*, 使用者上下文物件, source: str) -> Web執行階段:
            """為每次Web turn建立不共享mutable provider/runtime的執行階段。"""
            return cast(Web執行階段, 代理執行階段(
                工作階段庫物件,
                假模型供應商() if 設定.模型供應器 == "fake" else GeminiADC供應商(
                    設定.模型名稱, cast(str, 設定.Gemini專案識別碼), cast(str, 設定.Gemini位置),
                ),
                設定.模型名稱,
                供應商名稱=設定.模型供應器,
                平台名稱="web",
                工作目錄=str(設定.資料庫路徑.parent),
                最大迭代次數=8,
                模型模式=設定.模型供應器,
                啟用壓縮摘要=False,
                使用者上下文物件=使用者上下文物件,
                source=source,
                model_config={"mode": 設定.模型供應器},
            ))

        服務 = Web代理服務(cast(Web工作階段庫, 工作階段庫物件), 使用者庫物件, 建立執行階段)
        延遲服務.安裝(服務)
        return 生產Web代理資源(延遲服務, 服務, 工作階段庫物件, 使用者庫物件)
    except BaseException as 啟動錯誤:
        控制錯誤, _ = _關閉兩個連線(使用者庫物件, 工作階段庫物件)
        if isinstance(啟動錯誤, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise
        if 控制錯誤 is not None:
            raise 控制錯誤
        raise


def _關閉兩個連線(使用者庫物件, 工作階段庫物件) -> tuple[BaseException | None, BaseException | None]:
    """依序attempt兩個close，回傳第一個control-flow與第一個ordinary錯誤。"""
    控制錯誤 = 一般錯誤 = None
    for 庫 in (使用者庫物件, 工作階段庫物件):
        if 庫 is None:
            continue
        try:
            庫.連線.close()
        except BaseException as 錯誤:
            if isinstance(錯誤, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                if 控制錯誤 is None:
                    控制錯誤 = 錯誤
            elif 一般錯誤 is None:
                一般錯誤 = 錯誤
    return 控制錯誤, 一般錯誤
