"""CP4 lifespan-owned Published invocation production composition。

參數：
    本模組公開明確設定、lazy proxy、資源與 builder 類別，不讀取隱含設定。
返回值：
    工廠建立由 lifespan 擁有的 Web／Published 組裝與固定 invocation route。
例外：
    組裝一般錯誤由 lifespan 固定映射；控制流程例外保留 identity。
副作用：
    匯入與 app construction 無 I/O；SQLite、installer 與模型 registry 延至 startup。
"""
from __future__ import annotations
import asyncio
import json
import math
import os
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Condition, RLock
from typing import Callable
from starlette.concurrency import run_in_threadpool
from .相依項 import 發布介面相依項
from .設定 import 生產設定
from .資料庫 import 初始化發布介面資料庫
from .生產Web代理 import 生產Web代理建構器
from .生產Published管理 import (
    Planner生產設定,
    延遲草稿規劃服務,
    延遲發布管理服務,
    生產Planner資源,
    建立生產Planner資源,
)
from .憑證.加密 import AESGCM憑證封套
from .憑證.管理 import SQLite憑證管理服務
from .規劃.發布管理 import 發布管理協調器
from .規劃.端點發布 import SQLite端點發布服務
from .規劃.版本服務 import (
    SQLite目前版本解析器, SQLite版本配置服務, 已釘選版本, 目前版本不存在錯誤,
)
from .憑證.服務 import SQLite憑證驗證服務, 憑證驗證結果, 憑證驗證狀態
from .呼叫.儲存庫 import SQLite呼叫儲存庫, 呼叫敏感交易協調器
from .呼叫.敏感稽核 import SQLite敏感稽核儲存庫
from .呼叫.Published工作階段 import SQLitePublished工作階段儲存庫
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
from .技能套件.協調器 import 技能套件協調器
from .技能套件.發布器 import 技能套件發布器
from .路由.外部呼叫 import 建立外部呼叫路由
from .路由.憑證管理 import 建立憑證管理路由器
from .生產Owner觀測 import 延遲Owner觀測服務, 建立Owner觀測路由, 安裝Owner觀測資源
from .生產端點查詢 import (
    延遲端點管理查詢服務,
    建立端點管理身份相依,
    安裝端點查詢資源,
)
from .路由.端點查詢 import 建立端點查詢路由器
from .路由.文件 import 建立端點文件路由器
from .生產端點文件 import 延遲端點文件服務, 安裝端點文件資源
from .路由.規劃發布 import (
    建立安全規劃發布路由器, 建立安全草稿端點建立路由器, 建立安全草稿路由器,
)
_本機Path型別 = type(Path())
_資料庫隔離錯誤 = "Published資料庫不得與Web資料庫共用"
_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)


def _驗證資料庫實體隔離(Web資料庫: Path, Published資料庫: Path) -> None:
    """以 canonical path 與檔案系統 identity 拒絕兩個資料庫別名。

    參數：
        Web資料庫: CP3 Web 的 exact absolute ``Path``。
        Published資料庫: CP4 Published 的 exact absolute ``Path``。
    返回值：
        ``None``；表示目前兩個路徑不是相同 canonical path 或既存 inode。
    例外：
        路徑契約、解析或 identity 查詢一般失敗皆固定為 ``ValueError``；控制流程例外原樣傳出。
    副作用：
        查詢路徑解析與檔案 metadata，不建立、開啟或修改資料庫。
    """
    try:
        if (
            type(Web資料庫) is not _本機Path型別
            or type(Published資料庫) is not _本機Path型別
            or not Web資料庫.is_absolute()
            or not Published資料庫.is_absolute()
        ):
            raise ValueError
        相同 = Web資料庫.resolve(strict=False) == Published資料庫.resolve(strict=False)
        if not 相同 and Web資料庫.exists() and Published資料庫.exists():
            相同 = os.path.samefile(Web資料庫, Published資料庫)
        if 相同:
            raise ValueError
    except _控制流程例外:
        raise
    except BaseException:
        raise ValueError(_資料庫隔離錯誤) from None


@dataclass(frozen=True, slots=True)
class Published生產設定:
    """部署端必須明確提供的 CP4 executable dependencies。
    參數：absolute Published DB、bundle root、exact-once 工具 installer、模型 registry factory
    與孤兒保存期限。
    返回值：不可變設定。
    例外：任一值不合 strict contract 時拋 ``ValueError``。
    副作用：只驗證 lexical path 與 callable，不讀檔案系統或呼叫 callback。

    描述：部署端必須明確提供的 CP4 executable dependencies。
    """
    發布資料庫路徑: Path
    技能套件發布根: Path
    工具發布安裝器: Callable[[工具發布庫], None]
    模型供應商註冊表工廠: Callable[[], dict[str, object]]
    孤兒保留秒數: float = 86_400.0
    Planner設定: Planner生產設定 | None = None
    憑證封套工廠: Callable[[], AESGCM憑證封套] | None = None
    Owner觀測游標金鑰: bytes | None = field(default=None, repr=False)
    def __post_init__(self) -> None:
        """拒絕 cwd/home fallback、Path subclass 與非 callable 注入。
        參數：無；讀取五個設定欄位。
        返回值：``None``。
        例外：設定無效時拋 ``ValueError``。
        副作用：無 I/O。

        描述：拒絕 cwd/home fallback、Path subclass 與非 callable 注入。
        """
        資料庫 = object.__getattribute__(self, "發布資料庫路徑")
        根 = object.__getattribute__(self, "技能套件發布根")
        安裝器 = object.__getattribute__(self, "工具發布安裝器")
        工廠 = object.__getattribute__(self, "模型供應商註冊表工廠")
        保留秒數 = object.__getattribute__(self, "孤兒保留秒數")
        Planner組裝 = object.__getattribute__(self, "Planner設定")
        封套工廠 = object.__getattribute__(self, "憑證封套工廠")
        觀測金鑰 = object.__getattribute__(self, "Owner觀測游標金鑰")
        if (type(資料庫) is not _本機Path型別 or not 資料庫.is_absolute()
                or type(根) is not _本機Path型別 or not 根.is_absolute()
                or not callable(安裝器) or not callable(工廠)
                or type(保留秒數) not in (int, float)
                or 保留秒數 < 0 or 保留秒數 > sys.float_info.max
                or (type(保留秒數) is float and not math.isfinite(保留秒數))
                or (Planner組裝 is not None and type(Planner組裝) is not Planner生產設定)
                or (封套工廠 is not None and not callable(封套工廠))):
            raise ValueError("Published生產設定無效") from None
        if 觀測金鑰 is not None and (type(觀測金鑰) is not bytes or len(觀測金鑰) != 32):
            raise ValueError("Published生產設定無效") from None


