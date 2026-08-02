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
