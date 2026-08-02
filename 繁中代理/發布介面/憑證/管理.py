"""SQLite憑證管理協定的authoritative安全adapter。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, NoReturn

from ..憑證管理契約 import (
    一次性憑證建立收據, 找不到端點憑證錯誤, 憑證建立命令, 憑證列表結果,
    憑證摘要, 憑證撤銷收據, 憑證管理操作錯誤, 憑證管理狀態, 憑證管理錯誤,
    端點生命週期衝突錯誤,
)
from ..領域模型 import WebOwnerPrincipal
from .加密 import AESGCM憑證封套
from .儲存庫 import SQLite憑證儲存庫, 建立憑證結果
from .管理操作 import 列出管理憑證, 撤銷管理憑證

_控制例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)


def _重新拋出控制(錯誤: BaseException) -> NoReturn:
    """清除舊鏈並保留exact控制例外。"""
    BaseException.__setattr__(錯誤, "__cause__", None)
    BaseException.__setattr__(錯誤, "__context__", None)
    BaseException.__setattr__(錯誤, "__suppress_context__", True)
    try:
        raise 錯誤
    except _控制例外:
        del 錯誤
        raise


class SQLite憑證管理服務:
    """以owner composite scope提供未含秘密的憑證管理投影。"""

    def __init__(
        self, 資料庫: str | Path, 封套: AESGCM憑證封套 | None = None,
        *, 時鐘: Callable[[], float] = time.time,
    ) -> None:
        """建立管理轉接器並保留注入的資料庫、封套與時鐘。"""
        路徑 = 有效封套 = 有效時鐘 = None
        是否失敗 = False
        控制: list[BaseException] = []
        try:
            if not callable(時鐘) or (封套 is not None and type(封套) is not AESGCM憑證封套):
                raise ValueError
            路徑 = Path(資料庫)
        except _控制例外 as 錯誤:
            控制.append(錯誤)
        except BaseException:
            是否失敗 = True
        if not 是否失敗 and not 控制:
            有效封套, 有效時鐘 = 封套, 時鐘
        self.__dict__.clear()
        del 資料庫, 封套, 時鐘
        if 控制:
            del self
            _重新拋出控制(控制.pop())
        if 是否失敗 or 路徑 is None:
            路徑 = None
            del self
            raise 憑證管理操作錯誤("憑證管理失敗") from None
        self._資料庫, self._時鐘, self._封套 = 路徑, 有效時鐘, 有效封套
        有效封套 = 有效時鐘 = 路徑 = None

    def 建立憑證(
        self, *, 端點識別碼: str, 擁有者使用者識別碼: str, 請求: 憑證建立命令,
    ) -> 一次性憑證建立收據:
        """完整preflight請求後移除self，再建立並重建一次性收據。"""
        名稱 = 用途 = 到期時間 = IP允許清單 = 速率限制請求數 = None
        資料庫 = 封套 = 時鐘 = 來源 = 摘要 = 結果 = 初始金鑰 = None
        領域錯誤 = None
        是否失敗 = False
        try:
            if type(請求) is not 憑證建立命令:
                raise ValueError
            正規請求 = 憑證建立命令(
                請求.名稱, 請求.用途, 請求.到期時間, 請求.IP允許清單, 請求.速率限制請求數,
            )
            名稱, 用途, 到期時間 = 正規請求.名稱, 正規請求.用途, 正規請求.到期時間
            IP允許清單, 速率限制請求數 = 正規請求.IP允許清單, 正規請求.速率限制請求數
            正規請求 = None
        except BaseException:
            是否失敗 = True
        請求 = None
        if 是否失敗:
            del self, 端點識別碼, 擁有者使用者識別碼, 請求
            名稱 = 用途 = 到期時間 = IP允許清單 = 速率限制請求數 = None
            raise 憑證管理操作錯誤("憑證管理失敗") from None
        資料庫, 封套, 時鐘 = self._資料庫, self._封套, self._時鐘
        del self, 請求
        if type(封套) is not AESGCM憑證封套:
            資料庫 = 封套 = 時鐘 = None
            del 端點識別碼, 擁有者使用者識別碼
            名稱 = 用途 = 到期時間 = IP允許清單 = 速率限制請求數 = None
            raise 憑證管理操作錯誤("憑證管理失敗") from None
        控制: list[BaseException] = []
        是否失敗 = False
        try:
            來源 = SQLite憑證儲存庫(資料庫, 封套, clock=時鐘).建立管理憑證(
                端點識別碼, WebOwnerPrincipal(擁有者使用者識別碼), name=名稱, purpose=用途,
                expires_at=到期時間, ip_allowlist=IP允許清單,
                rate_limit_requests=速率限制請求數,
            )
            if type(來源) is not 建立憑證結果:
                raise ValueError
            摘要 = 憑證摘要(
                來源.credential_id, 來源.name, 來源.purpose, 來源.key_prefix,
                來源.key_last4, 憑證管理狀態.有效, 來源.expires_at, None,
                來源.created_at, None, 來源.ip_allowlist, 來源.rate_limit_requests,
            )
            初始金鑰 = 來源.api_key
            結果 = 一次性憑證建立收據(
                摘要.憑證識別碼, 摘要.名稱, 摘要.用途, 摘要.金鑰前綴,
                摘要.金鑰末四碼, 摘要.狀態, 摘要.到期時間, 摘要.最後使用時間,
                摘要.建立時間, 摘要.撤銷時間, 摘要.IP允許清單,
                摘要.速率限制請求數, 初始金鑰,
            )
        except 憑證管理錯誤 as 錯誤:
            領域錯誤 = type(錯誤)
        except _控制例外 as 錯誤:
            控制.append(錯誤)
        except BaseException:
            是否失敗 = True
        資料庫 = 封套 = 時鐘 = 來源 = 摘要 = 初始金鑰 = None
        名稱 = 用途 = 到期時間 = IP允許清單 = 速率限制請求數 = None
        del 端點識別碼, 擁有者使用者識別碼
        if 控制:
            結果 = None
            _重新拋出控制(控制.pop())
        if 領域錯誤 is not None:
            結果 = None
            訊息 = "找不到端點或憑證" if 領域錯誤 is 找不到端點憑證錯誤 else (
                "端點生命週期衝突" if 領域錯誤 is 端點生命週期衝突錯誤 else "憑證管理失敗"
            )
            領域錯誤 = None
            raise (找不到端點憑證錯誤(訊息) if 訊息 == "找不到端點或憑證" else (
                端點生命週期衝突錯誤(訊息) if 訊息 == "端點生命週期衝突"
                else 憑證管理操作錯誤(訊息)
            )) from None
        if 是否失敗 or 結果 is None:
            結果 = None
            raise 憑證管理操作錯誤("憑證管理失敗") from None
        return 結果

    def 撤銷憑證(
        self, *, 端點識別碼: str, 憑證識別碼: str, 擁有者使用者識別碼: str,
        是否管理者: bool, 請求識別碼: str,
    ) -> 憑證撤銷收據:
        """先移除可達master key的self，再委派atomic revoke。"""
        資料庫, 時鐘 = self._資料庫, self._時鐘
        del self
        try:
            return 撤銷管理憑證(
                資料庫, 時鐘, 端點識別碼, 憑證識別碼, 擁有者使用者識別碼,
                是否管理者, 請求識別碼,
            )
        finally:
            資料庫 = 時鐘 = None
            端點識別碼 = 憑證識別碼 = 擁有者使用者識別碼 = 請求識別碼 = None
            是否管理者 = None

    def 列出憑證(self, *, 端點識別碼: str, 擁有者使用者識別碼: str) -> 憑證列表結果:
        """先移除可達master key的self，再執行唯讀交易。"""
        資料庫, 時鐘 = self._資料庫, self._時鐘
        del self
        try:
            return 列出管理憑證(資料庫, 時鐘, 端點識別碼, 擁有者使用者識別碼)
        finally:
            資料庫 = 時鐘 = 端點識別碼 = 擁有者使用者識別碼 = None