class 延遲憑證管理服務:
    """讓 credential routes 在 construction 固定、startup 才取得真實 provider。
    描述：讓 credential routes 在 construction 固定、startup 才取得真實 provider。
    參數：建構資料由類別欄位或建構器簽章明確提供，不讀取隱含輸入。
    返回值：可供呼叫端使用的``延遲憑證管理服務``類型或實例。
    """
    def __init__(self) -> None:
        """建立未安裝的 per-app provider slot。
        描述：建立未安裝的 per-app provider slot。
        參數：無；使用已封裝狀態或固定測試資料。
        返回值：無；建立尚未安裝服務的per-app provider slot。
        """
        self._服務: SQLite憑證管理服務 | None = None
        self._條件 = Condition(RLock()); self._進行中 = 0; self._正在停止 = False; self._停止中的服務 = None
        self._世代 = 0; self._目前世代: int | None = None; self._停止中世代: int | None = None
    def 安裝(self, 服務: SQLite憑證管理服務) -> int:
        """在 startup exact-once 安裝真實 SQLite adapter。
        描述：在 startup exact-once 安裝真實 SQLite adapter。
        參數：``服務``。
        返回值：無；exact-once保存啟動中的``SQLite憑證管理服務``。
        """
        if type(服務) is not SQLite憑證管理服務:
            raise ValueError("Published憑證管理服務無效") from None
        with self._條件:
            if self._服務 is not None or self._進行中:
                raise ValueError("Published憑證管理服務無效") from None
            self._世代 += 1; self._目前世代 = self._世代
            self._正在停止 = False; self._停止中的服務 = None; self._停止中世代 = None; self._服務 = 服務
            return self._世代
    def 清除(self, 服務: SQLite憑證管理服務, 世代: int) -> None:
        """shutdown 只清除本次 startup 的 exact provider reference。
        描述：shutdown 只清除本次 startup 的 exact provider reference。參數：``服務``與``世代``。
        返回值：無；只有identity相同時清除目前服務參照。
        """
        self._撤銷並等待(服務, 世代)
    def _撤銷並等待(
        self, 服務: SQLite憑證管理服務, 世代: int | None, *, 允許解析目前世代: bool = False,
    ) -> None:
        """由production owner繞過可失敗wrapper，依世代撤銷slot並等待active lease。"""
        with self._條件:
            if 世代 is None and 允許解析目前世代 and self._服務 is 服務:
                世代 = self._目前世代
            if self._服務 is 服務 and self._目前世代 == 世代:
                self._服務 = None; self._目前世代 = None
                self._正在停止 = True; self._停止中的服務 = 服務; self._停止中世代 = 世代
                while self._進行中: self._條件.wait()
                self._停止中的服務 = None; self._停止中世代 = None; self._條件.notify_all()
            elif self._停止中的服務 is 服務 and self._停止中世代 == 世代:
                while self._停止中世代 == 世代: self._條件.wait()
    @contextmanager
    def _租借(self):
        """描述：完整操作期間租借provider，未啟動或draining時fail closed。
        參數：無。
        返回值：yield目前已安裝的``SQLite憑證管理服務``。
        """
        with self._條件:
            服務 = self._服務
            if 服務 is None or self._正在停止:
                raise RuntimeError("Published服務不可用") from None
            self._進行中 += 1
        try:
            yield 服務
        finally:
            with self._條件:
                self._進行中 -= 1
                if self._進行中 == 0:
                    self._條件.notify_all()
    def 列出憑證(self, **參數):
        """委派 safe list 操作。

        描述：委派 safe list 操作。
        參數：``**參數``；返回值：真實provider回傳的``憑證列表結果``。
        """
        with self._租借() as 服務:
            return 服務.列出憑證(**參數)
    def 建立憑證(self, **參數):
        """委派 additional credential create 操作。

        描述：委派 additional credential create 操作。
        參數：``**參數``；返回值：真實provider回傳的``一次性憑證建立收據``。
        """
        with self._租借() as 服務:
            return 服務.建立憑證(**參數)

    def 撤銷憑證(self, **參數):
        """委派 audited idempotent revoke 操作。

        描述：委派 audited idempotent revoke 操作。
        參數：``**參數``；返回值：真實provider回傳的``憑證撤銷收據``。
        """
        with self._租借() as 服務:
            return 服務.撤銷憑證(**參數)
