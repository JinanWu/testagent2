"""定義伺服器端規劃器的不可變輸入、輸出與嚴格驗證契約。

公開範圍：提供規劃器資料物件、轉接協定、驗證函式與固定文字回應結構。
例外：驗證函式可能拋出 ``ValueError`` 或 ``規劃器輸出無效``；匯入本模組不拋出業務例外。
副作用：匯入時建立正規表示式、不可變欄位集合與結構常數，沒有外部呼叫或持久化副作用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from jsonschema import Draft202012Validator

from ..協定 import 授權工具, 授權技能
from ..嚴格JSON import 建立正規JSON, 解析嚴格JSON

識別規則 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
短名規則 = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
輸出欄位 = frozenset({
    "endpoint_name", "suggested_slug", "behavior_summary", "selected_skills",
    "recommended_tools", "tool_capabilities", "system_prompt", "input_schema",
    "response_schema", "human_docs", "rate_limit", "warnings",
})
文字回應結構 = {
    "type": "object", "properties": {"answer": {"type": "string"}},
    "required": ["answer"], "additionalProperties": False,
}

class 規劃器輸出無效(ValueError):
    """表示供應商輸出不符合固定資料傳輸物件。

    參數：沿用 ``ValueError`` 的訊息與引數。
    回傳：不適用；此類別用於建立或拋出例外。
    例外：在規劃器輸出無法解析或驗證時由邊界拋出。
    副作用：建立與拋出例外皆無外部副作用。
    """

class 規劃器不可用(RuntimeError):
    """表示供應商或規劃器信任邊界無法安全完成。

    參數：沿用 ``RuntimeError`` 的訊息與引數。
    回傳：不適用；此類別用於建立或拋出例外。
    例外：在供應商呼叫或規劃流程無法安全完成時由邊界拋出。
    副作用：建立與拋出例外皆無外部副作用。
    """

@dataclass(frozen=True, slots=True)
class 規劃器輸入:
    """保存規劃器唯一可見的需求與完整授權快照。

    欄位：``原始需求`` 是需求文字；``回應模式`` 是文字或結構化模式；``技能`` 是已選授權技能；
    ``授權工具`` 是完整授權工具快照。
    回傳：建構後得到不可變的 ``規劃器輸入``。
    例外：欄位驗證不在建構器執行；呼叫端應先使用本模組驗證函式，其可能拋出 ``ValueError``。
    副作用：建構物件無外部副作用，且物件不可變。
    """
    原始需求: str
    回應模式: Literal["text", "structured"]
    技能: tuple[授權技能, ...]
    授權工具: tuple[授權工具, ...]

@dataclass(frozen=True, slots=True)
class 規劃器輸出:
    """保存已驗證且以正規 JSON 表示的精確規劃器資料物件。

    欄位：``_正規JSON`` 是經驗證的正規 JSON 文字。
    回傳：建構後得到不可變的 ``規劃器輸出``。
    例外：直接建構不解析內容；讀取屬性時可能拋出 JSON 解析或鍵值相關例外。
    副作用：建構物件無外部副作用，且物件不可變。
    """
    _正規JSON: str

    @property
    def 預覽(self) -> dict[str, Any]:
        """解析並回傳規劃器預覽。

        參數：除目前物件外沒有顯式參數。
        回傳：每次回傳由 ``_正規JSON`` 新解析出的字典。
        例外：內容失效時可能拋出嚴格 JSON 解析例外。
        副作用：不修改物件狀態，且無外部副作用。
        """
        return 解析嚴格JSON(self._正規JSON)

    @property
    def 建議工具(self) -> tuple[str, ...]:
        """讀取預覽中的建議工具。

        參數：除目前物件外沒有顯式參數。
        回傳：依預覽順序建立的工具名稱元組。
        例外：可能拋出嚴格 JSON 解析例外、``KeyError`` 或無法轉為元組的型別例外。
        副作用：不修改物件狀態，且無外部副作用。
        """
        return tuple(self.預覽["recommended_tools"])

class 規劃器轉接器(Protocol):
    """規範只接收分離式伺服器端輸入的不可變轉接邊界。

    方法：``產生`` 接收 ``規劃器輸入`` 並回傳嚴格 JSON 文字。
    例外：實作者可拋出供應商例外或 ``規劃器不可用``。
    副作用：協定本身無副作用；實作者必須揭露外部供應商呼叫等副作用。
    """
    def 產生(self, 輸入: 規劃器輸入, /) -> str:
        """產生候選規劃器輸出。

        參數：``輸入`` 是分離式、不可變的授權規劃快照。
        回傳：待驗證的嚴格 JSON 文字。
        例外：實作者可拋出供應商例外或 ``規劃器不可用``，控制流例外應原樣傳遞。
        副作用：依實作者而定；可能呼叫外部供應商，不應修改 ``輸入``。
        """
        ...

def 驗證選擇(技能: Any, 回應模式: Any) -> tuple[tuple[str, ...], str]:
    """在查詢權限前驗證技能選擇與回應模式。

    參數：``技能`` 是候選技能名稱元組；``回應模式`` 是候選模式。
    回傳：原技能元組及已確認的模式。
    例外：數量、型別、排序、唯一性、識別格式或模式不符時拋出 ``ValueError``。
    副作用：無外部副作用，也不修改輸入。
    """
    if type(技能) is not tuple or not 1 <= len(技能) <= 32 or 回應模式 not in ("text", "structured"):
        raise ValueError("規劃草稿請求無效") from None
    前一 = None
    for 名稱 in 技能:
        if type(名稱) is not str or 識別規則.fullmatch(名稱) is None or (前一 is not None and 名稱 <= 前一):
            raise ValueError("規劃草稿請求無效") from None
        前一 = 名稱
    return 技能, 回應模式

def 建立規劃器輸入(需求: str, 模式: str, 技能: tuple[授權技能, ...], 工具: tuple[授權工具, ...]) -> 規劃器輸入:
    """由精確、正規且分離式的權威資料建立規劃器輸入。

    參數：``需求`` 是原始需求；``模式`` 是回應模式；``技能`` 是已選授權技能；``工具`` 是完整授權工具。
    回傳：不可變的 ``規劃器輸入``。
    例外：需求、模式、技能或工具不符合固定契約時拋出 ``ValueError``。
    副作用：無外部副作用，也不修改傳入快照。
    """
    if type(需求) is not str or not 需求.strip() or 需求 != 需求.strip() or len(需求.encode("utf-8")) > 16_384:
        raise ValueError("規劃草稿請求無效") from None
    驗證選擇(tuple(項目.名稱 for 項目 in 技能), 模式)
    if type(工具) is not tuple or any(type(項目) is not 授權工具 for 項目 in 工具):
        raise ValueError("規劃草稿請求無效") from None
    return 規劃器輸入(需求, 模式, 技能, 工具)

def 驗證規劃器輸出(原始文字: Any, 輸入: 規劃器輸入) -> 規劃器輸出:
    """驗證規劃器輸出的完整欄位、授權內容、結構與大小界限。

    參數：``原始文字`` 是待驗證 JSON；``輸入`` 是比對技能、工具及模式的權威快照。
    回傳：保存正規 JSON 的不可變 ``規劃器輸出``。
    例外：任何資料或結構驗證失敗皆轉為 ``規劃器輸出無效``；控制流例外原樣傳遞。
    副作用：只建立解析與正規化結果，無外部副作用且不修改輸入。
    """
    try:
        if type(原始文字) is not str or len(原始文字.encode("utf-8")) > 131_072:
            raise ValueError
        值 = 解析嚴格JSON(原始文字)
        if type(值) is not dict or frozenset(值) != 輸出欄位:
            raise ValueError
        _驗證文字(值["endpoint_name"], 1, 128)
        _驗證文字(值["behavior_summary"], 1, 2_000)
        _驗證文字(值["system_prompt"], 1, 32_768)
        _驗證文字(值["human_docs"], 1, 16_384)
        if type(值["suggested_slug"]) is not str or not 1 <= len(值["suggested_slug"]) <= 63 or 短名規則.fullmatch(值["suggested_slug"]) is None:
            raise ValueError
        技能名稱 = [項目.名稱 for 項目 in 輸入.技能]
        if 值["selected_skills"] != 技能名稱:
            raise ValueError
        工具名稱 = _驗證名稱陣列(值["recommended_tools"], 0, 64)
        授權名稱 = {項目.名稱 for 項目 in 輸入.授權工具}
        if not set(工具名稱).issubset(授權名稱):
            raise ValueError
        能力 = 值["tool_capabilities"]
        if type(能力) is not dict or list(能力) != 工具名稱:
            raise ValueError
        for 名稱, 說明 in 能力.items():
            _驗證文字(說明, 1, 500)
        _驗證結構(值["input_schema"], 允許空=True)
        _驗證結構(值["response_schema"], 允許空=False)
        if 輸入.回應模式 == "text":
            值["response_schema"] = dict(文字回應結構)
        elif 值["response_schema"].get("type") != "object":
            raise ValueError
        限流 = 值["rate_limit"]
        if type(限流) is not dict or set(限流) != {"endpoint_per_minute", "credential_per_minute"}:
            raise ValueError
        if type(限流["endpoint_per_minute"]) is not int or not 1 <= 限流["endpoint_per_minute"] <= 10_000:
            raise ValueError
        if type(限流["credential_per_minute"]) is not int or not 1 <= 限流["credential_per_minute"] <= 10_000:
            raise ValueError
        警告 = 值["warnings"]
        if type(警告) is not list or len(警告) > 32:
            raise ValueError
        for 項目 in 警告:
            _驗證文字(項目, 1, 500)
        return 規劃器輸出(建立正規JSON(值))
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        raise 規劃器輸出無效("規劃器輸出無效") from None

def _驗證文字(值: Any, 最小: int, 最大UTF8: int) -> None:
    """驗證去除空白後不變的文字及 UTF-8 位元組界限。

    參數：``值`` 是候選文字；``最小`` 是最少字元數；``最大UTF8`` 是最大 UTF-8 位元組數。
    回傳：無回傳值。
    例外：型別、長度、首尾空白或位元組界限不符時拋出 ``ValueError``。
    副作用：無外部副作用，也不修改輸入。
    """
    if type(值) is not str or len(值) < 最小 or 值 != 值.strip() or len(值.encode("utf-8")) > 最大UTF8:
        raise ValueError

def _驗證名稱陣列(值: Any, 最小: int, 最大: int) -> list[str]:
    """驗證唯一、嚴格遞增且符合格式的名稱陣列。

    參數：``值`` 是候選陣列；``最小`` 與 ``最大`` 是允許的項目數界限。
    回傳：驗證後的原始串列物件。
    例外：型別、數量、名稱格式、唯一性或排序不符時拋出 ``ValueError``。
    副作用：無外部副作用，也不修改串列。
    """
    if type(值) is not list or not 最小 <= len(值) <= 最大:
        raise ValueError
    前一 = None
    for 名稱 in 值:
        if type(名稱) is not str or 識別規則.fullmatch(名稱) is None or (前一 is not None and 名稱 <= 前一):
            raise ValueError
        前一 = 名稱
    return 值

def _驗證結構(值: Any, *, 允許空: bool) -> None:
    """驗證 JSON Schema 結構與正規化後的大小。

    參數：``值`` 是候選結構；``允許空`` 指示是否接受 ``None``。
    回傳：無回傳值。
    例外：型別、JSON Schema 或 32,768 位元組界限不符時拋出 ``ValueError`` 或結構驗證例外。
    副作用：無外部副作用，也不修改輸入。
    """
    if 值 is None and 允許空:
        return
    if type(值) is not dict:
        raise ValueError
    Draft202012Validator.check_schema(值)
    if len(建立正規JSON(值).encode("utf-8")) > 32_768:
        raise ValueError

__all__ = ["規劃器輸入", "規劃器輸出", "規劃器轉接器", "規劃器輸出無效", "規劃器不可用", "文字回應結構", "驗證選擇", "建立規劃器輸入", "驗證規劃器輸出"]
