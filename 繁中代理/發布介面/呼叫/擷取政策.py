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


def 準備呼叫擷取(階段: 擷取階段, input: object, metadata: object | None) -> 呼叫擷取命令 | None:
    """slug miss 不取值；憑證前只留 metadata 摘要，驗證後保存全文。"""
    input快照 = metadata快照 = input_json = metadata_json = metadata_bytes = None
    計數器 = [0]
    try:
        if type(階段) is not 擷取階段:
            raise ValueError
        if 階段 is 擷取階段.SLUG_MISS:
            return None
        input快照 = _建立精確JSON快照(input, 0, 計數器)
        input = None
        input_json = 建立正規JSON(input快照)
        if len(input_json.encode("utf-8")) > _最大輸入位元組:
            raise ValueError
        if metadata is not None:
            計數器[0] = 0
            metadata快照 = _建立精確JSON快照(metadata, 0, 計數器)
            metadata = None
            metadata_canonical = 建立正規JSON(metadata快照)
            metadata_bytes = metadata_canonical.encode("utf-8")
            if len(metadata_bytes) > _最大METADATA位元組:
                raise ValueError
            metadata_size = len(metadata_bytes)
            metadata_sha = hashlib.sha256(metadata_bytes).hexdigest()
            if 階段 is 擷取階段.AUTHENTICATED:
                metadata_json = metadata_canonical
        else:
            metadata_size = metadata_sha = None
        return 呼叫擷取命令(
            階段, "user", input_json, metadata_json, metadata_size, metadata_sha,
        )
    except BaseException as 邊界錯誤:
        是控制流程 = type(邊界錯誤) in _控制流程例外
        階段 = input = metadata = input快照 = metadata快照 = input_json = metadata_json = None
        metadata_bytes = metadata_canonical = metadata_size = metadata_sha = 計數器 = None
        if 是控制流程:
            raise
    raise 呼叫擷取錯誤("呼叫擷取失敗") from None


def 準備含敏感偵測的呼叫擷取(
    階段: 擷取階段, input: object, metadata: object | None, *, response_data: object | None = None,
) -> 敏感偵測擷取結果 | None:
    """只從 L03 canonical 脫離值產生位置旁路，不寫入或修改管線物件。"""
    命令 = input值 = metadata值 = response快照 = response值 = 命中們 = None
    metadata文字 = response文字 = 計數器 = None
    try:
        命令 = 準備呼叫擷取(階段, input, metadata)
        input = metadata = None
        if 命令 is None:
            response_data = None
            return None
        if response_data is not None and 階段 is not 擷取階段.AUTHENTICATED:
            raise ValueError
        命令 = _重建旁路命令(命令)
        input值 = _解析正規JSON(object.__getattribute__(命令, "input_json"))
        metadata文字 = object.__getattribute__(命令, "metadata_json")
        metadata值 = None if metadata文字 is None else _解析正規JSON(metadata文字)
        命中們 = list(_偵測目標("input", input值))
        input值 = None
        if metadata值 is not None:
            命中們.extend(_偵測目標("metadata", metadata值))
            metadata值 = None
        if response_data is not None:
            計數器 = [0]
            response快照 = _建立精確JSON快照(response_data, 0, 計數器)
            response_data = None
            response文字 = 建立正規JSON(response快照)
            if len(str.encode(response文字, "utf-8")) > _最大輸入位元組:
                raise ValueError
            response值 = _解析正規JSON(response文字)
            response快照 = response文字 = 計數器 = None
            命中們.extend(_偵測目標("response_data", response值))
            response值 = None
        命中們.sort(key=lambda x: (x.目標代碼, x.JSON路徑, x.開始, x.結束, x.類型代碼))
        return 敏感偵測擷取結果(命令, tuple(命中們),
                         ("sensitive_data_detected",) if 命中們 else ())
    except BaseException as 錯誤:
        是控制流程 = type(錯誤) in _控制流程例外
        階段 = input = metadata = response_data = 命令 = input值 = metadata值 = None
        response快照 = response值 = 命中們 = metadata文字 = response文字 = 計數器 = 錯誤 = None
        if 是控制流程:
            raise
    raise 敏感旁路錯誤("敏感資料旁路建立失敗") from None


