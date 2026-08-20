"""Trusted in-process factories共用的Canonical dependency wrapper弱身份登錄。

本模組只防止attribute、hash與equality造成的意外或資料驅動誤標；同一Python
process內的產品程式碼視為可信，module-private writer不是敵對程式碼安全邊界。
"""
from __future__ import annotations

import weakref
from threading import RLock
from typing import Callable

_鎖 = RLock()
_封裝相依: dict[
    int, tuple[weakref.ReferenceType[Callable], weakref.ReferenceType[Callable]],
] = {}


def _登錄Canonical相依封裝(封裝: Callable, canonical: Callable) -> None:
    """供同process正式factory以id與exact weak identity登錄相依配對。"""
    if not callable(封裝) or not callable(canonical):
        raise ValueError("Canonical相依封裝無效") from None
    身份 = id(封裝)

    def 移除(封裝參照: weakref.ReferenceType[Callable]) -> None:
        with _鎖:
            項目 = _封裝相依.get(身份)
            if 項目 is not None and 項目[0] is 封裝參照:
                _封裝相依.pop(身份, None)

    try:
        封裝參照 = weakref.ref(封裝, 移除)
        canonical參照 = weakref.ref(canonical)
    except TypeError:
        raise ValueError("Canonical相依封裝無效") from None
    with _鎖:
        _封裝相依[身份] = (封裝參照, canonical參照)


def 讀取Canonical相依封裝(封裝: object) -> Callable | None:
    """只依id與``is``讀取正式factory登錄且仍存活的wrapper identity。"""
    if not callable(封裝):
        return None
    with _鎖:
        項目 = _封裝相依.get(id(封裝))
        if 項目 is None or 項目[0]() is not 封裝:
            return None
        canonical = 項目[1]()
    return canonical if callable(canonical) else None


__all__ = ("讀取Canonical相依封裝",)
