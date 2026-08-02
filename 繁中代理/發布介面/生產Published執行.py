"""CP4 lifespan-owned Published invocation production composition。
匯入與 app construction 只建立 immutable 設定、lazy proxy 及 router；SQLite、
技能套件檔案、工具 installer 與模型 registry 全部延後至 lifespan startup。
"""
from __future__ import annotations
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, RLock
from typing import Callable
from starlette.concurrency import run_in_threadpool
from .相依項 import 發布介面相依項
from .設定 import 生產設定
from .資料庫 import 初始化發布介面資料庫
from .生產Web代理 import 生產Web代理建構器
from .規劃.版本服務 import SQLite目前版本解析器, 已釘選版本, 目前版本不存在錯誤
from .憑證.服務 import SQLite憑證驗證服務, 憑證驗證結果, 憑證驗證狀態
from .呼叫.儲存庫 import SQLite呼叫儲存庫
from .呼叫.限流 import 限流決策
from .呼叫.擷取政策 import 擷取階段, 準備呼叫擷取, 寫入呼叫擷取
from .呼叫.編排器 import 外部呼叫編排器
from .呼叫.生產橋接 import (
    InvocationLedger橋接,
    SQLite雙層限流器,
    驗證釘選輸入結構,
    驗證釘選輸出結構,
)
from .執行期.工具版本庫 import 計算工具修訂摘要
from .執行期.工具發布庫 import 工具發布庫
from .執行期.快照儲存庫 import SQLite發布快照儲存庫
from .執行期.呼叫橋接 import 建立發布執行嘗試橋接
from .技能套件.載入器 import 已發布技能套件載入器
from .路由.外部呼叫 import 建立外部呼叫路由
_本機Path型別 = type(Path())
@dataclass(frozen=True, slots=True)
class Published生產設定:
    """部署端必須明確提供的 CP4 executable dependencies。
    參數：absolute Published DB、bundle root、exact-once 工具 installer 與模型 registry factory。
    返回值：不可變設定。
    例外：任一值不合 strict contract 時拋 ``ValueError``。
    副作用：只驗證 lexical path 與 callable，不讀檔案系統或呼叫 callback。
    """
    發布資料庫路徑: Path
    技能套件發布根: Path
    工具發布安裝器: Callable[[工具發布庫], None]
    模型供應商註冊表工廠: Callable[[], dict[str, object]]
    def __post_init__(self) -> None:
        """拒絕 cwd/home fallback、Path subclass 與非 callable 注入。
        參數：無；讀取四個設定欄位。
        返回值：``None``。
        例外：設定無效時拋 ``ValueError``。
        副作用：無 I/O。
        """
        資料庫 = object.__getattribute__(self, "發布資料庫路徑")
        根 = object.__getattribute__(self, "技能套件發布根")
        安裝器 = object.__getattribute__(self, "工具發布安裝器")
        工廠 = object.__getattribute__(self, "模型供應商註冊表工廠")
        if (type(資料庫) is not _本機Path型別 or not 資料庫.is_absolute()
                or type(根) is not _本機Path型別 or not 根.is_absolute()
                or not callable(安裝器) or not callable(工廠)):
            raise ValueError("Published生產設定無效") from None