def _偵測目標(目標, 值) -> tuple[目標敏感命中, ...]:
    """轉成 target-qualified detached copies；清單以空鍵包裝後還原原路徑。"""
    原命中們 = 結果 = 命中 = 路徑 = 包裝 = None
    try:
        包裝 = type(值) not in (str, dict)
        原命中們 = 偵測敏感資料({"": 值} if 包裝 else 值)
        結果 = []
        for 命中 in 原命中們:
            路徑 = object.__getattribute__(命中, "JSON路徑")
            if 包裝:
                路徑 = 路徑[1:]
            結果.append(目標敏感命中(
                目標, object.__getattribute__(命中, "類型代碼"), 路徑,
                object.__getattribute__(命中, "開始"), object.__getattribute__(命中, "結束"),
            ))
        return tuple(結果)
    except BaseException:
        目標 = 值 = 原命中們 = 結果 = 命中 = 路徑 = 包裝 = None
        raise


def _重建目標命中(命中) -> 目標敏感命中:
    """從已驗證固定槽位建立不共享副本。"""
    try:
        if type(命中) is not 目標敏感命中:
            raise ValueError
        return 目標敏感命中(
            object.__getattribute__(命中, "目標代碼"), object.__getattribute__(命中, "類型代碼"),
            object.__getattribute__(命中, "JSON路徑"), object.__getattribute__(命中, "開始"),
            object.__getattribute__(命中, "結束"),
        )
    except BaseException:
        命中 = None
        raise


def _重建旁路命令(命令) -> 呼叫擷取命令:
    """完整讀取、驗證 canonical 與階段矩陣，再建立不共享的命令。"""
    階段 = role = input文字 = metadata文字 = metadata大小 = metadata摘要 = 位元組 = 字元 = None
    try:
        if type(命令) is not 呼叫擷取命令:
            raise ValueError
        階段 = object.__getattribute__(命令, "階段")
        role = object.__getattribute__(命令, "metadata_role")
        input文字 = object.__getattribute__(命令, "input_json")
        metadata文字 = object.__getattribute__(命令, "metadata_json")
        metadata大小 = object.__getattribute__(命令, "metadata_size_bytes")
        metadata摘要 = object.__getattribute__(命令, "metadata_sha256")
        if (type(階段) is not 擷取階段 or 階段 is 擷取階段.SLUG_MISS
                or type(role) is not str or role != "user" or type(input文字) is not str):
            raise ValueError
        if len(str.encode(input文字, "utf-8")) > _最大輸入位元組:
            raise ValueError
        _解析正規JSON(input文字)
        if (metadata大小 is None) != (metadata摘要 is None):
            raise ValueError
        if metadata大小 is not None:
            if (type(metadata大小) is not int or not 0 <= metadata大小 <= _最大METADATA位元組
                    or type(metadata摘要) is not str or len(metadata摘要) != 64):
                raise ValueError
            for 字元 in metadata摘要:
                if 字元 not in "0123456789abcdef":
                    raise ValueError
        if metadata文字 is None:
            if 階段 is 擷取階段.AUTHENTICATED and metadata大小 is not None:
                raise ValueError
        else:
            if 階段 is not 擷取階段.AUTHENTICATED or type(metadata文字) is not str:
                raise ValueError
            _解析正規JSON(metadata文字)
            位元組 = str.encode(metadata文字, "utf-8")
            if len(位元組) != metadata大小 or hashlib.sha256(位元組).hexdigest() != metadata摘要:
                raise ValueError
        return 呼叫擷取命令(階段, role, input文字, metadata文字, metadata大小, metadata摘要)
    except BaseException:
        命令 = 階段 = role = input文字 = metadata文字 = metadata大小 = metadata摘要 = None
        位元組 = 字元 = None
        raise


