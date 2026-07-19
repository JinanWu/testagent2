"""傳輸中立的憑證管理安全投影與錯誤分類。"""

from __future__ import annotations

import ipaddress
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class 憑證管理狀態(str, Enum):
    """管理介面可揭露的憑證生命週期狀態。"""

    有效 = "active"
    閒置 = "inactive"
    已過期 = "expired"
    已撤銷 = "revoked"


class 憑證管理錯誤(RuntimeError):
    """不暴露資料庫或憑證內容的管理錯誤基底。"""


class 找不到端點憑證錯誤(憑證管理錯誤):
    """端點或端點內憑證不存在；亦包含外部擁有者。"""


class 端點生命週期衝突錯誤(憑證管理錯誤):
    """已擁有端點的狀態不允許要求的操作。"""


class 憑證管理操作錯誤(憑證管理錯誤):
    """資料毀損或基礎設施失敗。"""


def _安全文字(值: object, 最大長度: int) -> bool:
    """檢查管理投影中的非秘密文字。"""
    if type(值) is not str or 值 != 值.strip() or not 1 <= len(值) <= 最大長度:
        return False
    小寫文字 = 值.lower()
    return not any(ord(字元) < 32 for 字元 in 值) and not any(
        標記 in 小寫文字 for 標記 in ("pk_", "sk_", "sk-", "bearer")
    ) and not (len(值) == 64 and all(字元 in "0123456789abcdef" for 字元 in 小寫文字))


def _時間(值: object) -> bool:
    """檢查非負且有限的時間值。"""
    try:
        return type(值) in (int, float) and math.isfinite(float(值)) and 值 >= 0
    except (OverflowError, ValueError):
        return False


def _清除DTO(實例: object, 欄位名稱: tuple[str, ...]) -> None:
    """validation失敗前移除production frame可達的caller資料。"""
    for 名稱 in 欄位名稱:
        try:
            object.__setattr__(實例, 名稱, None)
        except (AttributeError, TypeError):
            pass


@dataclass(frozen=True, slots=True)
class 憑證摘要:
    """不含明文金鑰與密文材料的憑證摘要。"""

    憑證識別碼: str
    名稱: str
    用途: str
    金鑰前綴: str
    金鑰末四碼: str
    狀態: 憑證管理狀態
    到期時間: float
    最後使用時間: float | None
    建立時間: float
    撤銷時間: float | None
    IP允許清單: tuple[str, ...]
    速率限制請求數: int

    def __post_init__(self) -> None:
        """驗證摘要欄位並在失敗前清除內容。"""
        時間有效 = (
            _時間(self.到期時間) and _時間(self.建立時間)
            and self.到期時間 > self.建立時間
            and (self.最後使用時間 is None or (
                _時間(self.最後使用時間) and self.建立時間 <= self.最後使用時間
            ))
            and (self.撤銷時間 is None or (
                _時間(self.撤銷時間) and self.建立時間 <= self.撤銷時間
            ))
        )
        if (
            not _安全文字(self.憑證識別碼, 128)
            or not _安全文字(self.名稱, 256)
            or not _安全文字(self.用途, 2048)
            or type(self.金鑰前綴) is not str or not 1 <= len(self.金鑰前綴) <= 32
            or type(self.金鑰末四碼) is not str or len(self.金鑰末四碼) != 4
            or type(self.狀態) is not 憑證管理狀態
            or not 時間有效
            or not _允許清單有效(self.IP允許清單)
            or type(self.速率限制請求數) is not int
            or not 1 <= self.速率限制請求數 <= 10_000
        ):
            _清除DTO(self, (
                "憑證識別碼", "名稱", "用途", "金鑰前綴", "金鑰末四碼", "狀態",
                "到期時間", "最後使用時間", "建立時間", "撤銷時間", "IP允許清單",
                "速率限制請求數", "初始金鑰",
            ))
            時間有效 = None
            raise ValueError("憑證摘要無效") from None