class 延遲外部呼叫編排器:
    """固定 route identity，並以 active lease 支援 shutdown drain。"""
    def __init__(self) -> None:
        """建立未安裝的 per-app slot；無外部 I/O。"""
        self._條件 = Condition(RLock())
        self._編排器: 外部呼叫編排器 | None = None
        self._進行中 = 0
        self._正在停止 = False
    def 安裝(self, 編排器: 外部呼叫編排器) -> None:
        """startup exact-once 安裝 genuine orchestrator。
        參數：完整 production ``外部呼叫編排器``。
        返回值：``None``。
        例外：型別或 slot 狀態不符時拋 ``ValueError``。
        副作用：啟用後續 route lease。
        """
        if type(編排器) is not 外部呼叫編排器:
            raise ValueError("Published生產組裝無效") from None
        with self._條件:
            if self._編排器 is not None or self._進行中:
                raise ValueError("Published生產組裝無效") from None
            self._正在停止 = False
            self._編排器 = 編排器
    def 清除(self, 編排器: 外部呼叫編排器) -> None:
        """先拒絕新 request，再等待本次 identity 的 active leases 歸零。
        參數：本次 startup 安裝的 orchestrator。
        返回值：``None``。
        例外：無預期例外。
        副作用：可能阻塞直到進行中委派完成。
        """
        with self._條件:
            if self._編排器 is 編排器:
                self._編排器 = None
                self._正在停止 = True
                while self._進行中:
                    self._條件.wait()
    @contextmanager
    def _租借(self):
        """在完整同步委派期間持有 lease；未啟動或 draining 時 fail closed。"""
        with self._條件:
            編排器 = self._編排器
            if 編排器 is None or self._正在停止:
                raise RuntimeError("Published服務不可用") from None
            self._進行中 += 1
        try:
            yield 編排器
        finally:
            with self._條件:
                self._進行中 -= 1
                if self._進行中 == 0:
                    self._條件.notify_all()
    def 執行(self, 短名: str, 請求識別: str, API金鑰: str,
           輸入: object, 中繼資料: object | None, 時間: int | float):
        """以一個 active lease 委派完整 transport-neutral invocation。"""
        with self._租借() as 編排器:
            return 編排器.執行(短名, 請求識別, API金鑰, 輸入, 中繼資料, 時間)
class 生產Published執行資源:
    """擁有 lazy installation 與 startup-created registries 的 lifespan resource。"""
    def __init__(self, 代理: 延遲外部呼叫編排器, 編排器: 外部呼叫編排器,
                 工具庫: 工具發布庫, 模型表: dict[str, object]) -> None:
        """保存已成功安裝的資源參照，不執行額外 I/O。"""
        self._代理, self._編排器 = 代理, 編排器
        self._工具庫, self._模型表 = 工具庫, 模型表
        self._鎖, self._已關閉 = RLock(), False
    async def 關閉(self) -> None:
        """exact-once 清除 proxy、drain leases 並 detach registries。
        參數：無。
        返回值：``None``。
        例外：drain 的控制流程例外原樣傳出。
        副作用：不猜測 provider close protocol；只移除本 composition 的 references。
        """
        with self._鎖:
            if self._已關閉:
                return
            self._已關閉 = True
        編排器 = self._編排器
        if 編排器 is not None:
            await run_in_threadpool(self._代理.清除, 編排器)
        self._編排器 = None
        self._模型表.clear()
        self._工具庫 = None
class 生產Published執行建構器:
    """建立 CP4 invoke router 與單一 Published lifespan resource factory。"""
    def __init__(self, 設定: Published生產設定) -> None:
        """保存 exact immutable Published 設定；不呼叫注入或讀取路徑。"""
        if type(設定) is not Published生產設定:
            raise ValueError("Published生產組裝無效") from None
        self._設定 = 設定
    def 建立附加相依項(self, 設定: 生產設定, 目前工作階段相依, CSRF相依) -> 發布介面相依項:
        """在 app construction 建立 lazy proxy/router，將全部資源延後至 startup。"""
        if type(設定) is not 生產設定 or not callable(目前工作階段相依) or not callable(CSRF相依):
            raise ValueError("Published生產組裝無效") from None
        代理 = 延遲外部呼叫編排器()
        路由器 = 建立外部呼叫路由(代理)
        async def 建立資源() -> 生產Published執行資源:
            """在 threadpool 建立並安裝一次真實 Published composition。"""
            return await run_in_threadpool(_建立Published資源, 設定, self._設定, 代理)
        return 發布介面相依項((路由器,), (建立資源,))