class 延遲外部呼叫編排器:
    """固定 route identity，並以 active lease 支援 shutdown drain。

    參數：
        建構不接受參數，編排器只可由 ``安裝`` 提供。
    返回值：
        提供與真實 ``外部呼叫編排器`` 相同的同步 invocation 委派結果。
    例外：
        未安裝或 draining 時固定 ``RuntimeError``；安裝違約為 ``ValueError``。
    副作用：
        在條件鎖內追蹤 active leases 與本次 startup 編排器 identity。
    """
    def __init__(self) -> None:
        """建立未安裝的 per-app slot。

        參數：無。
        返回值：``None``；Python 建構完成新 proxy。
        例外：只有鎖配置的 runtime 錯誤可能原樣傳出。
        副作用：配置條件鎖與空 slot，不執行外部 I/O。
        """
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
        """在完整同步委派期間持有 lease；未啟動或 draining 時 fail closed。

        參數：無。
        返回值：yield 本次安裝的 exact 編排器。
        例外：服務不可用時固定 ``RuntimeError``；委派端例外原樣傳出。
        副作用：在鎖內增減 active lease，歸零時喚醒 shutdown waiter。
        """
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
           輸入: object, 中繼資料: object | None, 時間: int | float, *,
           工作階段識別: str | None = None):
        """以一個 active lease 委派完整 transport-neutral invocation。
        參數：短名、請求識別、API 金鑰、輸入、中繼資料、呼叫時間與 optional 工作階段識別皆原樣傳給編排器。
        返回值：真實編排器建立的 invocation response。
        例外：服務不可用固定失敗，編排器例外保持 identity 傳出。
        副作用：租借一次 proxy slot 並執行一次同步 invocation。
        """
        with self._租借() as 編排器:
            if 工作階段識別 is None:
                return 編排器.執行(短名, 請求識別, API金鑰, 輸入, 中繼資料, 時間)
            return 編排器.執行(
                短名, 請求識別, API金鑰, 輸入, 中繼資料, 時間,
                工作階段識別,
            )
