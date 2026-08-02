"""P07 交易cleanup失敗後的共用連線隔離登錄。"""

from __future__ import annotations

from typing import Any


_污染連線: dict[int, Any] = {}


def 標記發布連線污染(連線: Any) -> None:
    """以強引用保存污染連線，避免物件識別碼重用。"""
    _污染連線[id(連線)] = 連線


def 發布連線已污染(連線: Any) -> bool:
    """使用exact identity判斷連線是否禁止再進入任何P07路徑。"""
    return _污染連線.get(id(連線)) is 連線
