"""INV I03 短名、憑證、雙層計數與輸入閘門的傳輸中立編排。"""

from __future__ import annotations

import gc
import json
import math
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable

from ..契約 import 建立成功信封
from ..領域模型 import EndpointRef, InvocationRef, InvokeEnvelope, PublishedUsage, PublishedWarning
from .錯誤映射 import 錯誤映射結果, 映射呼叫錯誤

_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_JSON最大深度 = 8
_JSON最大節點 = 1024
_JSON最大容器項目 = 128
_JSON最大UTF8位元組 = 32768
_警告最大數量 = 64
_警告代碼最大UTF8位元組 = 256
_警告訊息最大UTF8位元組 = 2048
_警告合計最大UTF8位元組 = 16384
_釘選識別格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


def _安裝成功結果來源(結果類型: type) -> type:
    """以class method closures封存來源與一次性construction狀態。"""
    from threading import RLock
    from weakref import WeakKeyDictionary

    登錄 = WeakKeyDictionary()
    待初始化 = WeakKeyDictionary()
    鎖 = RLock()

    def 建立(cls: type, *位置參數: object, **命名參數: object) -> object:
        """只為exact公開類別建立待一次性初始化的weak identity。"""
        if cls is not 結果類型:
            raise ValueError("呼叫成功結果不符合契約") from None
        結果 = object.__new__(cls)
        with 鎖:
            待初始化[結果] = True
        return 結果

    def 初始化(self: object, 信封: InvokeEnvelope) -> None:
        """只消耗一次genuine __new__ provenance，成功後原子發布文字。"""
        with 鎖:
            if 待初始化.pop(self, None) is not True or self in 登錄:
                raise ValueError("呼叫成功結果不符合契約") from None
        安全信封 = 正規文字 = None
        失敗 = False
        try:
            安全信封, 正規文字 = _重建成功投影(信封)
            object.__setattr__(self, "status_code", 200)
            object.__setattr__(self, "envelope", 安全信封)
            with 鎖:
                登錄[self] = 正規文字
        except _控制流程 as 控制:
            object.__setattr__(self, "status_code", 0)
            object.__setattr__(self, "envelope", None)
            self = 信封 = 安全信封 = 正規文字 = 失敗 = None
            _清理並重拋(控制)
        except BaseException:
            失敗 = True
        if 失敗:
            object.__setattr__(self, "status_code", 0)
            object.__setattr__(self, "envelope", None)
            self = 信封 = 安全信封 = 正規文字 = 失敗 = None
            raise ValueError("呼叫成功結果不符合契約") from None

    def 序列化(self: object) -> dict[str, object]:
        """核對公開信封後，只回放closure-owned registry snapshot。"""
        公開信封 = 安全信封 = None
        公開文字 = 登錄文字 = 輸出信封 = None
        失敗 = False
        try:
            if type(self) is not 結果類型:
                raise ValueError
            狀態碼 = object.__getattribute__(self, "status_code")
            if type(狀態碼) is not int or 狀態碼 != 200:
                raise ValueError
            with 鎖:
                登錄文字 = 登錄.get(self)
            if type(登錄文字) is not str:
                raise ValueError
            公開信封 = object.__getattribute__(self, "envelope")
            安全信封, 公開文字 = _重建成功投影(公開信封)
            if type(公開文字) is not str or str.__eq__(公開文字, 登錄文字) is not True:
                raise ValueError
            輸出信封 = json.loads(登錄文字)
            if type(輸出信封) is not dict:
                raise ValueError
            self = 狀態碼 = 公開信封 = 安全信封 = 公開文字 = 登錄文字 = None
            return {"status_code": 200, "headers": {}, "envelope": 輸出信封}
        except _控制流程 as 控制:
            self = 狀態碼 = 公開信封 = 安全信封 = 公開文字 = 登錄文字 = 輸出信封 = 失敗 = None
            _清理並重拋(控制)
        except BaseException:
            失敗 = True
        self = 狀態碼 = 公開信封 = 安全信封 = 公開文字 = 登錄文字 = 輸出信封 = 失敗 = None
        raise ValueError("呼叫成功結果不符合契約") from None

    setattr(結果類型, "__new__", 建立)
    setattr(結果類型, "__init__", 初始化)
    setattr(結果類型, "轉為JSON", 序列化)
    return 結果類型


class 外部呼叫編排錯誤(RuntimeError):
    """任一依賴違反固定管線契約時的無鏈結失敗。"""


def _重建可信釘選版本(原始釘選: object, 釘選型別: type) -> object:
    """一次讀取 exact slotted DTO，再以 captured immutable fields 重建可信釘選。"""
    if type(釘選型別) is not type or type(原始釘選) is not 釘選型別:
        raise ValueError("釘選版本不符合契約") from None
    固定欄位 = type.__getattribute__(釘選型別, "__slots__")
    if (type(固定欄位) is not tuple or not 固定欄位
            or type.__getattribute__(釘選型別, "__dictoffset__") != 0):
        raise ValueError("釘選版本不符合契約") from None
    欄位值 = []
    欄位名稱 = []
    for 欄位 in tuple.__iter__(固定欄位):
        if (type(欄位) is not str or not 欄位 or 欄位 in ("__dict__", "__weakref__")
                or 欄位 in 欄位名稱):
            raise ValueError("釘選版本不符合契約") from None
        值 = object.__getattribute__(原始釘選, 欄位)
        if type(值) not in (str, int, float, bool, bytes, type(None)):
            原始釘選 = 欄位值 = 欄位名稱 = 值 = None
            raise ValueError("釘選版本不符合契約") from None
        if type(值) is float and not math.isfinite(值):
            原始釘選 = 欄位值 = 欄位名稱 = 值 = None
            raise ValueError("釘選版本不符合契約") from None
        欄位名稱.append(欄位)
        欄位值.append(值)
    if not {"endpoint_id", "service_account_id", "version_id", "version_number"}.issubset(欄位名稱):
        原始釘選 = 欄位值 = 欄位名稱 = None
        raise ValueError("釘選版本不符合契約") from None
    for 識別欄位 in ("endpoint_id", "service_account_id", "version_id"):
        識別 = 欄位值[欄位名稱.index(識別欄位)]
        if type(識別) is not str or _釘選識別格式.fullmatch(識別) is None:
            原始釘選 = 欄位值 = 欄位名稱 = 識別 = 識別欄位 = None
            raise ValueError("釘選版本不符合契約") from None
    安全釘選 = 釘選型別(*欄位值)
    if type(安全釘選) is not 釘選型別:
        原始釘選 = 欄位值 = 欄位名稱 = 安全釘選 = None
        raise ValueError("釘選版本不符合契約") from None
    原始釘選 = 欄位值 = 欄位名稱 = None
    return 安全釘選


