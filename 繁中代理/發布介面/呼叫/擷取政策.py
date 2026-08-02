"""依憑證驗證階段準備不可變、脫離呼叫者的呼叫擷取命令。"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, cast

from ..嚴格JSON import 建立正規JSON
from .敏感偵測 import 敏感命中, 偵測敏感資料

_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_最大深度 = 64
_最大節點 = 10_000
_最大輸入位元組 = 1_048_576
_最大METADATA位元組 = 262_144


class 呼叫擷取錯誤(RuntimeError):
    """代表擷取階段、JSON 快照或儲存委派固定拒絕。"""


class 敏感旁路錯誤(RuntimeError):
    """代表非修改式敏感偵測旁路固定拒絕。"""


class 擷取階段(Enum):
    """不可用任意字串偽造的 slug／憑證管線決策。"""

    SLUG_MISS = "slug_miss"
    PRE_CREDENTIAL_REJECTION = "pre_credential_rejection"
    INVALID_API_KEY = "invalid_api_key"
    AUTHENTICATED = "authenticated"


@dataclass(frozen=True, slots=True)
class 呼叫擷取命令:
    """只含 canonical JSON 與摘要純值的不可變儲存命令。"""

    階段: 擷取階段
    metadata_role: str
    input_json: str
    metadata_json: str | None
    metadata_size_bytes: int | None
    metadata_sha256: str | None


@dataclass(frozen=True, slots=True)
class 目標敏感命中:
    """加入固定偵測目標、仍只含位置的不可變命中。"""

    目標代碼: str
    類型代碼: str
    JSON路徑: str
    開始: int
    結束: int

    def __post_init__(self) -> None:
        """精確驗證固定 target 與 L05 位置欄位。"""
        暫存 = 目標 = None
        try:
            目標 = object.__getattribute__(self, "目標代碼")
            if type(self) is not 目標敏感命中 or type(目標) is not str or 目標 not in {
                    "input", "metadata", "response_data"}:
                raise ValueError
            暫存 = 敏感命中(
                object.__getattribute__(self, "類型代碼"),
                object.__getattribute__(self, "JSON路徑"),
                object.__getattribute__(self, "開始"), object.__getattribute__(self, "結束"),
            )
            self = 目標 = 暫存 = None
            return
        except BaseException as 錯誤:
            是控制流程 = type(錯誤) in _控制流程例外
            self = 目標 = 暫存 = 錯誤 = None
            if 是控制流程:
                raise
        raise 敏感旁路錯誤("目標敏感命中格式無效") from None


@dataclass(frozen=True, slots=True)
class 敏感偵測擷取結果:
    """脫離命令、位置命中與固定警告組成的純旁路結果。"""

    命令: 呼叫擷取命令
    命中們: tuple[目標敏感命中, ...]
    警告代碼們: tuple[str, ...]

    def __post_init__(self) -> None:
        """重建所有子 DTO，拒絕共享或偽造槽位。"""
        新命令 = 新命中們 = 新命中串列 = 命中 = None
        原命中們 = 警告們 = 預期警告 = None
        try:
            if type(self) is not 敏感偵測擷取結果:
                raise ValueError
            新命令 = _重建旁路命令(object.__getattribute__(self, "命令"))
            原命中們 = object.__getattribute__(self, "命中們")
            警告們 = object.__getattribute__(self, "警告代碼們")
            if type(原命中們) is not tuple or type(警告們) is not tuple:
                raise ValueError
            新命中串列 = []
            for 命中 in 原命中們:
                新命中串列.append(_重建目標命中(命中))
            新命中們 = tuple(新命中串列)
            預期警告 = ("sensitive_data_detected",) if 新命中們 else ()
            if 新命中們:
                if (len(警告們) != 1 or type(警告們[0]) is not str
                        or 警告們[0] != "sensitive_data_detected"):
                    raise ValueError
            elif len(警告們) != 0:
                raise ValueError
            object.__setattr__(self, "命令", 新命令)
            object.__setattr__(self, "命中們", 新命中們)
            object.__setattr__(self, "警告代碼們", 預期警告)
            self = 新命令 = 新命中們 = 新命中串列 = 命中 = None
            原命中們 = 警告們 = 預期警告 = None
            return
        except BaseException as 錯誤:
            是控制流程 = type(錯誤) in _控制流程例外
            self = 新命令 = 新命中們 = 新命中串列 = 命中 = None
            原命中們 = 警告們 = 預期警告 = 錯誤 = None
            if 是控制流程:
                raise
        raise 敏感旁路錯誤("敏感偵測擷取結果格式無效") from None


class 呼叫建立儲存庫(Protocol):
    """L01 呼叫儲存庫供政策委派的最小介面。"""

    def 建立已解析呼叫(
        self, endpoint_id: str, endpoint_version_id: str, request_id: str, input: object, *,
        credential_id: str | None = None, session_id: str | None = None,
        message_id: str | None = None, metadata: object | None = None,
        metadata_size_bytes: int | None = None, metadata_sha256: str | None = None,
    ) -> str: ...
def _建立精確JSON快照(值: object, 深度: int, 計數器: list[int]) -> object:
    """單次遞迴建立 module-owned JSON 樹，並限制深度與節點數。"""
    結果串列 = 結果字典 = 項目 = 鍵 = None
    try:
        計數器[0] += 1
        if 深度 > _最大深度 or 計數器[0] > _最大節點:
            raise ValueError
        值型別 = type(值)
        if 值 is None or 值型別 in (bool, int, str):
            return 值
        if 值型別 is float:
            if math.isfinite(cast(float, 值)):
                return 值
            raise ValueError
        if 值型別 is list:
            結果串列 = []
            for 項目 in list.__iter__(cast(list[object], 值)):
                結果串列.append(_建立精確JSON快照(項目, 深度 + 1, 計數器))
            return 結果串列
        if 值型別 is dict:
            結果字典 = {}
            for 鍵, 項目 in dict.items(cast(dict[object, object], 值)):
                if type(鍵) is not str:
                    raise ValueError
                結果字典[鍵] = _建立精確JSON快照(項目, 深度 + 1, 計數器)
            return 結果字典
        raise ValueError
    except BaseException as 邊界錯誤:
        是控制流程 = type(邊界錯誤) in _控制流程例外
        值 = 結果串列 = 結果字典 = 項目 = 鍵 = 計數器 = None
        if 是控制流程:
            raise
        raise


def _驗證寫入值(命令, endpoint_id, endpoint_version_id, request_id,
             credential_id, session_id, message_id
             ) -> tuple[擷取階段, str, str, str | None, int | None, str | None]:
    """在解析或 repository lookup 前驗證 DTO 與所有直接識別碼。"""
    階段值 = role值 = input_json值 = metadata_json值 = None
    metadata_size值 = metadata_sha值 = input_bytes = metadata_bytes = None
    識別碼 = 字元 = None
    無效 = False
    try:
        if type(命令) is not 呼叫擷取命令:
            raise ValueError
        階段值 = object.__getattribute__(命令, "階段")
        role值 = object.__getattribute__(命令, "metadata_role")
        input_json值 = object.__getattribute__(命令, "input_json")
        metadata_json值 = object.__getattribute__(命令, "metadata_json")
        metadata_size值 = object.__getattribute__(命令, "metadata_size_bytes")
        metadata_sha值 = object.__getattribute__(命令, "metadata_sha256")
        if type(階段值) is not 擷取階段 or 階段值 is 擷取階段.SLUG_MISS:
            raise ValueError
        if type(role值) is not str or role值 != "user":
            raise ValueError
        for 識別碼 in (endpoint_id, endpoint_version_id, request_id):
            if type(識別碼) is not str or not str.strip(識別碼):
                raise ValueError
        for 識別碼 in (credential_id, session_id, message_id):
            if 識別碼 is not None and (type(識別碼) is not str or not str.strip(識別碼)):
                raise ValueError
        識別碼 = None
        if type(input_json值) is not str:
            raise ValueError
        input_bytes = str.encode(input_json值, "utf-8")
        if len(input_bytes) > _最大輸入位元組:
            raise ValueError
        if metadata_json值 is not None and type(metadata_json值) is not str:
            raise ValueError
        if metadata_size值 is not None:
            if (type(metadata_size值) is not int or metadata_size值 < 0
                    or metadata_size值 > _最大METADATA位元組):
                raise ValueError
        if metadata_sha值 is not None:
            if type(metadata_sha值) is not str or len(metadata_sha值) != 64:
                raise ValueError
            for 字元 in metadata_sha值:
                if 字元 not in "0123456789abcdef":
                    raise ValueError
            字元 = None
        if (metadata_size值 is None) != (metadata_sha值 is None):
            raise ValueError
        if 階段值 is 擷取階段.AUTHENTICATED:
            if credential_id is None:
                raise ValueError
            if metadata_json值 is None:
                if metadata_size值 is not None or metadata_sha值 is not None:
                    raise ValueError
            else:
                if metadata_size值 is None or metadata_sha值 is None:
                    raise ValueError
                metadata_bytes = str.encode(metadata_json值, "utf-8")
                if (len(metadata_bytes) > _最大METADATA位元組
                        or len(metadata_bytes) != metadata_size值
                        or hashlib.sha256(metadata_bytes).hexdigest() != metadata_sha值):
                    raise ValueError
        elif credential_id is not None or metadata_json值 is not None:
            raise ValueError
        return (階段值, role值, input_json值, metadata_json值,
                metadata_size值, metadata_sha值)
    except BaseException as 邊界錯誤:
        是控制流程 = type(邊界錯誤) in _控制流程例外
        命令 = endpoint_id = endpoint_version_id = request_id = None
        credential_id = session_id = message_id = 識別碼 = 字元 = None
        階段值 = role值 = input_json值 = metadata_json值 = None
        metadata_size值 = metadata_sha值 = input_bytes = metadata_bytes = None
        if 是控制流程:
            raise
        無效 = True
    if 無效:
        raise ValueError
    raise AssertionError