@dataclass(frozen=True, slots=True)
class 憑證列表結果:
    """具數量上限的憑證摘要列表。"""

    項目: tuple[憑證摘要, ...]

    def __post_init__(self) -> None:
        """驗證列表只含可信摘要。"""
        if type(self.項目) is not tuple or len(self.項目) > 10_000 or any(
            type(項目值) is not 憑證摘要 for 項目值 in self.項目
        ):
            _清除DTO(self, ("項目",))
            raise ValueError("憑證列表無效") from None


def _允許清單有效(值清單: object) -> bool:
    """檢查已排序且正規化的 IP 允許清單。"""
    if type(值清單) is not tuple or len(值清單) > 256:
        return False
    前一排序鍵 = None
    try:
        for 值 in 值清單:
            if type(值) is not str or not 1 <= len(值) <= 128 or "%" in 值:
                return False
            解析值 = ipaddress.ip_network(值, strict=False) if "/" in 值 else ipaddress.ip_address(值)
            正規值 = str(解析值)
            排序鍵 = (解析值.version, 正規值)
            if 正規值 != 值 or (前一排序鍵 is not None and 排序鍵 <= 前一排序鍵):
                return False
            前一排序鍵 = 排序鍵
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class 憑證建立命令:
    """傳輸中立的憑證建立輸入。"""

    名稱: str
    用途: str
    到期時間: float
    IP允許清單: tuple[str, ...]
    速率限制請求數: int

    def __post_init__(self) -> None:
        """驗證建立命令的安全範圍。"""
        if (
            not _安全文字(self.名稱, 256) or not _安全文字(self.用途, 2048)
            or not _時間(self.到期時間) or not _允許清單有效(self.IP允許清單)
            or type(self.速率限制請求數) is not int
            or not 1 <= self.速率限制請求數 <= 10_000
        ):
            _清除DTO(self, ("名稱", "用途", "到期時間", "IP允許清單", "速率限制請求數"))
            raise ValueError("憑證建立命令無效") from None


@dataclass(frozen=True, slots=True)
class 一次性憑證建立收據(憑證摘要):
    """僅於建立成功時交付一次初始金鑰的收據。"""

    初始金鑰: str = field(repr=False)

    def __post_init__(self) -> None:
        """驗證摘要與不可顯示的初始金鑰。"""
        super(一次性憑證建立收據, self).__post_init__()
        if type(self.初始金鑰) is not str or not 1 <= len(self.初始金鑰) <= 512:
            _清除DTO(self, (
                "憑證識別碼", "名稱", "用途", "金鑰前綴", "金鑰末四碼", "狀態",
                "到期時間", "最後使用時間", "建立時間", "撤銷時間", "IP允許清單",
                "速率限制請求數", "初始金鑰",
            ))
            raise ValueError("憑證建立收據無效") from None


@dataclass(frozen=True, slots=True)
class 憑證撤銷收據:
    """不含秘密內容的憑證撤銷結果。"""

    憑證識別碼: str
    撤銷時間: float
    是否已撤銷: bool

    def __post_init__(self) -> None:
        """驗證撤銷收據欄位。"""
        if (
            not _安全文字(self.憑證識別碼, 128) or not _時間(self.撤銷時間)
            or type(self.是否已撤銷) is not bool
        ):
            _清除DTO(self, ("憑證識別碼", "撤銷時間", "是否已撤銷"))
            raise ValueError("憑證撤銷收據無效") from None


class 憑證管理服務(Protocol):
    """憑證管理能力的傳輸中立協定。"""

    def 列出憑證(
        self, *, 端點識別碼: str, 擁有者使用者識別碼: str,
    ) -> 憑證列表結果:
        """列出擁有者端點內的安全摘要。"""
        ...

    def 建立憑證(
        self, *, 端點識別碼: str, 擁有者使用者識別碼: str, 請求: 憑證建立命令,
    ) -> 一次性憑證建立收據:
        """建立憑證並回傳一次性初始金鑰。"""
        ...

    def 撤銷憑證(
        self, *, 端點識別碼: str, 憑證識別碼: str, 擁有者使用者識別碼: str,
        是否管理者: bool, 請求識別碼: str,
    ) -> 憑證撤銷收據:
        """依複合範圍撤銷憑證。"""
        ...