class 生產Controller建構器:
    """依序組合 CP3 Web 與 CP4 Published routers/resources。"""
    def __init__(self, 設定: Published生產設定) -> None:
        """建立兩個彼此不知內部細節的 production builders。"""
        self._Web = 生產Web代理建構器()
        self._Published = 生產Published執行建構器(設定)
    def 建立附加相依項(self, 設定: 生產設定, 目前工作階段相依, CSRF相依) -> 發布介面相依項:
        """保持 Web→Published startup 及 Published→Web reverse shutdown 次序。"""
        網頁 = self._Web.建立附加相依項(設定, 目前工作階段相依, CSRF相依)
        發布 = self._Published.建立附加相依項(設定, 目前工作階段相依, CSRF相依)
        return 發布介面相依項(
            網頁.路由器清單 + 發布.路由器清單,
            網頁.資源工廠清單 + 發布.資源工廠清單,
        )
def _工具摘要(name: str, revision: str, description: str, parameters_json: str) -> str:
    """將快照庫 canonical JSON 轉接到唯一權威工具修訂摘要 helper。"""
    參數 = json.loads(parameters_json)
    if type(參數) is not dict:
        raise ValueError("工具摘要無效") from None
    return 計算工具修訂摘要(name=name, revision=revision, description=description, parameters=參數)
def _建立Published資源(生產: 生產設定, 發布: Published生產設定,
                    代理: 延遲外部呼叫編排器) -> 生產Published執行資源:
    """startup exact-once 建立 production adapters、registries、bridge 與 orchestrator。"""
    資料庫 = 發布.發布資料庫路徑
    if 資料庫 == 生產.資料庫路徑:
        raise ValueError("Published資料庫不得與Web資料庫共用") from None
    初始化發布介面資料庫(資料庫)
    解析器 = SQLite目前版本解析器(資料庫)
    呼叫庫 = SQLite呼叫儲存庫(資料庫)
    憑證 = SQLite憑證驗證服務(資料庫)
    限流器 = SQLite雙層限流器(資料庫)
    快照庫 = SQLite發布快照儲存庫(資料庫, _工具摘要)
    套件載入器 = 已發布技能套件載入器(發布.技能套件發布根, 快照庫)
    工具庫 = 工具發布庫()
    發布.工具發布安裝器(工具庫)
    原模型表 = 發布.模型供應商註冊表工廠()
    if type(原模型表) is not dict:
        raise ValueError("模型供應商註冊表無效") from None
    模型表 = dict(原模型表)
    if (not 模型表 or any(type(鍵) is not str or not 鍵 or 值 is None for 鍵, 值 in 模型表.items())):
        模型表.clear()
        raise ValueError("模型供應商註冊表無效") from None
    Runtime橋接 = 建立發布執行嘗試橋接(
        發布快照儲存庫=快照庫, 技能套件載入器=套件載入器,
        工具發布庫=工具庫, 模型供應商註冊表=模型表,
    )
    台帳 = InvocationLedger橋接(呼叫庫)
    編排器 = 外部呼叫編排器(
        解析器, 呼叫庫, 憑證,
        解析未找到型別=目前版本不存在錯誤,
        釘選型別=已釘選版本,
        驗證型別=憑證驗證結果,
        驗證狀態型別=憑證驗證狀態,
        階段型別=擷取階段,
        準備擷取=準備呼叫擷取,
        寫入擷取=寫入呼叫擷取,
        限流決策型別=限流決策,
        提交雙層計數=限流器.提交,
        驗證輸入=驗證釘選輸入結構,
        開始執行嘗試=台帳.開始執行嘗試,
        執行嘗試=Runtime橋接,
        驗證輸出=驗證釘選輸出結構,
        記錄執行嘗試=台帳.記錄執行嘗試,
    )
    代理.安裝(編排器)
    return 生產Published執行資源(代理, 編排器, 工具庫, 模型表)
__all__ = (
    "Published生產設定", "延遲外部呼叫編排器", "生產Published執行資源",
    "生產Published執行建構器", "生產Controller建構器",
)