class 生產Published執行資源:
    """擁有 lazy installation 與 startup-created registries 的 lifespan resource。
    參數：
        由成功 startup 傳入 proxy、編排器、工具庫及 detached 模型表。
    返回值：
        提供 async ``關閉`` 的 lifespan resource。
    例外：
        shutdown control/ordinary drain 例外在完成 reference cleanup 後傳出。
    副作用：
        保存並於關閉時釋放本 composition 的所有 live handler/model authority。
    描述：擁有 lazy installation 與 startup-created registries 的 lifespan resource。
    """
    def __init__(self, 代理: 延遲外部呼叫編排器, 編排器: 外部呼叫編排器,
                 工具庫: 工具發布庫, 模型表: dict[str, object],
                 Planner資源: 生產Planner資源 | None = None,
                 發布管理代理: 延遲發布管理服務 | None = None,
                 發布管理服務: 發布管理協調器 | None = None,
                 技能套件協調器物件: 技能套件協調器 | None = None,
                 技能套件發布器物件: 技能套件發布器 | None = None,
                 端點發布服務物件: SQLite端點發布服務 | None = None,
                 憑證管理代理: 延遲憑證管理服務 | None = None,
                 憑證管理服務物件: SQLite憑證管理服務 | None = None,
                 憑證管理世代: int | None = None) -> None:
        """保存已成功安裝的資源參照。
        參數：代理、編排器、工具庫與模型表皆屬於同一次 startup。
        返回值：``None``；Python 建構完成 lifespan resource。
        例外：只有鎖配置錯誤可能由 runtime 原樣傳出。
        副作用：保存強參照與配置 exact-once 關閉鎖，不執行額外 I/O。
        描述：保存已成功安裝的資源參照。
        """
        self._代理, self._編排器 = 代理, 編排器
        self._工具庫, self._模型表 = 工具庫, 模型表
        self._Planner資源, self._發布管理代理, self._發布管理服務 = Planner資源, 發布管理代理, 發布管理服務
        self._技能套件協調器, self._技能套件發布器 = 技能套件協調器物件, 技能套件發布器物件
        self._端點發布服務, self._憑證管理代理, self._憑證管理服務 = 端點發布服務物件, 憑證管理代理, 憑證管理服務物件
        self._憑證管理世代 = 憑證管理世代
        self._關閉條件 = Condition(RLock())
        self._已關閉 = False
        self._關閉狀態 = "尚未開始"
        self._關閉錯誤: BaseException | None = None

    def 取得Planner資源(self) -> 生產Planner資源 | None:
        """取得仍由本次 Published lifespan 擁有的 Planner resource。
        參數：無。
        返回值：啟動期間回傳 exact Planner resource；關閉後回傳 ``None``。
        例外：無預期例外。
        副作用：無；不建立第二份工具庫或草稿 Aggregate。
        """
        return self._Planner資源

    def 取得規劃服務(self):
        """取得後續 #4 必須共用的唯一 Draft Aggregate。
        參數：無。
        返回值：Planner 啟用時回傳 exact ``規劃服務``；未啟用或關閉後回傳 ``None``。
        例外：無預期例外。
        副作用：無。
        """
        Planner資源 = self._Planner資源
        return None if Planner資源 is None else Planner資源.取得規劃服務()

    def 取得發布管理服務(self) -> 發布管理協調器 | None:
        """取得仍由 lifespan 擁有且已安裝的 exact management coordinator。
        參數：無。
        返回值：management 啟用期間回傳 exact 協調器；未啟用或關閉後回傳 ``None``。
        例外：無預期例外。
        副作用：無；不建立或安裝任何 authority。
        """
        return self._發布管理服務
    async def 關閉(self) -> None:
        """讓任意 event loop 的 concurrent callers 等待同一 exact-once shutdown。
        參數：無。
        返回值：``None``。
        例外：cleanup error 對所有 caller 保留 identity；caller cancellation 延至清理完成後傳出。
        副作用：第一個 worker 成為唯一清理 owner；其餘 caller 跨 loop 等待同一完成結果。
        """
        caller取消: asyncio.CancelledError | None = None
        派送錯誤: BaseException | None = None
        while True:
            with self._關閉條件:
                if self._關閉狀態 == "已完成":
                    if self._關閉錯誤 is not None:
                        raise self._關閉錯誤
                    break
            try:
                await run_in_threadpool(self._清除同步)
                break
            except asyncio.CancelledError as 錯誤:
                with self._關閉條件:
                    if self._關閉狀態 == "已完成" and self._關閉錯誤 is 錯誤:
                        raise
                if caller取消 is None:
                    caller取消 = 錯誤
                目前工作 = asyncio.current_task()
                if 目前工作 is not None:
                    目前工作.uncancel()
            except BaseException as 錯誤:
                with self._關閉條件:
                    尚未取得所有權 = self._關閉狀態 == "尚未開始"
                    已完成同一錯誤 = self._關閉狀態 == "已完成" and self._關閉錯誤 is 錯誤
                if 已完成同一錯誤:
                    raise
                if not 尚未取得所有權:
                    if 派送錯誤 is None:
                        派送錯誤 = 錯誤
                    self._清除同步()
                    break
                # threadpool dispatch 尚未進入 cleanup owner 時，caller 原地執行；
                # 若遲到的 worker 之後啟動，只會依 Condition 等待同一 outcome。
                self._清除同步()
                if isinstance(錯誤, _控制流程例外):
                    raise
                break
        if isinstance(派送錯誤, _控制流程例外):
            raise 派送錯誤
        if caller取消 is not None:
            raise caller取消
        if 派送錯誤 is not None:
            raise 派送錯誤

    def _清除同步(self) -> None:
        """以 Condition 協調跨 loop callers 並 exact-once 發布同步清理結果。
        參數：無。
        返回值：None。
        例外：所有 caller 在唯一清理完成後取得同一 exact 第一錯誤物件。
        副作用：唯一 owner 執行實際清理；其他 worker 等待，不會雙重撤銷 authority。
        """
        with self._關閉條件:
            if self._關閉狀態 == "已完成":
                if self._關閉錯誤 is not None:
                    raise self._關閉錯誤
                return
            if self._關閉狀態 == "進行中":
                while self._關閉狀態 != "已完成":
                    self._關閉條件.wait()
                if self._關閉錯誤 is not None:
                    raise self._關閉錯誤
                return
            self._關閉狀態 = "進行中"
            self._已關閉 = True
        關閉錯誤: BaseException | None = None
        try:
            self._執行關閉同步()
        except BaseException as 清理錯誤:
            關閉錯誤 = 清理錯誤
        finally:
            with self._關閉條件:
                self._關閉錯誤 = 關閉錯誤
                self._關閉狀態 = "已完成"
                self._關閉條件.notify_all()
        if 關閉錯誤 is not None:
            raise 關閉錯誤

    def _執行關閉同步(self) -> None:
        """由唯一 shutdown owner 清除 Planner、Invocation 與 registries。
        參數：無。
        返回值：None。
        例外：所有清理都嘗試後，控制流程優先於第一個 ordinary failure。
        副作用：drain 兩個 proxy，清空模型表與工具發布 authority 並移除強參照。
        描述：由唯一 shutdown owner 清除 Planner、Invocation 與 registries。
        """
        管理代理, 管理服務 = self._發布管理代理, self._發布管理服務
        憑證代理, 憑證服務, 憑證世代 = self._憑證管理代理, self._憑證管理服務, self._憑證管理世代
        Planner資源, 編排器, 工具庫 = self._Planner資源, self._編排器, self._工具庫
        控制流程錯誤 = 普通清除錯誤 = None
        try:
            if 管理代理 is not None and 管理服務 is not None:
                管理代理.清除(管理服務)
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外):
                控制流程錯誤 = 錯誤
            else:
                普通清除錯誤 = 錯誤
            try:
                if type(管理代理) is 延遲發布管理服務 and type(管理服務) is 發布管理協調器:
                    管理代理._撤銷並等待(管理服務, 允許已撤銷=True)
            except BaseException as 撤銷錯誤:
                if isinstance(撤銷錯誤, _控制流程例外) and 控制流程錯誤 is None:
                    控制流程錯誤 = 撤銷錯誤
                elif 普通清除錯誤 is None:
                    普通清除錯誤 = 撤銷錯誤
        try:
            if 憑證代理 is not None and 憑證服務 is not None and 憑證世代 is not None:
                憑證代理.清除(憑證服務, 憑證世代)
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外):
                if 控制流程錯誤 is None: 控制流程錯誤 = 錯誤
            elif 普通清除錯誤 is None: 普通清除錯誤 = 錯誤
        finally:
            try:
                if 憑證代理 is not None and 憑證服務 is not None and 憑證世代 is not None:
                    延遲憑證管理服務._撤銷並等待(憑證代理, 憑證服務, 憑證世代)
            except BaseException as 錯誤:
                if isinstance(錯誤, _控制流程例外):
                    if 控制流程錯誤 is None: 控制流程錯誤 = 錯誤
                elif 普通清除錯誤 is None: 普通清除錯誤 = 錯誤
            self._憑證管理服務 = self._憑證管理代理 = self._憑證管理世代 = None
            self._發布管理服務 = self._發布管理代理 = self._端點發布服務 = None
            self._技能套件發布器 = self._技能套件協調器 = None
        try:
            if Planner資源 is not None:
                Planner資源._清除同步()
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外):
                if 控制流程錯誤 is None:
                    控制流程錯誤 = 錯誤
            elif 普通清除錯誤 is None:
                普通清除錯誤 = 錯誤
        try:
            if 編排器 is not None:
                self._代理.清除(編排器)
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外):
                if 控制流程錯誤 is None:
                    控制流程錯誤 = 錯誤
            elif 普通清除錯誤 is None:
                普通清除錯誤 = 錯誤
        finally:
            self._Planner資源 = None
            self._編排器 = None
            self._模型表.clear()
            if 工具庫 is not None:
                try:
                    工具庫.清除所有發布()
                except BaseException as 錯誤:
                    if isinstance(錯誤, _控制流程例外):
                        if 控制流程錯誤 is None:
                            控制流程錯誤 = 錯誤
                    elif 普通清除錯誤 is None:
                        普通清除錯誤 = 錯誤
            self._工具庫 = None
        if 控制流程錯誤 is not None:
            raise 控制流程錯誤
        if 普通清除錯誤 is not None:
            raise 普通清除錯誤
