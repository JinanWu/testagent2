"""測試用Published lifespan resource穩定定位。"""
from __future__ import annotations

from 繁中代理.發布介面.生產Published執行 import 生產Published執行資源


def 取得Published資源(應用程式) -> 生產Published執行資源:
    """依exact capability class定位唯一Published資源，不依賴tuple位置。"""
    資源們 = object.__getattribute__(應用程式, "state").發布介面資源
    if type(資源們) is not tuple:
        raise AssertionError("發布介面資源不是exact tuple")
    符合 = tuple(資源 for 資源 in 資源們 if type(資源) is 生產Published執行資源)
    if len(符合) != 1:
        raise AssertionError(f"Published資源數量錯誤：{len(符合)}")
    return 符合[0]
