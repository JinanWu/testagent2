"""Published Runtime 模型快照的安全 immutable 契約。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any

設定鍵 = frozenset({
    "provider", "model", "temperature", "max_tokens", "timeout_seconds",
    "structured_output", "schema_retry_count",
})
秘密片段 = ("api_key", "apikey", "token", "oauth", "secret", "credential", "password")
控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
設定訊息 = "發布模型設定不可用"
轉接訊息 = "模型供應商呼叫失敗"
_JSON最大深度 = 64
_JSON最大節點 = 10_000


class 模型轉接錯誤(RuntimeError):
    """模型轉接的安全固定錯誤。"""
    code = "model_adapter_error"


class 模型設定錯誤(模型轉接錯誤):
    """端點保存的模型設定無效或 provider 不存在。"""
    code = "endpoint_misconfigured"


class 模型逾時錯誤(模型轉接錯誤):
    """provider 已在其 timeout boundary 回報逾時。"""
    code = "model_timeout"


class 供應商逾時(TimeoutError):
    """provider adapter 可主動提出的專用逾時訊號。"""


@dataclass(frozen=True, slots=True, repr=False, init=False)
class 模型設定快照:
    """端點版本持久化的 exact model config；欄位名是外部 JSON contract。"""
    provider: str
    model: str
    temperature: float
    max_tokens: int
    timeout_seconds: float
    structured_output: bool
    schema_retry_count: int

    def __init__(self, provider, model, temperature, max_tokens, timeout_seconds,
                 structured_output, schema_retry_count) -> None:
        """正規化有限數值並強制一次 schema retry。"""
        try:
            if not (_是短字串(provider) and _是短字串(model)):
                raise ValueError
            if not (_是有限數值(temperature) and 0 <= temperature <= 2):
                raise ValueError
            if type(max_tokens) is not int or not 1 <= max_tokens <= 1_000_000:
                raise ValueError
            if not (_是有限數值(timeout_seconds) and 0 < timeout_seconds <= 900):
                raise ValueError
            if type(structured_output) is not bool or type(schema_retry_count) is not int or schema_retry_count != 1:
                raise ValueError
            object.__setattr__(self, "provider", provider)
            object.__setattr__(self, "model", model)
            object.__setattr__(self, "temperature", float(temperature))
            object.__setattr__(self, "max_tokens", max_tokens)
            object.__setattr__(self, "timeout_seconds", float(timeout_seconds))
            object.__setattr__(self, "structured_output", structured_output)
            object.__setattr__(self, "schema_retry_count", schema_retry_count)
        except 控制流程:
            self = provider = model = temperature = max_tokens = timeout_seconds = structured_output = schema_retry_count = None
            raise
        except BaseException:
            self = provider = model = temperature = max_tokens = timeout_seconds = structured_output = schema_retry_count = None
            raise 模型設定錯誤(設定訊息) from None

    def 轉成JSON物件(self) -> dict[str, Any]:
        """由重建後的 trusted scalar 產生 detached persisted JSON object。"""
        重建 = 供應商值 = 模型值 = 溫度值 = 最大權杖值 = 逾時秒數值 = 結構輸出值 = 重試次數值 = 結果 = None
        try:
            重建 = 重建設定(self)
            供應商值 = object.__getattribute__(重建, "provider")
            模型值 = object.__getattribute__(重建, "model")
            溫度值 = object.__getattribute__(重建, "temperature")
            最大權杖值 = object.__getattribute__(重建, "max_tokens")
            逾時秒數值 = object.__getattribute__(重建, "timeout_seconds")
            結構輸出值 = object.__getattribute__(重建, "structured_output")
            重試次數值 = object.__getattribute__(重建, "schema_retry_count")
            結果 = {
                "provider": 供應商值, "model": 模型值, "temperature": 溫度值,
                "max_tokens": 最大權杖值, "timeout_seconds": 逾時秒數值,
                "structured_output": 結構輸出值, "schema_retry_count": 重試次數值,
            }
            return 結果
        except 控制流程:
            self = 重建 = 供應商值 = 模型值 = 溫度值 = 最大權杖值 = 逾時秒數值 = 結構輸出值 = 重試次數值 = 結果 = None
            raise
        except BaseException:
            self = 重建 = 供應商值 = 模型值 = 溫度值 = 最大權杖值 = 逾時秒數值 = 結構輸出值 = 重試次數值 = 結果 = None
            raise 模型設定錯誤(設定訊息) from None


@dataclass(frozen=True, slots=True, repr=False, init=False)
class 模型轉接請求:
    """一次模型呼叫的 detached canonical JSON 輸入。"""
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    response_schema: dict[str, Any] | None

    def __init__(self, messages, tools, response_schema) -> None:
        """先捕捉全部欄位，再重建 module-owned JSON tree。"""
        訊息原值 = messages
        工具原值 = tools
        結構原值 = response_schema
        訊息 = 工具 = 結構 = None
        try:
            訊息 = 複製JSON(訊息原值, 1_000_000)
            工具 = 複製JSON(工具原值, 1_000_000)
            結構 = None if 結構原值 is None else 複製JSON(結構原值, 500_000)
            if type(訊息) is not list or type(工具) is not list or (結構 is not None and type(結構) is not dict):
                raise ValueError
            object.__setattr__(self, "messages", 訊息)
            object.__setattr__(self, "tools", 工具)
            object.__setattr__(self, "response_schema", 結構)
        except 控制流程:
            self = messages = tools = response_schema = 訊息原值 = 工具原值 = 結構原值 = 訊息 = 工具 = 結構 = None
            raise
        except BaseException:
            self = messages = tools = response_schema = 訊息原值 = 工具原值 = 結構原值 = 訊息 = 工具 = 結構 = None
            raise 模型轉接錯誤(轉接訊息) from None


@dataclass(frozen=True, slots=True, repr=False)
class 模型回應快照:
    """重建後、不洩漏 provider authoritative identity 的回應 DTO。"""
    text: str
    finish_reason: str
    usage: dict[str, Any]
    tool_calls: list[dict[str, Any]]


def 重建設定(值: object) -> 模型設定快照:
    """一次捕捉全部七欄後，驗證並重建 exact DTO。"""
    供應商值 = 模型值 = 溫度值 = 最大權杖值 = 逾時秒數值 = 結構輸出值 = 重試次數值 = None
    資料 = 鍵們 = 結果 = None
    try:
        if type(值) is 模型設定快照:
            供應商值 = object.__getattribute__(值, "provider")
            模型值 = object.__getattribute__(值, "model")
            溫度值 = object.__getattribute__(值, "temperature")
            最大權杖值 = object.__getattribute__(值, "max_tokens")
            逾時秒數值 = object.__getattribute__(值, "timeout_seconds")
            結構輸出值 = object.__getattribute__(值, "structured_output")
            重試次數值 = object.__getattribute__(值, "schema_retry_count")
        elif type(值) is dict:
            鍵們 = tuple(dict.keys(值))
            if len(鍵們) != 7:
                raise ValueError
            for 鍵 in 鍵們:
                if type(鍵) is not str:
                    raise ValueError
            if frozenset(鍵們) != 設定鍵:
                raise ValueError
            供應商值 = dict.__getitem__(值, "provider")
            模型值 = dict.__getitem__(值, "model")
            溫度值 = dict.__getitem__(值, "temperature")
            最大權杖值 = dict.__getitem__(值, "max_tokens")
            逾時秒數值 = dict.__getitem__(值, "timeout_seconds")
            結構輸出值 = dict.__getitem__(值, "structured_output")
            重試次數值 = dict.__getitem__(值, "schema_retry_count")
        else:
            raise ValueError
        結果 = 模型設定快照(
            供應商值, 模型值, 溫度值, 最大權杖值, 逾時秒數值,
            結構輸出值, 重試次數值,
        )
        return 結果
    except 控制流程:
        值 = 供應商值 = 模型值 = 溫度值 = 最大權杖值 = 逾時秒數值 = 結構輸出值 = 重試次數值 = 資料 = 鍵們 = 結果 = 鍵 = 片段 = None
        raise
    except BaseException:
        值 = 供應商值 = 模型值 = 溫度值 = 最大權杖值 = 逾時秒數值 = 結構輸出值 = 重試次數值 = 資料 = 鍵們 = 結果 = 鍵 = 片段 = None
        raise


def 複製JSON(值: Any, 上限: int) -> Any:
    """先捕捉整棵 exact-builtins descriptor，再複製、計量並重播防竄改。"""
    結果 = 原文 = 狀態 = 描述 = None
    try:
        if type(上限) is not int or 上限 < 1:
            raise ValueError
        狀態 = [0, 0, 上限]
        描述 = _捕捉JSON描述(值, set(), 狀態, 1)
        結果 = _由JSON描述複製(描述, 狀態)
        _重播JSON描述(描述)
        原文 = json.dumps(結果, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        _重播JSON描述(描述)
        if len(原文.encode("utf-8")) > 上限:
            raise ValueError
        return 結果
    except BaseException:
        值 = 結果 = 原文 = 狀態 = 描述 = None
        del 上限
        raise


def _計入JSON位元組(狀態: list[int], 數量: int) -> None:
    """在配置輸出節點前 totalize 累計 canonical bytes。"""
    狀態[1] += 數量
    if 狀態[1] > 狀態[2]:
        raise ValueError


def _計入JSON字串(狀態: list[int], 值: str) -> None:
    """不配置序列化字串地逐字計入 canonical UTF-8 與 JSON escape。"""
    剩餘 = 狀態[2] - 狀態[1]
    if len(值) > 剩餘 - 2:
        raise ValueError
    數量 = 2
    for 字元 in 值:
        編碼 = ord(字元)
        if 字元 in ('"', "\\") or 字元 in "\b\f\n\r\t":
            數量 += 2
        elif 編碼 < 0x20:
            數量 += 6
        elif 編碼 < 0x80:
            數量 += 1
        elif 編碼 < 0x800:
            數量 += 2
        elif 0xD800 <= 編碼 <= 0xDFFF:
            raise ValueError
        elif 編碼 < 0x10000:
            數量 += 3
        else:
            數量 += 4
        if 數量 > 剩餘:
            raise ValueError
    _計入JSON位元組(狀態, 數量)


def _捕捉JSON描述(值: Any, 已看: set[int], 狀態: list[int], 深度: int) -> tuple[Any, ...]:
    """不執行 callback 地先捕捉完整 exact-builtins container descriptor。"""
    描述 = 子描述 = 項目 = 鍵 = 子值 = None
    識別 = 長度 = 索引 = None
    try:
        if 深度 > _JSON最大深度:
            raise ValueError
        狀態[0] += 1
        if 狀態[0] > _JSON最大節點:
            raise ValueError
        if type(值) is str:
            if len(值) > 狀態[2]:
                raise ValueError
            return (None, 值)
        if 值 is None or type(值) in (bool, int):
            return (None, 值)
        if type(值) is float:
            if not math.isfinite(值):
                raise ValueError
            return (None, 值)
        if type(值) not in (list, dict):
            raise ValueError
        識別 = id(值)
        if 識別 in 已看:
            raise ValueError
        已看.add(識別)
        長度 = len(值)
        if 長度 > _JSON最大節點 - 狀態[0]:
            raise ValueError
        描述 = []
        if type(值) is list:
            索引 = 0
            while 索引 < 長度:
                項目 = list.__getitem__(值, 索引)
                子描述 = _捕捉JSON描述(項目, 已看, 狀態, 深度 + 1)
                描述.append((項目, 子描述))
                索引 += 1
            if len(值) != 長度:
                raise ValueError
            結果 = (list, 值, tuple(描述))
        else:
            for 鍵, 子值 in dict.items(值):
                if type(鍵) is not str:
                    raise ValueError
                子描述 = _捕捉JSON描述(子值, 已看, 狀態, 深度 + 1)
                描述.append((鍵, 子值, 子描述))
            if len(描述) != 長度 or len(值) != 長度:
                raise ValueError
            結果 = (dict, 值, tuple(描述))
        已看.remove(識別)
        return 結果
    except BaseException:
        值 = 已看 = 狀態 = 描述 = 子描述 = 項目 = 鍵 = 子值 = 結果 = None
        識別 = 長度 = 索引 = 深度 = None
        raise


def _由JSON描述複製(描述: tuple[Any, ...], 狀態: list[int]) -> Any:
    """只從已封存 descriptor 建立 module-owned tree 並執行 bounded byte accounting。"""
    類型 = 項目們 = 結果 = 原文 = 鍵文 = None
    try:
        類型 = 描述[0]
        if 類型 is None:
            結果 = 描述[1]
            if type(結果) is str:
                _計入JSON字串(狀態, 結果)
                原文 = json.dumps(結果, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                return 結果
            原文 = json.dumps(結果, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            _計入JSON位元組(狀態, len(原文.encode("utf-8")))
            return 結果
        項目們 = 描述[2]
        _計入JSON位元組(狀態, 2 + max(0, len(項目們) - 1))
        if 類型 is list:
            結果 = []
            for _, 子描述 in 項目們:
                結果.append(_由JSON描述複製(子描述, 狀態))
            return 結果
        結果 = {}
        for 鍵, _, 子描述 in 項目們:
            _計入JSON字串(狀態, 鍵)
            鍵文 = json.dumps(鍵, ensure_ascii=False, separators=(",", ":"))
            _計入JSON位元組(狀態, 1)
            結果[鍵] = _由JSON描述複製(子描述, 狀態)
        return 結果
    except BaseException:
        描述 = 狀態 = 類型 = 項目們 = 結果 = 原文 = 鍵文 = 鍵 = 子描述 = None
        raise


def _重播JSON描述(描述: tuple[Any, ...]) -> None:
    """以 built-in access 重播每個原容器，且在 key equality 前確認 exact type。"""
    類型 = 原容器 = 項目們 = 現鍵 = 現值 = 鍵 = 子值 = 子描述 = 迭代器 = None
    索引 = None
    try:
        類型 = 描述[0]
        if 類型 is None:
            return
        原容器 = 描述[1]
        項目們 = 描述[2]
        if type(原容器) is not 類型 or len(原容器) != len(項目們):
            raise ValueError
        if 類型 is list:
            for 索引 in range(len(項目們)):
                子值, 子描述 = 項目們[索引]
                if list.__getitem__(原容器, 索引) is not 子值:
                    raise ValueError
                _重播JSON描述(子描述)
            return
        迭代器 = iter(dict.items(原容器))
        for 鍵, 子值, 子描述 in 項目們:
            現鍵, 現值 = next(迭代器)
            if type(現鍵) is not str or 現鍵 != 鍵 or 現值 is not 子值:
                raise ValueError
            _重播JSON描述(子描述)
        try:
            next(迭代器)
            raise ValueError
        except StopIteration:
            return
    except BaseException:
        描述 = 類型 = 原容器 = 項目們 = 現鍵 = 現值 = 鍵 = 子值 = 子描述 = 迭代器 = None
        索引 = None
        raise


def _是短字串(值: object) -> bool:
    """判斷是否為 bounded exact nonblank str。"""
    return type(值) is str and 0 < len(值) <= 128 and not 值.isspace()


def _是有限數值(值: object) -> bool:
    """安全判斷 exact int/float 是否有限，含超大整數 totalization。"""
    if type(值) not in (int, float):
        return False
    try:
        return math.isfinite(值)  # type: ignore[arg-type]
    except (OverflowError, TypeError, ValueError):
        return False