class 生產Published執行建構器:
    """建立 CP4 invoke router 與單一 Published lifespan resource factory。
    參數：建構時只接受 exact ``Published生產設定``。
    返回值：透過 ``建立附加相依項`` 回傳 router 與 resource factory。
    例外：設定或 dependency 違約時固定 ``ValueError``。
    副作用：app construction 只建立 proxy、router 與 closure，不執行 callback 或 I/O。
    描述：建立 CP4 invoke router 與單一 Published lifespan resource factory。
    """
    def __init__(self, 設定: Published生產設定) -> None:
        """保存 exact immutable Published 設定。

        參數：設定是 exact ``Published生產設定``。
        返回值：``None``；完成 builder 建構。
        例外：型別不符時固定 ``ValueError``。
        副作用：只保存參照，不呼叫注入或讀取路徑。

        描述：保存 exact immutable Published 設定。
        """
        if type(設定) is not Published生產設定:
            raise ValueError("Published生產組裝無效") from None
        self._設定 = 設定
        self._草稿規劃代理, self._發布管理代理 = 延遲草稿規劃服務(), 延遲發布管理服務()
        self._憑證管理代理 = 延遲憑證管理服務()
        self._管理稽核代理, self._管理稽核游標 = 建立管理稽核權限()
        self._Owner觀測代理 = 延遲Owner觀測服務()
        self._端點查詢代理 = 延遲端點管理查詢服務()
        self._端點文件代理 = 延遲端點文件服務()

    def 取得草稿規劃代理(self) -> 延遲草稿規劃服務:
        """取得本 builder 在 app construction 建立的 per-app Lazy Draft Proxy。

        參數：無。
        返回值：固定 identity 的 ``延遲草稿規劃服務``，供後續安全 route 捕捉。
        例外：無預期例外。
        副作用：無；不建立任何 startup resource。
        """
        return self._草稿規劃代理

    def 取得發布管理代理(self) -> 延遲發布管理服務:
        """取得 canonical Create route 捕捉的固定 per-app lazy proxy。

        參數：無。
        返回值：本 builder 建構時建立的 exact ``延遲發布管理服務``。
        例外：無預期例外。
        副作用：無；不呼叫 envelope factory 或建立 management resources。
        """
        return self._發布管理代理
    def 建立附加相依項(self, 設定: 生產設定, 目前工作階段相依, CSRF相依) -> 發布介面相依項:
        """在 app construction 建立 lazy proxy/router，將全部資源延後至 startup。

        參數：CP3 生產設定、canonical session dependency 與 CSRF dependency。
        返回值：含 exact invocation 與固定可探索的 Draft router；只有 explicit Planner＋Key
        完整設定時才額外公開 Endpoint Create，並附一個 async resource factory。
        例外：設定或 dependency 違約時固定 ``ValueError``。
        副作用：只建立 per-app proxy、router 與 closure，不執行 callback 或 I/O。

        描述：在 app construction 建立 lazy proxy/router，將全部資源延後至 startup。
        """
        if type(設定) is not 生產設定 or not callable(目前工作階段相依) or not callable(CSRF相依):
            raise ValueError("Published生產組裝無效") from None
        from .生產組裝 import _取得生產工作階段權限
        from .治理.管理遮蔽治理 import 管理遮蔽治理權限
        from .路由.管理遮蔽 import 建立管理遮蔽路由器
        try:
            工作階段服務, 網頁設定 = _取得生產工作階段權限(目前工作階段相依)
            管理遮蔽權限 = 管理遮蔽治理權限(工作階段服務, 網頁設定)
        except ValueError:
            管理遮蔽權限 = None
        代理 = 延遲外部呼叫編排器()
        路由器清單 = (建立外部呼叫路由(代理),)
        if self._設定.憑證封套工廠 is None:
            管理路由器 = 建立安全草稿路由器(
                self._草稿規劃代理, 目前工作階段相依, CSRF相依,
            )
        else:
            if self._設定.Planner設定 is None:
                管理路由器 = 建立安全草稿路由器(
                    self._草稿規劃代理, 目前工作階段相依, CSRF相依,
                )
            else:
                管理路由器 = 建立安全規劃發布路由器(
                    self._草稿規劃代理, self._發布管理代理,
                    目前工作階段相依, CSRF相依,
                )
        路由器清單 += (管理路由器,)
        if self._設定.憑證封套工廠 is not None:
            路由器清單 += (建立憑證管理路由器(
                self._憑證管理代理, 目前工作階段相依, CSRF相依,
            ),)
        路由器清單 += 建立端點文件路由器(self._端點文件代理, 目前工作階段相依)
        路由器清單 += (建立管理稽核路由(self._管理稽核代理, self._管理稽核游標, 目前工作階段相依),)
        if 管理遮蔽權限 is not None:
            路由器清單 += (建立管理遮蔽路由器(管理遮蔽權限),)
        if self._設定.Owner觀測游標金鑰 is not None:
            路由器清單 += (建立端點查詢路由器(
                self._端點查詢代理, 建立端點管理身份相依(目前工作階段相依),
            ),)
            路由器清單 += (建立Owner觀測路由(self._Owner觀測代理, 目前工作階段相依),)
        async def 建立資源():
            """在 threadpool 建立並安裝一次真實 Published composition。

            參數：無；使用 immutable closure 內的三個 composition dependencies。
            返回值：成功安裝的 ``生產Published執行資源``。
            例外：startup 例外原樣傳給 lifespan 統一分類。
            副作用：執行 migration、注入 callback 及完整 Published 組裝。

            描述：在 threadpool 建立並安裝一次真實 Published composition。
            """
            主資源 = await 安裝管理稽核資源(await run_in_threadpool(
                _建立Published資源, 設定, self._設定, 代理, self._草稿規劃代理,
                self._發布管理代理, self._憑證管理代理,
            ), self._管理稽核代理, self._設定.發布資料庫路徑)
            主資源 = await 安裝端點文件資源(
                主資源, self._端點文件代理, self._設定.發布資料庫路徑,
            )
            if self._設定.Owner觀測游標金鑰 is not None:
                主資源 = await 安裝Owner觀測資源(
                    主資源, self._Owner觀測代理, self._設定.發布資料庫路徑,
                    self._設定.Owner觀測游標金鑰,
                )
                主資源 = await 安裝端點查詢資源(
                    主資源, self._端點查詢代理, self._設定.發布資料庫路徑,
                    self._設定.Owner觀測游標金鑰,
                )
            if 管理遮蔽權限 is None:
                return 主資源
            return await 安裝管理遮蔽資源(主資源, 管理遮蔽權限, self._設定.發布資料庫路徑)
        return 發布介面相依項(路由器清單, (建立資源,))
