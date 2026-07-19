"""不執行 caller callbacks 的 bounded exact-built-in shape 快照。"""

from typing import Any

from .設定 import 路由設定錯誤訊息

_最大節點 = 4096


def _失敗():
    """統一拒絕不安全政策形狀。"""
    raise ValueError(路由設定錯誤訊息)


def 擷取形狀(值: Any, 計數: list[int] | None = None):
    """將 exact containers 轉成 module-owned tuple tree，其他 leaf 僅保留 identity。"""
    if 計數 is None:
        計數 = [0]
    計數[0] += 1
    if 計數[0] > _最大節點:
        _失敗()
    類別 = type(值)
    if 值 is None or 值 is Ellipsis or 類別 in (bool, int, float, str, bytes):
        return ("v", 值)
    if 類別 in (list, tuple, set, frozenset):
        項目 = []
        for 子值 in 值:
            項目.append(擷取形狀(子值, 計數))
        return (類別, tuple(項目))
    if 類別 is dict:
        項目 = []
        for 鍵, 子值 in dict.items(值):
            if type(鍵) not in (str, int):
                _失敗()
            項目.append((鍵, 擷取形狀(子值, 計數)))
        return (dict, tuple(項目))
    return ("i", 值)


def 重建形狀(描述):
    """由安全 tuple tree 建立 fresh exact-built-in containers。"""
    類別, 值 = 描述
    if 類別 == "v" or 類別 == "i":
        return 值
    if 類別 is dict:
        return {鍵: 重建形狀(子值) for 鍵, 子值 in 值}
    項目 = [重建形狀(子值) for 子值 in 值]
    return 類別(項目)


def _集合形狀相同(左, 右) -> bool:
    """不用 hashing 比較可能含 identity leaf 的 detached set shape。"""
    已用 = set()
    for 左項 in 左:
        for 索引, 右項 in enumerate(右):
            if 索引 not in 已用 and 形狀相同(左項, 右項):
                已用.add(索引)
                break
        else:
            return False
    return True


def 形狀相同(左, 右) -> bool:
    """exact-type 比較 scalar/container；不對 identity leaf 呼叫 eq/hash。"""
    if 左[0] is not 右[0] and 左[0] != 右[0]:
        return False
    if 左[0] == "i":
        return 左[1] is 右[1]
    if 左[0] == "v":
        return type(左[1]) is type(右[1]) and 左[1] == 右[1]
    if len(左[1]) != len(右[1]):
        return False
    if 左[0] is dict:
        return all(
            type(甲[0]) is type(乙[0]) and 甲[0] == 乙[0] and 形狀相同(甲[1], 乙[1])
            for 甲, 乙 in zip(左[1], 右[1])
        )
    if 左[0] in (set, frozenset):
        return _集合形狀相同(左[1], 右[1])
    return all(形狀相同(甲, 乙) for 甲, 乙 in zip(左[1], 右[1]))
