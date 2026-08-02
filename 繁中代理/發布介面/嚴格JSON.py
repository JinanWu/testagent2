"""嚴格 JSON 解析、正規化與雜湊工具。"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


class 嚴格JSON錯誤(ValueError):
    """代表輸入不是發布介面允許的嚴格 JSON。"""


def 解析嚴格JSON(原始文字: str) -> Any:
    """解析嚴格 JSON 字串，拒絕重複鍵、非有限數值與語法錯誤。"""
    if not isinstance(原始文字, str):
        raise 嚴格JSON錯誤("嚴格 JSON 解析只接受 str 輸入")

    錯誤類型: str | None = None
    try:
        解析結果 = json.loads(
            原始文字,
            object_pairs_hook=_拒絕重複鍵並建立物件,
            parse_constant=_拒絕非有限數值常數,
        )
    except (嚴格JSON錯誤, json.JSONDecodeError, ValueError) as 錯誤:
        錯誤類型 = 錯誤.__class__.__name__

    if 錯誤類型 is not None:
        原始文字 = None
        raise 嚴格JSON錯誤(f"JSON 語法或數值不符合嚴格契約: {錯誤類型}")

    return 解析結果


def 建立正規JSON(資料: Any) -> str:
    """由 JSON value 建立 deterministic canonical JSON 字串。"""
    建立錯誤 = False
    try:
        _確認JSON值(資料)
        正規JSON = json.dumps(
            資料,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (嚴格JSON錯誤, TypeError, ValueError, RecursionError):
        建立錯誤 = True

    if 建立錯誤:
        資料 = None
        raise 嚴格JSON錯誤("資料不符合正規 JSON 契約")

    return 正規JSON


def 計算正規JSON雜湊(資料: Any) -> str:
    """計算正規 JSON UTF-8 bytes 的 SHA-256 hex digest。"""
    正規JSON = 建立正規JSON(資料)
    return hashlib.sha256(正規JSON.encode("utf-8")).hexdigest()


def _拒絕重複鍵並建立物件(鍵值對: list[tuple[str, Any]]) -> dict[str, Any]:
    已見鍵: set[str] = set()
    物件: dict[str, Any] = {}
    for 鍵, 值 in 鍵值對:
        if 鍵 in 已見鍵:
            raise 嚴格JSON錯誤("JSON object 含有重複鍵")
        已見鍵.add(鍵)
        物件[鍵] = 值
    return 物件


def _拒絕非有限數值常數(常數: str) -> None:
    raise 嚴格JSON錯誤("JSON 數值必須是有限值")


def _確認JSON值(資料: Any, 目前路徑: set[int] | None = None) -> None:
    if 資料 is None or isinstance(資料, (str, bool)):
        return
    if isinstance(資料, int) and not isinstance(資料, bool):
        return
    if isinstance(資料, float):
        if math.isfinite(資料):
            return
        raise 嚴格JSON錯誤("JSON float 必須是有限值")
    if isinstance(資料, (list, dict)):
        if 目前路徑 is None:
            目前路徑 = set()
        容器id = id(資料)
        if 容器id in 目前路徑:
            raise 嚴格JSON錯誤("資料包含循環參照")
        目前路徑.add(容器id)
        try:
            if isinstance(資料, list):
                for 項目 in 資料:
                    _確認JSON值(項目, 目前路徑)
                return
            for 鍵, 值 in 資料.items():
                if not isinstance(鍵, str):
                    raise 嚴格JSON錯誤("JSON object key 必須是 str")
                _確認JSON值(值, 目前路徑)
            return
        finally:
            目前路徑.remove(容器id)
    raise 嚴格JSON錯誤("資料包含非 JSON value 型別")