class 生產Controller建構器:
    """依序組合 CP3 Web 與 CP4 Published routers/resources。

    參數：建構時接受 exact Published 生產設定。
    返回值：建立含兩組 routers 與兩個 lifespan resources 的附加相依項。
    例外：子 builder 的組裝契約錯誤原樣傳出。
    副作用：只建立兩個 builder；實際 FS preflight 與資源建立延至 startup。
    """
    def __init__(self, 設定: Published生產設定) -> None:
        """建立兩個彼此不知內部細節的 production builders。

        參數：設定是 CP4 Published 的 exact immutable 設定。
        返回值：``None``；完成 Controller builder 建構。
        例外：Published builder 拒絕設定時傳出固定 ``ValueError``。
        副作用：建立 Web 與 Published builder，不讀檔案系統。
        """
        self._Web = 生產Web代理建構器()
        self._Published = 生產Published執行建構器(設定)
    def 建立附加相依項(self, 設定: 生產設定, 目前工作階段相依, CSRF相依) -> 發布介面相依項:
        """先驗證資料庫 identity，再保持 Web→Published startup 與反向 shutdown。

        參數：
            設定: 含 Web DB 的 exact CP3 生產設定。
            目前工作階段相依: canonical current-session dependency。
            CSRF相依: canonical single-use CSRF dependency。
        返回值：
            路由順序不變，且 Web factory 前具有同一 closure 內 preflight 的附加相依項。
        例外：
            組裝契約一般失敗傳出 ``ValueError``；控制流程例外原樣傳出。
        副作用：
            只建立 closure；檔案 identity 查詢與所有資源建立均延至 startup。
        """
        網頁 = self._Web.建立附加相依項(設定, 目前工作階段相依, CSRF相依)
        發布 = self._Published.建立附加相依項(設定, 目前工作階段相依, CSRF相依)
        Web工廠 = 網頁.資源工廠清單[0]

        async def 建立已隔離Web資源():
            """在任何 Web migration 前執行 CP4 DB identity preflight。

            參數：
                無；使用 immutable composition closure。
            返回值：
                原 Web factory 建立的 lifespan resource。
            例外：
                資料庫別名固定拒絕；原 Web factory 例外原樣傳出供 lifespan 映射。
            副作用：
                先在 threadpool 查詢 FS identity，再委派一次 Web resource factory。
            """
            await run_in_threadpool(
                _驗證資料庫實體隔離, 設定.資料庫路徑, self._Published._設定.發布資料庫路徑,
            )
            return await Web工廠()

        return 發布介面相依項(
            網頁.路由器清單 + 發布.路由器清單,
            (建立已隔離Web資源,) + 發布.資源工廠清單,
        )



def _工具摘要(name: str, revision: str, description: str, parameters_json: str) -> str:
    """將快照庫 canonical JSON 轉接到唯一權威工具修訂摘要 helper。

    參數：工具名稱、修訂、說明與 canonical parameters JSON 字串。
    返回值：唯一權威 helper 計算的十六進位摘要。
    例外：JSON 非 object 時固定 ``ValueError``；解析與摘要錯誤原樣傳出。
    副作用：只解析記憶體字串與計算摘要。
    """
    參數 = json.loads(parameters_json)
    if type(參數) is not dict:
        raise ValueError("工具摘要無效") from None
    return 計算工具修訂摘要(name=name, revision=revision, description=description, parameters=參數)


from .生產管理稽核 import (
    建立管理稽核權限, 建立管理稽核路由, 安裝管理稽核資源, 安裝管理遮蔽資源,
)


def _提交協調資料庫(協調資料庫: sqlite3.Connection) -> None:
    """直接使用 SQLite 基底 primitive 提交協調交易。

    參數：
        協調資料庫: 目前持有協調交易的 SQLite 連線。
    返回值：
        提交成功後回傳 ``None``。
    例外：
        SQLite 提交或耐久確認失敗時原樣傳出。
    副作用：
        提交目前交易；不委派可能覆寫 ``commit`` 的連線 subclass 方法。
    """
    sqlite3.Connection.commit(協調資料庫)


def _回滾協調資料庫(協調資料庫: sqlite3.Connection) -> None:
    """直接使用 SQLite 基底 primitive 回滾仍存在的協調交易。

    參數：
        協調資料庫: 可能仍持有協調交易的 SQLite 連線。
    返回值：
        無交易或回滾完成後回傳 ``None``。
    例外：
        SQLite 回滾失敗時原樣傳出。
    副作用：
        必要時回滾目前交易；不委派可能覆寫 ``rollback`` 的 subclass 方法。
    """
    if 協調資料庫.in_transaction:
        sqlite3.Connection.rollback(協調資料庫)


def _關閉協調資料庫(協調資料庫: sqlite3.Connection) -> None:
    """直接使用 SQLite 基底 primitive 釋放協調連線 authority。

    參數：
        協調資料庫: 本次 startup 獨占擁有的短生命週期連線。
    返回值：
        連線成功關閉後回傳 ``None``。
    例外：
        SQLite 基底關閉失敗時原樣傳出。
    副作用：
        關閉連線並釋放其交易與檔案鎖；不委派可能覆寫 ``close`` 的 subclass 方法。
    """
    sqlite3.Connection.close(協調資料庫)