@dataclass(frozen=True, slots=True)
class _正規呼叫快照:
    """只保存 module-owned canonical JSON，按邊界產生 fresh exact tree。"""

    輸入文字: str
    中繼資料文字: str | None

    def 建立輸入(self) -> object:
        """回傳與 caller reference 完全脫離的 input tree。"""
        return json.loads(self.輸入文字)

    def 建立中繼資料(self) -> object | None:
        """回傳獨立 metadata object；角色永遠由編排器固定為 user。"""
        return None if self.中繼資料文字 is None else json.loads(self.中繼資料文字)


def _建立正規呼叫快照(輸入: object, 中繼資料: object | None) -> _正規呼叫快照:
    """以一次 bounded exact-builtins walk 建立 canonical detached snapshots。"""
    if 中繼資料 is not None and type(中繼資料) is not dict:
        raise ValueError("呼叫快照不符合契約") from None
    輸入快照 = _複製有界JSON(輸入)
    中繼快照 = None if 中繼資料 is None else _複製有界JSON(中繼資料)
    try:
        輸入文字 = json.dumps(
            輸入快照, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        中繼文字 = None if 中繼快照 is None else json.dumps(
            中繼快照, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        if len(輸入文字.encode("utf-8")) > _JSON最大UTF8位元組:
            raise ValueError
        if 中繼文字 is not None and len(中繼文字.encode("utf-8")) > _JSON最大UTF8位元組:
            raise ValueError
        return _正規呼叫快照(輸入文字, 中繼文字)
    except (TypeError, ValueError, OverflowError, RecursionError):
        輸入 = 中繼資料 = 輸入快照 = 中繼快照 = None
        raise ValueError("呼叫快照不符合契約") from None


def _複製有界JSON(根: object) -> object:
    """先驗 exact key/type 再查找或 hash，並在同一遞迴中完成 detachment。"""
    計數 = [0]
    路徑: set[int] = set()

    def 複製(值: object, 深度: int) -> object:
        """遞迴複製單一exact JSON節點並套用共享資源上限。"""
        計數[0] += 1
        if 計數[0] > _JSON最大節點 or 深度 > _JSON最大深度:
            raise ValueError
        值型別 = type(值)
        if 值 is None or 值型別 in (str, int, bool):
            if 值型別 is str and len(值.encode("utf-8")) > _JSON最大UTF8位元組:
                raise ValueError
            return 值
        if 值型別 is float:
            if not math.isfinite(值):
                raise ValueError
            return 值
        if 值型別 not in (list, dict) or len(值) > _JSON最大容器項目:
            raise ValueError
        識別 = id(值)
        if 識別 in 路徑:
            raise ValueError
        路徑.add(識別)
        try:
            if 值型別 is list:
                輸出列 = []
                for 項目 in list.__iter__(值):
                    輸出列.append(複製(項目, 深度 + 1))
                return 輸出列
            項目列 = tuple(dict.items(值))
            for 鍵, _ in 項目列:
                if type(鍵) is not str:
                    raise ValueError
            輸出物件 = {}
            for 鍵, 項目 in 項目列:
                輸出物件[鍵] = 複製(項目, 深度 + 1)
            return 輸出物件
        finally:
            路徑.remove(識別)

    try:
        return 複製(根, 0)
    except (ValueError, OverflowError, RecursionError, UnicodeError):
        根 = 路徑 = 計數 = None
        raise ValueError("呼叫快照不符合契約") from None


@dataclass(frozen=True, slots=True, init=False)
class 執行嘗試請求:
    """INV-owned exact request；每次嘗試保留同一 authoritative pin。"""

    pinned_version: object
    input: object
    metadata: object | None
    history: tuple[object, ...]
    attempt: int

    def __init__(self, 釘選版本: object, 輸入: object, 中繼資料: object | None, 嘗試次數: int,
                 歷史: tuple[object, ...] = ()) -> None:
        """驗證並保存一次釘選執行嘗試的不可變請求。

        參數：釘選版本、輸入、中繼資料、attempt 1/2 與 bounded successful history。
        返回值：無；完成 detached immutable request 的初始化。
        """
        if 釘選版本 is None or type(嘗試次數) is not int or 嘗試次數 not in (1, 2):
            釘選版本 = 輸入 = 中繼資料 = 嘗試次數 = None
            raise ValueError("執行嘗試請求不符合契約") from None
        object.__setattr__(self, "pinned_version", 釘選版本)
        object.__setattr__(self, "input", 輸入)
        object.__setattr__(self, "metadata", 中繼資料)
        object.__setattr__(self, "history", tuple(歷史))
        object.__setattr__(self, "attempt", 嘗試次數)


def _擷取有界警告純量(警告清單: object) -> tuple[tuple[str, str], ...]:
    """在複製／serialize 前限制警告 count、個別 UTF-8 bytes 與 aggregate。"""
    if type(警告清單) is not tuple or len(警告清單) > _警告最大數量:
        raise ValueError
    輸出 = []
    合計 = 0
    for 警告 in tuple.__iter__(警告清單):
        if type(警告) is not PublishedWarning:
            raise ValueError
        代碼 = object.__getattribute__(警告, "code")
        訊息 = object.__getattribute__(警告, "message")
        if type(代碼) is not str or not 代碼 or type(訊息) is not str or not 訊息:
            raise ValueError
        try:
            代碼長度 = len(str.encode(代碼, "utf-8"))
            訊息長度 = len(str.encode(訊息, "utf-8"))
        except UnicodeError:
            raise ValueError from None
        合計 += 代碼長度 + 訊息長度
        if (代碼長度 > _警告代碼最大UTF8位元組
                or 訊息長度 > _警告訊息最大UTF8位元組
                or 合計 > _警告合計最大UTF8位元組):
            raise ValueError
        輸出.append((代碼, 訊息))
    return tuple(輸出)


@dataclass(frozen=True, slots=True, init=False)
class 執行嘗試結果:
    """INV-owned terminal DTO；成功 data 只保存 detached canonical JSON。"""

    kind: str
    data: object
    usage: PublishedUsage | None
    warnings: tuple[PublishedWarning, ...]

    def __init__(
        self, 種類: str, 資料: object = None, 用量: PublishedUsage | None = None,
        警告清單: tuple[PublishedWarning, ...] = (),
    ) -> None:
        """驗證終局結果並重建可安全保存的公開用量與警告。"""
        失敗種類 = {
            "model_timeout", "tool_execution_failed", "tool_timeout",
            "endpoint_misconfigured", "internal_error",
        }
        合法 = type(種類) is str and (種類 == "success" or 種類 in 失敗種類)
        合法 = 合法 and type(警告清單) is tuple
        if 合法:
            合法 = len(警告清單) <= _警告最大數量
        if 種類 != "success":
            合法 = 合法 and 資料 is None and 用量 is None and not 警告清單
        if 用量 is not None:
            合法 = 合法 and type(用量) is PublishedUsage
            if 合法:
                權杖數 = object.__getattribute__(用量, "total_tokens")
                合法 = 權杖數 is None or (type(權杖數) is int and 權杖數 >= 0)
        安全警告 = []
        if 合法:
            try:
                警告純量 = _擷取有界警告純量(警告清單)
                for 代碼, 訊息 in 警告純量:
                    安全警告.append(PublishedWarning(代碼, 訊息))
            except ValueError:
                合法 = False
        安全資料 = None
        if 合法 and 種類 == "success":
            try:
                安全資料 = _建立正規呼叫快照(資料, None).建立輸入()
            except ValueError:
                合法 = False
        if not 合法:
            種類 = 資料 = 用量 = 警告清單 = 安全警告 = 安全資料 = None
            raise ValueError("執行嘗試結果不符合契約") from None
        安全用量 = None if 用量 is None else PublishedUsage(用量.total_tokens)
        object.__setattr__(self, "kind", 種類)
        object.__setattr__(self, "data", 安全資料)
        object.__setattr__(self, "usage", 安全用量)
        object.__setattr__(self, "warnings", tuple(安全警告))


@dataclass(frozen=True, slots=True, init=False)
class 執行嘗試紀錄收據:
    """INV-owned exact receipt；只承認匹配嘗試且已提交的 append。"""

    invocation_id: str
    attempt: int
    committed: bool
    sequence: int

    def __init__(self, 呼叫識別: str, 嘗試次數: int, 已提交: bool, 序號: int) -> None:
        """建立不可變且可由編排器重新驗證的執行嘗試收據。"""
        if (type(呼叫識別) is not str or not 呼叫識別
                or type(嘗試次數) is not int or 嘗試次數 not in (1, 2)
                or 已提交 is not True or type(已提交) is not bool
                or type(序號) is not int or not 1 <= 序號 <= 2**63 - 1):
            呼叫識別 = 嘗試次數 = 已提交 = 序號 = None
            raise ValueError("執行嘗試紀錄收據不符合契約") from None
        object.__setattr__(self, "invocation_id", 呼叫識別)
        object.__setattr__(self, "attempt", 嘗試次數)
        object.__setattr__(self, "committed", 已提交)
        object.__setattr__(self, "sequence", 序號)


@dataclass(frozen=True, slots=True)
class _終局結果快照:
    """recorder 不可觸及的 exact scalar／canonical terminal authority。"""

    種類: str
    結構有效: bool | None
    資料文字: str | None
    有用量: bool
    權杖數: int | None
    警告純量: tuple[tuple[str, str], ...]

    def 建立結果(self) -> 執行嘗試結果:
        """只由私有純量重建 fresh runtime result DTO。"""
        資料 = None if self.資料文字 is None else json.loads(self.資料文字)
        用量 = PublishedUsage(self.權杖數) if self.有用量 else None
        警告 = tuple(PublishedWarning(代碼, 訊息) for 代碼, 訊息 in self.警告純量)
        return 執行嘗試結果(self.種類, 資料, 用量, 警告)


def _建立終局結果快照(結果: 執行嘗試結果, 結構有效: bool | None) -> _終局結果快照:
    """在 recorder 前 exact-read 已重建結果，保存 module-owned terminal authority。"""
    if type(結果) is not 執行嘗試結果 or (結構有效 is not None and type(結構有效) is not bool):
        raise ValueError
    種類 = object.__getattribute__(結果, "kind")
    資料 = object.__getattribute__(結果, "data")
    用量 = object.__getattribute__(結果, "usage")
    警告 = object.__getattribute__(結果, "warnings")
    if type(種類) is not str:
        raise ValueError
    有用量 = 用量 is not None
    權杖數 = None
    if 有用量:
        if type(用量) is not PublishedUsage:
            raise ValueError
        權杖數 = object.__getattribute__(用量, "total_tokens")
        if 權杖數 is not None and (type(權杖數) is not int or 權杖數 < 0):
            raise ValueError
    警告純量 = _擷取有界警告純量(警告)
    資料文字 = None
    if 種類 == "success":
        資料文字 = object.__getattribute__(_建立正規呼叫快照(資料, None), "輸入文字")
    elif 資料 is not None or 有用量 or 警告純量 or 結構有效 is not None:
        raise ValueError
    return _終局結果快照(種類, 結構有效, 資料文字, 有用量, 權杖數, 警告純量)


def _重建成功投影(原始信封: object) -> tuple[InvokeEnvelope, str]:
    """exact-read 每一固定 slot，並經共同 factory 重建成功信封。"""
    if type(原始信封) is not InvokeEnvelope:
        raise ValueError
    成功 = object.__getattribute__(原始信封, "ok")
    原始端點 = object.__getattribute__(原始信封, "endpoint")
    原始呼叫 = object.__getattribute__(原始信封, "invocation")
    原始資料 = object.__getattribute__(原始信封, "data")
    原始用量 = object.__getattribute__(原始信封, "usage")
    原始警告 = object.__getattribute__(原始信封, "warnings")
    原始錯誤 = object.__getattribute__(原始信封, "error")
    if 成功 is not True or type(成功) is not bool or 原始錯誤 is not None:
        raise ValueError

    if type(原始端點) is not EndpointRef or type(原始呼叫) is not InvocationRef:
        raise ValueError
    端點識別 = object.__getattribute__(原始端點, "id")
    短名 = object.__getattribute__(原始端點, "slug")
    版本 = object.__getattribute__(原始端點, "version")
    呼叫識別 = object.__getattribute__(原始呼叫, "id")
    請求識別 = object.__getattribute__(原始呼叫, "request_id")
    工作階段識別 = object.__getattribute__(原始呼叫, "session_id")
    if (type(端點識別) is not str or not 端點識別 or type(短名) is not str or not 短名
            or type(版本) is not int or 版本 < 1 or type(呼叫識別) is not str or not 呼叫識別
            or type(請求識別) is not str or not 請求識別
            or (工作階段識別 is not None and type(工作階段識別) is not str)):
        raise ValueError
    權杖數 = None
    if 原始用量 is not None:
        if type(原始用量) is not PublishedUsage:
            raise ValueError
        權杖數 = object.__getattribute__(原始用量, "total_tokens")
        if 權杖數 is not None and (type(權杖數) is not int or 權杖數 < 0):
            raise ValueError
    安全警告 = [
        PublishedWarning(代碼, 訊息)
        for 代碼, 訊息 in _擷取有界警告純量(原始警告)
    ]
    安全資料 = _解凍成功資料(原始資料)
    安全端點 = EndpointRef(端點識別, 短名, 版本)
    安全呼叫 = InvocationRef(呼叫識別, 請求識別, 工作階段識別)
    安全用量 = None if 原始用量 is None else PublishedUsage(權杖數)
    安全信封 = 建立成功信封(
        安全端點, 安全呼叫, 安全資料, usage=安全用量, warnings=tuple(安全警告),
    )
    正規文字 = json.dumps(
        InvokeEnvelope.to_json(安全信封), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )
    return 安全信封, 正規文字


def _解凍成功資料(根: object) -> object:
    """只讀取 factory-owned exact immutable JSON tree，並施加 INV bounded limits。"""
    計數 = [0]

    def 解凍(值: object, 深度: int) -> object:
        """由module-owned immutable狀態建立fresh JSON容器。"""
        計數[0] += 1
        if 計數[0] > _JSON最大節點 or 深度 > _JSON最大深度:
            raise ValueError
        值型別 = type(值)
        if 值 is None or 值型別 in (str, int, bool):
            return 值
        if 值型別 is float:
            if not math.isfinite(值):
                raise ValueError
            return 值
        if 值型別 is tuple:
            if len(值) > _JSON最大容器項目:
                raise ValueError
            return [解凍(項目, 深度 + 1) for 項目 in tuple.__iter__(值)]
        if 值型別 is MappingProxyType:
            參照物 = gc.get_referents(值)
            if len(參照物) != 1 or type(參照物[0]) is not dict:
                raise ValueError
            背景字典 = 參照物[0]
            項目列 = tuple(dict.items(背景字典))
            if len(項目列) > _JSON最大容器項目:
                raise ValueError
            輸出 = {}
            for 鍵, 項目 in 項目列:
                if type(鍵) is not str:
                    raise ValueError
                輸出[鍵] = 解凍(項目, 深度 + 1)
            return 輸出
        raise ValueError

    快照 = 解凍(根, 0)
    return _建立正規呼叫快照(快照, None).建立輸入()


@_安裝成功結果來源
@dataclass(frozen=True, slots=True, weakref_slot=True, init=False, eq=False)
class 呼叫成功結果:
    """HTTP 200成功投影；provenance只存在class method closures。"""

    status_code: int
    envelope: InvokeEnvelope

    @property
    def 標頭(self) -> object:
        """成功回應沒有額外標頭。"""
        return MappingProxyType({})


setattr(呼叫成功結果, "headers", 呼叫成功結果.標頭)
setattr(呼叫成功結果, "to_json", 呼叫成功結果.轉為JSON)
del _安裝成功結果來源


@dataclass(frozen=True, slots=True)
class 外部呼叫入口:
    """I03 結果；錯誤已穩定映射，成功則攜帶後續切片所需權限。"""

    endpoint: EndpointRef | None
    invocation: InvocationRef | None
    pinned_version: object | None
    authentication: object | None
    error: 錯誤映射結果 | None
    _續行快照: _正規呼叫快照 | None = None


class 外部呼叫編排器:
    """組合 frozen FND／LOG／CRED／RATE 邊界，不持有交易或 runtime。

    參數：建構時接收 exact dependency types 與各階段 callbacks。
    回傳：建立可執行固定呼叫順序的傳輸中立編排器。
    例外：依賴違反契約時由公開操作固定映射或拋編排錯誤。
    副作用：建構無副作用；公開操作依序呼叫已注入的儲存與 runtime 邊界。
    """

    def __init__(
        self, 解析器: object, 呼叫儲存庫: object, 憑證服務: object, *,
        解析未找到型別: type,
        釘選型別: type, 驗證型別: type, 驗證狀態型別: type,
        階段型別: type,
        準備擷取: Callable[[object, object, object | None], object | None],
        寫入擷取: Callable[..., str],
        限流決策型別: type,
        提交雙層計數: Callable[[str, str, int, int, float], object],
        驗證輸入: Callable[[object, object], bool],
        開始執行嘗試: Callable[[InvocationRef, 執行嘗試請求], None] | None = None,
        執行嘗試: Callable[[執行嘗試請求], 執行嘗試結果] | None = None,
        驗證輸出: Callable[[object, object], bool] | None = None,
        記錄執行嘗試: Callable[
            [InvocationRef, 執行嘗試請求, 執行嘗試結果, bool | None], 執行嘗試紀錄收據
        ] | None = None,
        工作階段儲存庫: object | None = None,
    ) -> None:
        """保存已組合的 exact dependency types 與 callback 邊界。

        參數：解析、儲存、憑證、限流、schema、runtime、pre-hook 與 recorder 依賴。
        回傳：無。
        例外：建構只保存參照，沒有預期例外。
        副作用：不執行 callback、資料庫或模型操作。
        """
        self._解析器 = 解析器
        self._解析未找到型別 = 解析未找到型別
        self._呼叫儲存庫 = 呼叫儲存庫
        self._憑證服務 = 憑證服務
        self._釘選型別 = 釘選型別
        self._驗證型別 = 驗證型別
        self._驗證狀態型別 = 驗證狀態型別
        self._階段型別 = 階段型別
        self._準備擷取 = 準備擷取
        self._寫入擷取 = 寫入擷取
        self._限流決策型別 = 限流決策型別
        self._提交雙層計數 = 提交雙層計數
        self._驗證輸入 = 驗證輸入
        self._開始執行嘗試 = 開始執行嘗試
        self._執行嘗試 = 執行嘗試
        self._驗證輸出 = 驗證輸出
        self._記錄執行嘗試 = 記錄執行嘗試
        self._工作階段儲存庫 = 工作階段儲存庫

    def 執行(
        self, 短名: str, 請求識別: str, 提供的API金鑰: str,
        輸入資料: object, 中繼資料: object | None, 驗證時間: int | float,
        工作階段識別: str | None = None,
    ) -> 呼叫成功結果 | 錯誤映射結果:
        """完成 I03 gate 後最多執行兩次；recorder 只能接觸 disposable DTO。

        參數：短名、請求識別、API 金鑰、輸入資料、中繼資料及驗證時間；
            ``工作階段識別`` 為 ``str | None``，省略或 ``None`` 時維持 stateless，
            提供時則在執行前載入並於成功後原子追加 durable history。
        回傳：成功信封或 canonical 錯誤映射結果。
        例外：控制流程保留 identity；普通 runtime／recorder 異常固定為 internal error。
        副作用：依序執行 I03、attempt-1 pre-hook、模型嘗試、驗證與 ledger callback。
        """
        入口 = None
        try:
            if 工作階段識別 is None:
                入口 = self.開始(短名, 請求識別, 提供的API金鑰, 輸入資料, 中繼資料, 驗證時間)
            else:
                入口 = self.開始(
                    短名, 請求識別, 提供的API金鑰, 輸入資料, 中繼資料, 驗證時間,
                    工作階段識別,
                )
        except _控制流程 as 控制:
            self = 短名 = 請求識別 = 提供的API金鑰 = 輸入資料 = 中繼資料 = 驗證時間 = 入口 = None
            _清理並重拋(控制)
        if 入口.error is not None:
            return 入口.error

        請求 = 原始結果 = 安全結果 = 原始資料 = 信封 = 收據 = None
        紀錄呼叫 = 紀錄請求 = 紀錄結果 = 終局快照 = None
        私有端點 = 私有呼叫 = 私有快照 = 私有釘選 = None
        端點識別 = 端點短名 = 呼叫識別 = 私有請求識別 = 工作階段識別 = None
        端點版本 = 0
        執行函式 = self._執行嘗試
        開始函式 = self._開始執行嘗試
        驗證函式 = self._驗證輸出
        紀錄函式 = self._記錄執行嘗試
        錯誤碼 = None
        失敗 = False
        try:
            if type(入口.endpoint) is not EndpointRef or type(入口.invocation) is not InvocationRef:
                raise ValueError
            端點識別 = object.__getattribute__(入口.endpoint, "id")
            端點短名 = object.__getattribute__(入口.endpoint, "slug")
            端點版本 = object.__getattribute__(入口.endpoint, "version")
            呼叫識別 = object.__getattribute__(入口.invocation, "id")
            私有請求識別 = object.__getattribute__(入口.invocation, "request_id")
            工作階段識別 = object.__getattribute__(入口.invocation, "session_id")
            私有端點 = EndpointRef(端點識別, 端點短名, 端點版本)
            私有呼叫 = InvocationRef(呼叫識別, 私有請求識別, 工作階段識別)
            私有快照 = object.__getattribute__(入口, "_續行快照")
            私有釘選 = object.__getattribute__(入口, "pinned_version")
            歷史 = ()
            if 工作階段識別 is not None:
                if self._工作階段儲存庫 is None:
                    raise ValueError
                歷史 = self._工作階段儲存庫.讀取成功歷史(
                    端點識別, object.__getattribute__(私有釘選, "service_account_id"), 工作階段識別,
                )
            if type(私有快照) is not _正規呼叫快照 or 私有釘選 is None:
                raise ValueError
            入口 = None
            if 執行函式 is None or 驗證函式 is None:
                錯誤碼 = "endpoint_misconfigured"
            elif 紀錄函式 is None:
                錯誤碼 = "internal_error"
            else:
                for 次數 in (1, 2):
                    請求 = 執行嘗試請求(
                        私有釘選, 私有快照.建立輸入(), 私有快照.建立中繼資料(), 次數, 歷史,
                    )
                    if 次數 == 1 and 開始函式 is not None:
                        開始函式(
                            InvocationRef(呼叫識別, 私有請求識別, 工作階段識別),
                            執行嘗試請求(
                                私有釘選, 私有快照.建立輸入(), 私有快照.建立中繼資料(), 次數,
                            ),
                        )
                    原始結果 = 執行函式(請求)
                    if type(原始結果) is not 執行嘗試結果:
                        raise ValueError
                    安全結果 = 執行嘗試結果(
                        object.__getattribute__(原始結果, "kind"),
                        object.__getattribute__(原始結果, "data"),
                        object.__getattribute__(原始結果, "usage"),
                        object.__getattribute__(原始結果, "warnings"),
                    )
                    原始結果 = None
                    驗證有效 = None
                    if object.__getattribute__(安全結果, "kind") == "success":
                        原始資料 = _建立正規呼叫快照(
                            object.__getattribute__(安全結果, "data"), None,
                        ).建立輸入()
                        驗證有效 = 驗證函式(私有釘選, 原始資料)
                        原始資料 = None
                        if type(驗證有效) is not bool:
                            raise ValueError
                    終局快照 = _建立終局結果快照(安全結果, 驗證有效)
                    紀錄呼叫 = InvocationRef(呼叫識別, 私有請求識別, 工作階段識別)
                    紀錄請求 = 執行嘗試請求(
                        私有釘選, 私有快照.建立輸入(), 私有快照.建立中繼資料(), 次數,
                        歷史,
                    )
                    紀錄結果 = 終局快照.建立結果()
                    收據 = 紀錄函式(紀錄呼叫, 紀錄請求, 紀錄結果, 終局快照.結構有效)
                    紀錄呼叫 = 紀錄請求 = 紀錄結果 = 請求 = 安全結果 = None
                    _確認執行嘗試收據(收據, 呼叫識別, 次數)
                    收據 = None
                    if 終局快照.種類 != "success":
                        錯誤碼 = 終局快照.種類
                        終局快照 = None
                        break
                    if 終局快照.結構有效:
                        安全結果 = 終局快照.建立結果()
                        信封 = 建立成功信封(
                            EndpointRef(端點識別, 端點短名, 端點版本),
                            InvocationRef(呼叫識別, 私有請求識別, 工作階段識別),
                            object.__getattribute__(安全結果, "data"),
                            usage=object.__getattribute__(安全結果, "usage"),
                            warnings=object.__getattribute__(安全結果, "warnings"),
                        )
                        結果 = 呼叫成功結果(信封)
                        請求 = 原始結果 = 安全結果 = 原始資料 = 信封 = 收據 = None
                        紀錄呼叫 = 紀錄請求 = 紀錄結果 = 終局快照 = None
                        開始函式 = 執行函式 = 驗證函式 = 紀錄函式 = 入口 = None
                        私有端點 = 私有呼叫 = 私有快照 = 私有釘選 = None
                        return 結果
                    終局快照 = None
                if 錯誤碼 is None:
                    錯誤碼 = "model_output_schema_invalid"
        except _控制流程 as 控制:
            self = 短名 = 請求識別 = 提供的API金鑰 = 輸入資料 = 中繼資料 = 驗證時間 = None
            入口 = 請求 = 原始結果 = 安全結果 = 原始資料 = 信封 = 收據 = None
            紀錄呼叫 = 紀錄請求 = 紀錄結果 = 終局快照 = None
            私有端點 = 私有呼叫 = 私有快照 = 私有釘選 = None
            端點識別 = 端點短名 = 呼叫識別 = 私有請求識別 = 工作階段識別 = None
            開始函式 = 執行函式 = 驗證函式 = 紀錄函式 = 錯誤碼 = None
            _清理並重拋(控制)
        except BaseException:
            失敗 = True
        if 失敗:
            錯誤碼 = "internal_error"
        端點, 呼叫 = 私有端點, 私有呼叫
        self = 短名 = 請求識別 = 提供的API金鑰 = 輸入資料 = 中繼資料 = 驗證時間 = None
        入口 = 請求 = 原始結果 = 安全結果 = 原始資料 = 信封 = 收據 = None
        紀錄呼叫 = 紀錄請求 = 紀錄結果 = 終局快照 = None
        私有端點 = 私有呼叫 = 私有快照 = 私有釘選 = None
        開始函式 = 執行函式 = 驗證函式 = 紀錄函式 = None
        return 映射呼叫錯誤(錯誤碼, endpoint=端點, invocation=呼叫)

    def 開始(
        self, 短名: str, 請求識別: str, 提供的API金鑰: str,
        輸入資料: object, 中繼資料: object | None, 驗證時間: int | float,
        工作階段識別: str | None = None,
    ) -> 外部呼叫入口:
        """認證成功後刷新、提交雙層計數，再依 D20 status/rate/input 決定。"""
        釘選 = 驗證結果 = 擷取命令 = 端點 = 呼叫 = 結果 = 決策 = 快照 = None
        擷取輸入 = 擷取中繼 = 輸入快照 = None
        端點識別 = 版本識別 = 憑證識別 = 呼叫識別 = 狀態 = 狀態值 = None
        認證端點 = 端點狀態 = 超限範圍 = 重試秒數 = None
        端點上限 = 憑證上限 = 端點計數 = 憑證計數 = 0
        允許 = 輸入有效 = 快照失敗 = False
        準備函式 = self._準備擷取
        解析函式 = 驗證函式 = 刷新函式 = 計數函式 = 輸入函式 = None
        解析未找到 = 解析失敗 = 編排失敗 = False
        try:
            解析函式 = self._解析器.依slug解析
            釘選 = 解析函式(短名)
        except _控制流程 as 控制:
            self = 短名 = 請求識別 = 提供的API金鑰 = 輸入資料 = 中繼資料 = 驗證時間 = None
            釘選 = 解析函式 = None
            _清理並重拋(控制)
        except Exception as 解析錯誤:
            解析未找到 = type(解析錯誤) is self._解析未找到型別
            解析失敗 = not 解析未找到
            解析錯誤 = None
        except BaseException:
            解析失敗 = True
        if 解析未找到:
            self = 請求識別 = 提供的API金鑰 = 輸入資料 = 中繼資料 = 驗證時間 = 解析函式 = None
            短名 = 釘選 = None
            return 外部呼叫入口(None, None, None, None, 映射呼叫錯誤("endpoint_not_found"))
        if 解析失敗:
            self = 短名 = 請求識別 = 提供的API金鑰 = 輸入資料 = 中繼資料 = 驗證時間 = None
            釘選 = 解析函式 = None
            raise 外部呼叫編排錯誤("外部呼叫編排失敗") from None

        try:
            釘選 = _重建可信釘選版本(釘選, self._釘選型別)
            端點識別 = object.__getattribute__(釘選, "endpoint_id")
            版本識別 = object.__getattribute__(釘選, "version_id")
            版本編號 = object.__getattribute__(釘選, "version_number")
            if (type(短名) is not str or not 短名 or type(請求識別) is not str or not 請求識別
                    or type(端點識別) is not str or not 端點識別
                    or type(版本識別) is not str or not 版本識別
                    or type(版本編號) is not int or 版本編號 < 1
                    or type(驗證時間) not in (int, float)
                    or not math.isfinite(float(驗證時間)) or 驗證時間 < 0):
                raise ValueError
            端點 = EndpointRef(端點識別, 短名, 版本編號)
            驗證函式 = self._憑證服務.驗證
            驗證結果 = 驗證函式(端點識別, 提供的API金鑰)
            提供的API金鑰 = None
            if type(驗證結果) is not self._驗證型別:
                raise ValueError
            狀態 = 驗證結果.status
            if type(狀態) is not self._驗證狀態型別:
                raise ValueError
            狀態值 = 狀態.value
            if type(狀態值) is not str:
                raise ValueError
            階段 = _選擇階段(self._階段型別, 狀態值)
            憑證識別 = 驗證結果.credential_id if 狀態值 == "authenticated" else None
            if 狀態值 == "authenticated" and (type(憑證識別) is not str or not 憑證識別):
                raise ValueError
            if 狀態值 == "authenticated":
                認證端點 = 驗證結果.endpoint_id
                端點狀態 = 驗證結果.endpoint_status
                憑證上限 = 驗證結果.credential_rate_limit
                端點上限 = 驗證結果.endpoint_rate_limit
                if (type(認證端點) is not str or 認證端點 != 端點識別
                        or type(端點狀態) is not str or 端點狀態 not in ("active", "disabled", "archived")
                        or type(端點上限) is not int or not 1 <= 端點上限 <= 10_000
                        or type(憑證上限) is not int or not 1 <= 憑證上限 <= 10_000):
                    raise ValueError
            try:
                快照 = _建立正規呼叫快照(輸入資料, 中繼資料)
            except ValueError:
                快照失敗 = True
            if 快照失敗:
                擷取輸入 = 擷取中繼 = None
            else:
                assert type(快照) is _正規呼叫快照
                擷取輸入 = 快照.建立輸入()
                擷取中繼 = 快照.建立中繼資料()
            輸入資料 = 中繼資料 = None
            擷取命令 = 準備函式(階段, 擷取輸入, 擷取中繼)
            擷取輸入 = 擷取中繼 = None
            if 擷取命令 is None:
                raise ValueError
            呼叫識別 = self._寫入擷取(
                self._呼叫儲存庫, 擷取命令, 端點識別, 版本識別, 請求識別,
                credential_id=憑證識別, session_id=工作階段識別,
            )
            if type(呼叫識別) is not str or not 呼叫識別:
                raise ValueError
            呼叫 = InvocationRef(呼叫識別, 請求識別, 工作階段識別)
            if 狀態值 != "authenticated":
                錯誤碼 = "api_key_expired" if 狀態值 == "api_key_expired" else "invalid_api_key"
                結果 = 外部呼叫入口(
                    端點, 呼叫, 釘選, None,
                    映射呼叫錯誤(錯誤碼, endpoint=端點, invocation=呼叫),
                )
            else:
                刷新函式 = self._憑證服務.刷新已認證使用
                刷新函式(驗證結果, float(驗證時間))
                計數函式 = self._提交雙層計數
                決策 = 計數函式(端點識別, 憑證識別, 端點上限, 憑證上限, float(驗證時間))
                if type(決策) is not self._限流決策型別:
                    raise ValueError
                允許 = 決策.允許
                端點計數 = 決策.端點計數
                憑證計數 = 決策.憑證計數
                超限範圍 = 決策.超限範圍
                重試秒數 = 決策.重試秒數
                if (type(允許) is not bool or type(端點計數) is not int or not 1 <= 端點計數 <= 2**63 - 1
                        or type(憑證計數) is not int or not 1 <= 憑證計數 <= 2**63 - 1):
                    raise ValueError
                if 允許:
                    if (超限範圍 is not None or 重試秒數 is not None
                            or 端點計數 > 端點上限 or 憑證計數 > 憑證上限):
                        raise ValueError
                else:
                    if (type(超限範圍) is not str or 超限範圍 not in ("endpoint", "credential")
                            or type(重試秒數) is not int or not 1 <= 重試秒數 <= 60):
                        raise ValueError
                    if 超限範圍 == "endpoint":
                        if 端點計數 <= 端點上限:
                            raise ValueError
                    elif 端點計數 > 端點上限 or 憑證計數 <= 憑證上限:
                        raise ValueError
                錯誤碼 = "endpoint_disabled" if 端點狀態 == "disabled" else (
                    "endpoint_archived" if 端點狀態 == "archived" else None
                )
                if 錯誤碼 is not None:
                    結果 = _認證錯誤(端點, 呼叫, 釘選, 驗證結果, 錯誤碼)
                elif not 允許:
                    結果 = _認證錯誤(
                        端點, 呼叫, 釘選, 驗證結果, "rate_limit_exceeded",
                        {"scope": 超限範圍, "retry_after_seconds": 重試秒數},
                    )
                elif 快照失敗:
                    結果 = _認證錯誤(端點, 呼叫, 釘選, 驗證結果, "input_schema_invalid")
                else:
                    輸入函式 = self._驗證輸入
                    輸入快照 = 快照.建立輸入()
                    輸入有效 = 輸入函式(釘選, 輸入快照)
                    輸入快照 = None
                    if type(輸入有效) is not bool:
                        raise ValueError
                    if not 輸入有效:
                        結果 = _認證錯誤(端點, 呼叫, 釘選, 驗證結果, "input_schema_invalid")
                    else:
                        結果 = 外部呼叫入口(端點, 呼叫, 釘選, 驗證結果, None, 快照)
        except _控制流程 as 控制:
            self = 短名 = 請求識別 = 提供的API金鑰 = 輸入資料 = 中繼資料 = 驗證時間 = None
            釘選 = 驗證結果 = 擷取命令 = 端點 = 呼叫 = 結果 = 決策 = 快照 = None
            擷取輸入 = 擷取中繼 = 輸入快照 = None
            端點識別 = 版本識別 = 憑證識別 = 呼叫識別 = 狀態 = 狀態值 = None
            認證端點 = 端點狀態 = 超限範圍 = 重試秒數 = None
            端點上限 = 憑證上限 = 端點計數 = 憑證計數 = 0
            允許 = 輸入有效 = 快照失敗 = False
            準備函式 = 解析函式 = 驗證函式 = 刷新函式 = 計數函式 = 輸入函式 = None
            _清理並重拋(控制)
        except BaseException:
            編排失敗 = True
        if 編排失敗:
            self = 短名 = 請求識別 = 提供的API金鑰 = 輸入資料 = 中繼資料 = 驗證時間 = None
            釘選 = 驗證結果 = 擷取命令 = 端點 = 呼叫 = 結果 = 決策 = 快照 = None
            擷取輸入 = 擷取中繼 = 輸入快照 = None
            端點識別 = 版本識別 = 憑證識別 = 呼叫識別 = 狀態 = 狀態值 = None
            認證端點 = 端點狀態 = 超限範圍 = 重試秒數 = None
            端點上限 = 憑證上限 = 端點計數 = 憑證計數 = 0
            允許 = 輸入有效 = 快照失敗 = False
            準備函式 = 解析函式 = 驗證函式 = 刷新函式 = 計數函式 = 輸入函式 = None
            raise 外部呼叫編排錯誤("外部呼叫編排失敗") from None
        return 結果


def _確認執行嘗試收據(原始收據: object, 呼叫識別: str, 嘗試次數: int) -> None:
    """trusted-rebuild recorder receipt 並要求 authoritative identity 完全匹配。"""
    if type(原始收據) is not 執行嘗試紀錄收據:
        raise ValueError("執行嘗試紀錄失敗") from None
    安全收據 = 執行嘗試紀錄收據(
        object.__getattribute__(原始收據, "invocation_id"),
        object.__getattribute__(原始收據, "attempt"),
        object.__getattribute__(原始收據, "committed"),
        object.__getattribute__(原始收據, "sequence"),
    )
    if (object.__getattribute__(安全收據, "invocation_id") != 呼叫識別
            or object.__getattribute__(安全收據, "attempt") != 嘗試次數):
        原始收據 = 安全收據 = 呼叫識別 = 嘗試次數 = None
        raise ValueError("執行嘗試紀錄失敗") from None


def _認證錯誤(
    端點: EndpointRef, 呼叫: InvocationRef, 釘選: object, 驗證結果: object,
    錯誤碼: str, 細節: dict[str, Any] | None = None,
) -> 外部呼叫入口:
    """保留已認證主流程權限，並以 I01 固定 mapper 建立拒絕結果。"""
    return 外部呼叫入口(
        端點, 呼叫, 釘選, 驗證結果,
        映射呼叫錯誤(錯誤碼, endpoint=端點, invocation=呼叫, details=細節),
    )


def _選擇階段(階段型別: type, 狀態: str) -> object:
    """將 CRED 固定分類轉成既有 LOG 階段，不接受未知分類。"""
    if 狀態 == "authenticated":
        return 階段型別.AUTHENTICATED
    if 狀態 == "invalid_api_key":
        return 階段型別.INVALID_API_KEY
    if 狀態 in ("api_key_expired", "api_key_revoked"):
        return 階段型別.PRE_CREDENTIAL_REJECTION
    raise ValueError


def _清理並重拋(控制: BaseException) -> None:
    """保留 K/I/S/G identity/args，移除舊 traceback 與例外鏈。"""
    BaseException.__setattr__(控制, "__traceback__", None)
    BaseException.__setattr__(控制, "__cause__", None)
    BaseException.__setattr__(控制, "__context__", None)
    BaseException.__setattr__(控制, "__suppress_context__", True)
    try:
        raise 控制
    except _控制流程:
        控制 = None  # type: ignore[assignment]
        raise
