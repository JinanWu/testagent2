"""A01 injected APIRouter 生命週期 ownership 驗證。"""

from typing import Any

from fastapi import APIRouter

from .設定 import 路由設定錯誤訊息

_預設生命週期類別 = type(APIRouter().__dict__["lifespan_context"])


def _字典(物件: Any) -> dict:
    """不執行 property 地讀 exact instance dict。"""
    值 = object.__getattribute__(物件, "__dict__")
    if type(值) is not dict:
        raise ValueError(路由設定錯誤訊息)
    return 值


def 驗證預設生命週期(路由器: APIRouter) -> None:
    """只接受 exact 空事件清單及由該 router 擁有的 canonical no-op lifespan。"""
    值 = _字典(路由器)
    for 名稱 in ("on_startup", "on_shutdown"):
        清單 = dict.get(值, 名稱)
        if type(清單) is not list or 清單:
            raise ValueError(路由設定錯誤訊息)
    生命週期 = dict.get(值, "lifespan_context")
    if type(生命週期) is not _預設生命週期類別:
        raise ValueError(路由設定錯誤訊息)
    生命字典 = _字典(生命週期)
    if len(生命字典) != 1 or dict.get(生命字典, "_router") is not 路由器:
        raise ValueError(路由設定錯誤訊息)