def _執行技能套件啟動協調(發布: Published生產設定) -> 技能套件協調器:
    """以一份 lifespan coordinator 完成啟動協調並交回 management 共用。

    參數：
        發布: 提供 Published DB、技能套件根與孤兒保存期限的不可變設定。
    返回值：
        已完成本輪協調且可供 management orphan handling 共用的 exact ``技能套件協調器``。
    例外：
        連線、協調或提交失敗原樣傳出；控制流程例外保持原物件。
    副作用：
        每次嘗試開啟一條短生命週期 SQLite 連線，可能修復收據、隔離或刪除套件；
        成功時提交，普通提交錯誤以新連線重驗一次，其他失敗回滾後關閉。
    """
    協調器 = 技能套件協調器(
        發布.技能套件發布根,
        孤兒保留秒數=發布.孤兒保留秒數,
    )
    for 嘗試次數 in range(2):
        協調資料庫 = sqlite3.connect(發布.發布資料庫路徑)
        try:
            協調資料庫.execute("PRAGMA foreign_keys=ON")
            協調結果 = 協調器.啟動協調(time.time(), 協調資料庫)
            try:
                _提交協調資料庫(協調資料庫)
            except _控制流程例外:
                raise
            except BaseException:
                if 嘗試次數 == 0:
                    continue
                raise
            return 協調器
        except BaseException:
            try:
                _回滾協調資料庫(協調資料庫)
            except BaseException:
                # 基底 close 仍會釋放未提交交易；清理錯誤不得替換第一個失敗。
                pass
            raise
        finally:
            原始錯誤 = sys.exception()
            try:
                _關閉協調資料庫(協調資料庫)
            except BaseException:
                if 原始錯誤 is None:
                    raise
    raise AssertionError


