"""發布介面應用程式的 immutable composition 契約。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from fastapi import APIRouter

from .設定 import 路由設定錯誤訊息


class 發布介面資源(Protocol):
    """由應用程式 lifespan 擁有且可非同步關閉的資源。"""

    def 關閉(self) -> Awaitable[None]:
        """釋放資源；lifespan 對每個成功建立的資源只呼叫一次。"""
        ...


發布介面資源工廠 = Callable[[], Awaitable[發布介面資源]]
"""在 startup 建立一個由該 lifespan 獨佔資源的工廠型別。"""


@dataclass(frozen=True, slots=True)
class 發布介面相依項:
    """封裝明確 routers 與 process-lifespan 資源工廠。

    容器本身 immutable；APIRouter 仍可變，因此應用程式 factory 會在每次
    publication 前重新驗證完整 route inventory。
    """

    路由器清單: tuple[APIRouter, ...]
    資源工廠清單: tuple[發布介面資源工廠, ...]

    def __post_init__(self) -> None:
        """拒絕 mutable collections、非 router、重複 identity 與非 callable factory。"""
        if type(self.路由器清單) is not tuple or type(self.資源工廠清單) is not tuple:
            raise ValueError(路由設定錯誤訊息)
        已見路由器: set[int] = set()
        for 路由器 in self.路由器清單:
            if not isinstance(路由器, APIRouter) or id(路由器) in 已見路由器:
                raise ValueError(路由設定錯誤訊息)
            已見路由器.add(id(路由器))
        if any(not callable(工廠) for 工廠 in self.資源工廠清單):
            raise ValueError(路由設定錯誤訊息)
