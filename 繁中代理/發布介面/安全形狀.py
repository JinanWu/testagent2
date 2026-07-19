"""路由政策欄位專用、callback-free 的 bounded detached 快照。"""

from __future__ import annotations

import math
from typing import Any

from .設定 import 路由設定錯誤訊息

_最大節點 = 4096
_最大字串位元組 = 16384
_最大整數 = 2**63 - 1
_容器類別 = (list, tuple, dict, set, frozenset)


def _失敗():
    """統一拒絕不安全政策形狀。"""
    raise ValueError(路由設定錯誤訊息)


def _純量(值: Any):
    """只接受 exact、有限且 bounded 的 immutable scalar。"""
    類別 = type(值)
    if 值 is None or 類別 is bool:
        return ("v", 值)
    if 類別 is int and -_最大整數 <= 值 <= _最大整數:
        return ("v", 值)
    if 類別 is float and math.isfinite(值):
        return ("v", 值)
    if 類別 is str and len(值.encode("utf-8")) <= _最大字串位元組:
        return ("v", 值)
    _失敗()


def 擷取形狀(值: Any, 模式: str, 計數: list[int] | None = None, *, 模型欄位: bool = False):
    """依欄位契約建立 module-owned tuple tree；不保留任意 identity leaf。"""
    if 計數 is None:
        計數 = [0]
    計數[0] += 1
    if 計數[0] > _最大節點:
        _失敗()
    if 模型欄位:
        if not isinstance(值, type):
            _失敗()
        return ("i", 值)
    類別 = type(值)
    if 類別 not in _容器類別:
        return _純量(值)
    if 模式 == "json" and 類別 not in (list, dict):
        _失敗()
    if 模式 == "tags":
        if 類別 is not list:
            _失敗()
        項目 = []
        for 子值 in 值:
            if type(子值) is not str:
                _失敗()
            項目.append(_純量(子值))
        return (list, tuple(項目))
    if 類別 is dict:
        項目 = []
        for 鍵, 子值 in dict.items(值):
            鍵類別 = type(鍵)
            if 模式 in ("json", "responses"):
                if 鍵類別 is not str and not (模式 == "responses" and 鍵類別 is int):
                    _失敗()
            elif 鍵類別 not in (str, int, bool):
                _失敗()
            鍵描述 = _純量(鍵)
            是模型 = 模式 == "responses" and 鍵類別 is str and 鍵 == "model"
            項目.append((鍵描述, 擷取形狀(子值, 模式, 計數, 模型欄位=是模型)))
        return (dict, tuple(項目))
    項目 = []
    for 子值 in 值:
        項目.append(擷取形狀(子值, 模式, 計數))
    return (類別, tuple(項目))


def 重建形狀(描述):
    """由已驗證 tuple tree 建立 fresh exact-built-in containers。"""
    類別, 值 = 描述
    if 類別 in ("v", "i"):
        return 值
    if 類別 is dict:
        return {重建形狀(鍵): 重建形狀(子值) for 鍵, 子值 in 值}
    項目 = [重建形狀(子值) for 子值 in 值]
    return 類別(項目)


def _集合相同(左, 右) -> bool:
    """不用 caller hashing 比較已驗證 detached set/frozenset。"""
    已用: set[int] = set()
    for 左項 in 左:
        for 索引, 右項 in enumerate(右):
            if 索引 not in 已用 and 形狀相同(左項, 右項):
                已用.add(索引)
                break
        else:
            return False
    return True


def 形狀相同(左, 右) -> bool:
    """只比較已驗證 scalar/container；identity 欄位僅用 is。"""
    if 左[0] is not 右[0] and 左[0] != 右[0]:
        return False
    if 左[0] == "i":
        return 左[1] is 右[1]
    if 左[0] == "v":
        return type(左[1]) is type(右[1]) and 左[1] == 右[1]
    if len(左[1]) != len(右[1]):
        return False
    if 左[0] is dict:
        return all(形狀相同(甲[0], 乙[0]) and 形狀相同(甲[1], 乙[1]) for 甲, 乙 in zip(左[1], 右[1]))
    if 左[0] in (set, frozenset):
        return _集合相同(左[1], 右[1])
    return all(形狀相同(甲, 乙) for 甲, 乙 in zip(左[1], 右[1]))