def _建立Published資源(生產: 生產設定, 發布: Published生產設定,
                    代理: 延遲外部呼叫編排器,
                    草稿代理: 延遲草稿規劃服務 | None = None,
                    管理代理: 延遲發布管理服務 | None = None,
                    憑證管理代理: 延遲憑證管理服務 | None = None) -> 生產Published執行資源:
    """startup 建立完整 Published composition，任一局部失敗皆清空 handler authority。

    參數：
        生產: 含 Web DB authority 的 exact CP3 設定。
        發布: 含 Published DB、installer 與 model factory 的 exact CP4 設定。
        代理: 本 app 唯一、尚未安裝的延遲 invocation proxy。
    返回值：
        已安裝 proxy 且擁有 detached registries 的 lifespan resource。
    例外：
        資料庫別名或模型表違約為 ``ValueError``；callback/control 例外保留 identity 傳出。
    副作用：
        migration 前後查詢 FS identity，依序執行 initializer、技能套件協調與提交、
        installer、model factory 與 bridge 組裝。

    描述：startup 建立完整 Published composition，任一局部失敗皆清空 handler authority。
    """
    資料庫 = 發布.發布資料庫路徑
    _驗證資料庫實體隔離(生產.資料庫路徑, 資料庫)
    初始化發布介面資料庫(資料庫)
    _驗證資料庫實體隔離(生產.資料庫路徑, 資料庫)
    敏感writer = SQLite敏感稽核儲存庫(資料庫)
    敏感writer.驗證啟動結構()
    敏感協調器 = 呼叫敏感交易協調器(敏感writer)
    呼叫庫 = SQLite呼叫儲存庫(資料庫, 敏感交易協調器=敏感協調器)
    套件協調器 = _執行技能套件啟動協調(發布)
    解析器 = SQLite目前版本解析器(資料庫)
    工作階段庫 = SQLitePublished工作階段儲存庫(資料庫)
    憑證 = SQLite憑證驗證服務(資料庫)
    限流器 = SQLite雙層限流器(資料庫)
    快照庫 = SQLite發布快照儲存庫(資料庫, _工具摘要)
    套件載入器 = 已發布技能套件載入器(發布.技能套件發布根, 快照庫)
    工具庫 = 工具發布庫()
    模型表: dict[str, object] = {}
    編排器 = None
    Planner資源 = None
    套件發布器 = 端點發布服務 = 憑證封套 = 管理服務 = 憑證管理服務 = None
    憑證管理世代 = None
    try:
        發布.工具發布安裝器(工具庫)
        原模型表 = 發布.模型供應商註冊表工廠()
        if type(原模型表) is not dict:
            raise ValueError("模型供應商註冊表無效") from None
        模型表.update(原模型表)
        if not 模型表 or any(type(鍵) is not str or not 鍵 or 值 is None for 鍵, 值 in 模型表.items()):
            raise ValueError("模型供應商註冊表無效") from None
        Runtime橋接 = 建立發布執行嘗試橋接(
            發布快照儲存庫=快照庫, 技能套件載入器=套件載入器,
            工具發布庫=工具庫, 模型供應商註冊表=模型表,
            工具呼叫紀錄器=呼叫庫.附加工具呼叫,
        )
        台帳 = InvocationLedger橋接(呼叫庫)
        編排器 = 外部呼叫編排器(
            解析器, 呼叫庫, 憑證,
            解析未找到型別=目前版本不存在錯誤, 釘選型別=已釘選版本,
            驗證型別=憑證驗證結果, 驗證狀態型別=憑證驗證狀態,
            階段型別=擷取階段, 準備擷取=準備呼叫擷取, 寫入擷取=寫入呼叫擷取,
            限流決策型別=限流決策, 提交雙層計數=限流器.提交,
            驗證輸入=驗證釘選輸入結構, 開始執行嘗試=台帳.開始執行嘗試,
            執行嘗試=Runtime橋接, 驗證輸出=驗證釘選輸出結構,
            記錄執行嘗試=台帳.記錄執行嘗試,
            工作階段儲存庫=工作階段庫,
        )
        代理.安裝(編排器)
        if 發布.Planner設定 is not None:
            if type(草稿代理) is not 延遲草稿規劃服務:
                raise ValueError("Published生產組裝無效") from None
            Planner資源 = 建立生產Planner資源(
                發布.Planner設定, 生產.資料庫路徑, 工具庫, 草稿代理,
            )
        if 發布.憑證封套工廠 is not None:
            憑證封套 = 發布.憑證封套工廠()
            if type(憑證封套) is not AESGCM憑證封套:
                raise ValueError("Published憑證封套無效") from None
            if 憑證管理代理 is not None:
                if type(憑證管理代理) is not 延遲憑證管理服務:
                    raise ValueError("Published生產組裝無效") from None
                憑證管理服務 = SQLite憑證管理服務(資料庫, 憑證封套)
                憑證管理世代 = 憑證管理代理.安裝(憑證管理服務)
        if 發布.憑證封套工廠 is not None and Planner資源 is not None:
            if type(管理代理) is not 延遲發布管理服務 or type(套件協調器) is not 技能套件協調器:
                raise ValueError("Published生產組裝無效") from None
            草稿Aggregate = Planner資源.取得規劃服務()
            擁有者解析器 = Planner資源.取得擁有者解析器()
            共用工具庫 = Planner資源.取得工具發布庫()
            if 草稿Aggregate is None or 擁有者解析器 is None or 共用工具庫 is not 工具庫:
                raise ValueError("Published生產組裝無效") from None
            套件發布器 = 技能套件發布器(發布.技能套件發布根)
            端點發布服務 = SQLite端點發布服務(
                資料庫,
                lambda: f"endpoint-{uuid.uuid4().hex}",
                lambda: f"version-{uuid.uuid4().hex}",
                lambda: f"credential-{uuid.uuid4().hex}",
                lambda: f"service-account-{uuid.uuid4().hex}",
                time.time,
            )
            版本服務 = SQLite版本配置服務(
                資料庫, lambda: f"version-{uuid.uuid4().hex}", time.time,
            )
            管理服務 = 發布管理協調器(
                草稿服務=草稿Aggregate,
                擁有者解析器=擁有者解析器,
                套件發布器物件=套件發布器,
                套件協調器物件=套件協調器,
                端點發布服務=端點發布服務,
                憑證封套=憑證封套,
                版本配置服務=版本服務,
                模型設定={
                    "provider": 生產.模型供應器,
                    "model": 生產.模型名稱,
                    "temperature": 0.0,
                    "max_tokens": 4096,
                    "timeout_seconds": 60.0,
                    "structured_output": True,
                    "schema_retry_count": 1,
                },
            )
            管理代理.安裝(管理服務)
        return 生產Published執行資源(
            代理, 編排器, 工具庫, 模型表, Planner資源,
            管理代理 if 管理服務 is not None else None,
            管理服務,
            套件協調器 if 管理服務 is not None else None,
            套件發布器, 端點發布服務,
            憑證管理代理 if 憑證管理服務 is not None else None, 憑證管理服務, 憑證管理世代,
        )
    except BaseException as 啟動錯誤:
        清理控制流程: BaseException | None = None
        try:
            if type(管理代理) is 延遲發布管理服務 and type(管理服務) is 發布管理協調器:
                管理代理.清除(管理服務)
        except BaseException as 清理錯誤:
            if isinstance(清理錯誤, _控制流程例外):
                清理控制流程 = 清理錯誤
            try:
                if type(管理代理) is 延遲發布管理服務 and type(管理服務) is 發布管理協調器:
                    管理代理._撤銷並等待(管理服務, 允許已撤銷=True)
            except BaseException as 撤銷錯誤:
                if isinstance(撤銷錯誤, _控制流程例外) and 清理控制流程 is None:
                    清理控制流程 = 撤銷錯誤
        try:
            if (type(憑證管理代理) is 延遲憑證管理服務
                    and type(憑證管理服務) is SQLite憑證管理服務
                    and type(憑證管理世代) is int):
                憑證管理代理.清除(憑證管理服務, 憑證管理世代)
        except BaseException as 清理錯誤:
            if isinstance(清理錯誤, _控制流程例外) and 清理控制流程 is None:
                清理控制流程 = 清理錯誤
        finally:
            try:
                if (type(憑證管理代理) is 延遲憑證管理服務
                        and type(憑證管理服務) is SQLite憑證管理服務):
                    延遲憑證管理服務._撤銷並等待(
                        憑證管理代理, 憑證管理服務, 憑證管理世代, 允許解析目前世代=True,
                    )
            except BaseException as 清理錯誤:
                if isinstance(清理錯誤, _控制流程例外) and 清理控制流程 is None:
                    清理控制流程 = 清理錯誤
            憑證管理服務 = None
            管理服務 = 憑證封套 = 端點發布服務 = 套件發布器 = None
        try:
            if Planner資源 is not None:
                Planner資源._清除同步()
        except BaseException as 清理錯誤:
            if isinstance(清理錯誤, _控制流程例外) and 清理控制流程 is None:
                清理控制流程 = 清理錯誤
        try:
            if 編排器 is not None:
                代理.清除(編排器)
        except BaseException as 清理錯誤:
            if isinstance(清理錯誤, _控制流程例外) and 清理控制流程 is None:
                清理控制流程 = 清理錯誤
        finally:
            模型表.clear()
            try:
                工具庫.清除所有發布()
            except BaseException as 清理錯誤:
                if isinstance(清理錯誤, _控制流程例外) and 清理控制流程 is None:
                    清理控制流程 = 清理錯誤
        if isinstance(啟動錯誤, _控制流程例外):
            raise
        if 清理控制流程 is not None:
            清理控制流程.__cause__ = None
            清理控制流程.__context__ = None
            raise 清理控制流程
        啟動錯誤.__cause__ = None
        啟動錯誤.__context__ = None
        raise
__all__ = (
    "Published生產設定", "延遲外部呼叫編排器", "生產Published執行資源",
    "生產Published執行建構器", "生產Controller建構器", "延遲憑證管理服務",
)
