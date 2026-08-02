"""發布介面公開參照 DTO 與安全稽核 metadata 領域模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import math

import re

from types import MappingProxyType

from typing import Any, Mapping

import unicodedata

from .嚴格JSON import 建立正規JSON, 解析嚴格JSON

JsonObject = dict[str, Any]

class _公開DTO:
    """提供公開 DTO 共用的 JSON 輸出行為。"""

    def to_json(self) -> JsonObject:
        """回傳公開契約 JSON 物件。

        這個方法沒有參數，會依 dataclass 欄位順序產生新的 dict；不會修改實例，
        也不會觸發外部副作用。實例若不是 dataclass，會由 dataclasses.asdict
        拋出 TypeError。
        """
        return asdict(self)

class EndpointRef(_公開DTO):
    """公開端點版本的最小參照。"""

    id: str
    slug: str
    version: int

class InvocationRef(_公開DTO):
    """公開呼叫紀錄的最小參照。"""

    id: str
    request_id: str
    session_id: str | None = None

class PublishedUsage(_公開DTO):
    """公開回應中的用量摘要。"""

    total_tokens: int | None = None

class PublishedWarning(_公開DTO):
    """公開回應中的非致命警告。"""

    code: str
    message: str

class PublishedError(_公開DTO):
    """公開回應中的frozen錯誤摘要。

    ``to_json``是唯一公開serialization API；內部不可變快照不支援
    ``dataclasses.asdict``或``dataclasses.replace``。
    """

    code: str
    message: str
    __annotations__["details"] = Any
    locals()["details"] = field(default=None, repr=False)
    _細節正規JSON: str = field(default="{}", repr=False)

    def __init__(self, *位置參數: Any, **命名參數: Any) -> None:
        """驗證固定字串欄位，並建立 details 的深層不可變 JSON 快照。"""
        錯誤 = False
        未提供 = object()
        代碼: Any = 未提供
        訊息: Any = 未提供
        細節: Any = None
        原始細節: Any = None
        正規文字: str | None = None
        快照: Any = MappingProxyType({})
        計數 = [0, 0, 0]
        object.__setattr__(self, "code", "")
        object.__setattr__(self, "message", "")
        object.__setattr__(self, "details", MappingProxyType({}))
        object.__setattr__(self, "_細節正規JSON", "{}")
        try:
            if len(位置參數) > 3 or not set(命名參數).issubset({"code", "message", "details"}):
                錯誤 = True
            elif any(索引 < len(位置參數) and 名稱 in 命名參數 for 索引, 名稱 in enumerate(("code", "message", "details"))):
                錯誤 = True
            else:
                代碼 = 位置參數[0] if len(位置參數) > 0 else 命名參數.get("code", 未提供)
                訊息 = 位置參數[1] if len(位置參數) > 1 else 命名參數.get("message", 未提供)
                細節 = 位置參數[2] if len(位置參數) > 2 else 命名參數.get("details")
                原始細節 = {} if 細節 is None else 細節
            if 錯誤:
                pass
            elif not _PublishedError文字合法(代碼, 128):
                錯誤 = True
            elif not _PublishedError文字合法(訊息, 512):
                錯誤 = True
            elif type(原始細節) is not dict:
                錯誤 = True
            elif not _PublishedError細節合法(原始細節, 0, set(), 計數):
                錯誤 = True
            else:
                正規文字 = 建立正規JSON(原始細節)
                if len(正規文字.encode("utf-8")) > 32768:
                    錯誤 = True
                else:
                    快照 = _建立不可變JSON快照(原始細節)
        except Exception:
            錯誤 = True

        if 錯誤:
            位置參數 = ()
            命名參數 = {}
            未提供 = 代碼 = 訊息 = 細節 = 原始細節 = 正規文字 = 快照 = 計數 = None
            索引 = 名稱 = None
            raise ValueError("PublishedError 不符合公開契約") from None

        object.__setattr__(self, "code", 代碼)
        object.__setattr__(self, "message", 訊息)
        object.__setattr__(self, "details", 快照)
        assert 正規文字 is not None
        object.__setattr__(self, "_細節正規JSON", 正規文字)

    def to_json(self) -> JsonObject:
        """回傳固定鍵序與 fresh ordinary details containers。"""
        細節 = 解析嚴格JSON(self._細節正規JSON)
        if type(細節) is not dict:
            raise ValueError("PublishedError 不符合公開契約")
        return {
            "code": self.code,
            "message": self.message,
            "details": 細節,
        }

class InvokeEnvelope:
    """公開呼叫結果信封，固定成功與失敗回應的共同外部契約。"""

    ok: bool
    endpoint: EndpointRef | None
    invocation: InvocationRef | None
    data: Any
    usage: PublishedUsage | None
    warnings: tuple[PublishedWarning, ...]
    error: PublishedError | None

    def __init__(
        self,
        *,
        ok: bool,
        endpoint: EndpointRef | None,
        invocation: InvocationRef | None,
        data: Any = None,
        usage: PublishedUsage | None = None,
        warnings: tuple[PublishedWarning, ...] | list[PublishedWarning] | None = None,
        error: PublishedError | None = None,
    ) -> None:
        """驗證信封 invariant，並對 data 建立深層不可變 JSON 快照。"""
        try:
            if type(ok) is not bool:
                raise ValueError("InvokeEnvelope 狀態不符合公開契約")
            for 值, 型別 in (
                (endpoint, EndpointRef),
                (invocation, InvocationRef),
                (usage, PublishedUsage),
                (error, PublishedError),
            ):
                if 值 is not None and type(值) is not 型別:
                    raise ValueError("InvokeEnvelope DTO 不符合公開契約")

            if warnings is None:
                frozen_warnings = ()
            else:
                frozen_warnings = tuple(warnings)
                for warning in frozen_warnings:
                    if type(warning) is not PublishedWarning:
                        raise ValueError("InvokeEnvelope 警告不符合公開契約")
            正規錯誤 = None if error is None else _重建PublishedError(error)
            if ok:
                if endpoint is None or invocation is None or error is not None:
                    raise ValueError("InvokeEnvelope 成功狀態不符合公開契約")
                frozen_data = _建立不可變JSON快照(data)
            else:
                if error is None or data is not None or usage is not None:
                    raise ValueError("InvokeEnvelope 失敗狀態不符合公開契約")
                frozen_data = None

            for 欄位, 值 in (
                ("ok", ok),
                ("endpoint", endpoint),
                ("invocation", invocation),
                ("data", frozen_data),
                ("usage", usage),
                ("warnings", frozen_warnings),
                ("error", 正規錯誤),
            ):
                object.__setattr__(self, 欄位, 值)
        except Exception:
            for 欄位, 值 in (
                ("ok", False),
                ("endpoint", None),
                ("invocation", None),
                ("data", None),
                ("usage", None),
                ("warnings", ()),
                ("error", None),
            ):
                object.__setattr__(self, 欄位, 值)
            ok = endpoint = invocation = data = usage = warnings = error = None
            frozen_warnings = frozen_data = 正規錯誤 = 欄位 = 值 = 型別 = warning = None
            raise

    def to_json(self) -> JsonObject:
        """回傳固定鍵序且只含 ordinary JSON container 的公開信封。"""
        return {
            "ok": self.ok,
            "endpoint": None if self.endpoint is None else self.endpoint.to_json(),
            "invocation": None if self.invocation is None else self.invocation.to_json(),
            "data": _解凍JSON值(self.data),
            "usage": None if self.usage is None else self.usage.to_json(),
            "warnings": [warning.to_json() for warning in self.warnings],
            "error": None if self.error is None else PublishedError.to_json(self.error),
        }

class ServiceAccountSnapshotRef(_公開DTO):
    """服務帳號權限快照的公開參照。"""

    service_account_id: str
    endpoint_version_id: str
    permission_snapshot_digest: str

def _建立不可變JSON快照(資料: Any) -> Any:
    """重用嚴格 JSON 審核後建立深層不可變快照。"""
    正規文字: str | None = None
    解析結果: Any = None
    try:
        正規文字 = 建立正規JSON(資料)
        解析結果 = 解析嚴格JSON(正規文字)
        return _凍結JSON值(解析結果)
    except Exception:
        資料 = 正規文字 = 解析結果 = None
        raise

def _PublishedError文字合法(值: Any, 最大長度: int) -> bool:
    """確認 PublishedError 固定字串欄位為 bounded exact str 且無控制字元。"""
    return (
        type(值) is str
        and 0 < len(值) <= 最大長度
        and not any(unicodedata.category(字元) == "Cc" for 字元 in 值)
    )

def _PublishedError細節合法(
    值: Any,
    深度: int,
    路徑: set[int],
    計數: list[int],
) -> bool:
    """遞迴確認bounded exact JSON，並在serialize前限制節點與估計bytes。"""
    值型別 = type(值)
    計數[1] += 1
    if 計數[1] > 1024:
        return False
    if 值 is None or 值型別 is bool or 值型別 is int:
        計數[2] += 4 if 值 is None else len(str(值))
        return 計數[2] <= 32768
    if 值型別 is float:
        計數[2] += len(repr(值))
        return math.isfinite(值) and 計數[2] <= 32768
    if 值型別 is str:
        計數[2] += len(值.encode("utf-8")) + 2
        return len(值) <= 4096 and 計數[2] <= 32768
    if 值型別 not in (dict, list) or 深度 > 8:
        return False
    容器id = id(值)
    if 容器id in 路徑:
        return False
    路徑.add(容器id)
    try:
        計數[2] += 2
        if 計數[2] > 32768:
            return False
        if 值型別 is list:
            return all(_PublishedError細節合法(項目, 深度 + 1, 路徑, 計數) for 項目 in 值)
        for 鍵, 項目 in 值.items():
            計數[0] += 1
            if type(鍵) is not str or len(鍵) > 4096 or 計數[0] > 128:
                return False
            計數[2] += len(鍵.encode("utf-8")) + 3
            if 計數[2] > 32768:
                return False
            if not _PublishedError細節合法(項目, 深度 + 1, 路徑, 計數):
                return False
        return True
    finally:
        路徑.remove(容器id)

def _重建PublishedError(原始錯誤: Any) -> PublishedError:
    """只從exact scalar與canonical JSON文字重建可信公開錯誤。"""
    try:
        if type(原始錯誤) is not PublishedError:
            raise ValueError
        代碼 = 原始錯誤.code
        訊息 = 原始錯誤.message
        正規文字 = 原始錯誤._細節正規JSON
        if type(正規文字) is not str:
            raise ValueError
        細節 = 解析嚴格JSON(正規文字)
        if type(細節) is not dict:
            raise ValueError
        return PublishedError(代碼, 訊息, 細節)
    except Exception:
        原始錯誤 = 代碼 = 訊息 = 正規文字 = 細節 = None
        raise ValueError("PublishedError 不符合公開契約") from None

def _凍結JSON值(值: Any) -> Any:
    """將已審核 JSON value 轉為 tuple 與 read-only mapping 組成的快照。"""
    if isinstance(值, list):
        return tuple(_凍結JSON值(項目) for 項目 in 值)
    if isinstance(值, dict):
        return MappingProxyType({鍵: _凍結JSON值(項目) for 鍵, 項目 in 值.items()})
    return 值

def _解凍JSON值(值: Any) -> Any:
    """將內部不可變 JSON 快照轉回 ordinary dict、list 與 scalar。"""
    if isinstance(值, tuple):
        return [_解凍JSON值(項目) for 項目 in 值]
    if isinstance(值, Mapping):
        return {鍵: _解凍JSON值(項目) for 鍵, 項目 in 值.items()}
    return 值