def 寫入呼叫擷取(
    儲存庫: 呼叫建立儲存庫, 命令: 呼叫擷取命令,
    endpoint_id: str, endpoint_version_id: str, request_id: str, *,
    credential_id: str | None = None, session_id: str | None = None,
    message_id: str | None = None,
) -> str:
    """驗證完整不可變命令後，精確一次委派 L01 repository 寫入。"""
    階段值 = role值 = input_json值 = metadata_json值 = None
    metadata_size值 = metadata_sha值 = input值 = metadata值 = None
    try:
        (階段值, role值, input_json值, metadata_json值,
         metadata_size值, metadata_sha值) = _驗證寫入值(
            命令, endpoint_id, endpoint_version_id, request_id,
            credential_id, session_id, message_id,
        )
        input值 = _解析正規JSON(input_json值)
        metadata值 = None if metadata_json值 is None else _解析正規JSON(metadata_json值)
        return 儲存庫.建立已解析呼叫(
            endpoint_id, endpoint_version_id, request_id, input值,
            credential_id=credential_id, session_id=session_id, message_id=message_id,
            metadata=metadata值, metadata_size_bytes=metadata_size值,
            metadata_sha256=metadata_sha值,
        )
    except BaseException as 邊界錯誤:
        是控制流程 = type(邊界錯誤) in _控制流程例外
        儲存庫 = 命令 = endpoint_id = endpoint_version_id = request_id = None
        credential_id = session_id = message_id = input值 = metadata值 = None
        階段值 = role值 = input_json值 = metadata_json值 = None
        metadata_size值 = metadata_sha值 = None
        if 是控制流程:
            raise
    raise 呼叫擷取錯誤("呼叫擷取寫入失敗") from None


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


def _解析正規JSON(原始文字) -> object:
    """本地嚴格解析並驗證 canonical，所有失敗都先清除原文與結果。"""
    解析結果 = 精確結果 = 正規文字 = None
    計數器 = [0]
    無效 = False
    try:
        if type(原始文字) is not str:
            raise ValueError
        解析結果 = json.loads(
            原始文字,
            object_pairs_hook=_建立無重複鍵物件,
            parse_constant=_拒絕非有限常數,
        )
        精確結果 = _建立精確JSON快照(解析結果, 0, 計數器)
        解析結果 = None
        正規文字 = json.dumps(
            精確結果, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        if type(正規文字) is not str or 正規文字 != 原始文字:
            raise ValueError
        return 精確結果
    except BaseException as 邊界錯誤:
        是控制流程 = type(邊界錯誤) in _控制流程例外
        原始文字 = 解析結果 = 精確結果 = 正規文字 = 計數器 = None
        if 是控制流程:
            raise
        無效 = True
    if 無效:
        raise ValueError
    raise AssertionError


def _建立無重複鍵物件(鍵值對) -> dict[str, object]:
    """供本地 decoder 建立 exact dict，拒絕重複鍵。"""
    結果 = {}
    已見 = set()
    鍵 = 值 = None
    try:
        if type(鍵值對) is not list:
            raise ValueError
        for 鍵, 值 in list.__iter__(鍵值對):
            if type(鍵) is not str or 鍵 in 已見:
                raise ValueError
            已見.add(鍵)
            結果[鍵] = 值
        return 結果
    except BaseException as 邊界錯誤:
        是控制流程 = type(邊界錯誤) in _控制流程例外
        鍵值對 = 結果 = 已見 = 鍵 = 值 = None
        if 是控制流程:
            raise
        raise


def _拒絕非有限常數(常數) -> None:
    """拒絕 JSON 規格外的 NaN 與 Infinity token。"""
    常數 = None
    raise ValueError
