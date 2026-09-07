"""提供決定性假規劃器與不可變 Gemini 規劃器轉接器。

公開範圍：提供兩種規劃器實作及最小 Gemini 結構化產生協定。
例外：供應商失敗可能轉為 ``規劃器不可用``；決定性實作可能傳遞資料處理例外。
副作用：匯入只建立類別，沒有網路或環境副作用；Gemini 實作於呼叫時才接觸外部供應商。
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

from ..嚴格JSON import 建立正規JSON, 解析嚴格JSON
from .規劃器契約 import 規劃器不可用, 規劃器輸入, 文字回應結構

class Gemini結構化產生器(Protocol):
    """規範由組合根注入的最小不可變 Gemini 邊界。

    方法：``產生JSON`` 接收系統指令與使用者內容，回傳 JSON 文字。
    例外：實作者可拋出供應商、網路或資料轉換例外。
    副作用：協定本身無副作用；實作者通常會發出外部供應商呼叫。
    """
    def 產生JSON(self, *, 系統指令: str, 使用者內容: str) -> str:
        """向 Gemini 產生結構化 JSON。

        參數：``系統指令`` 是固定規劃約束；``使用者內容`` 是正規化輸入文字。
        回傳：供應商產生的 JSON 文字。
        例外：可能拋出供應商、網路或資料轉換例外，控制流例外應原樣傳遞。
        副作用：通常會進行網路或其他外部供應商呼叫。
        """
        ...


def _正規化模型輸出(原始文字: str) -> str:
    """正規化可安全推導的slug與工具順序，其餘不合約內容仍交由驗證器拒絕。"""
    try:
        值 = 解析嚴格JSON(原始文字)
        if type(值) is not dict:
            return 原始文字
        短名 = 值.get("suggested_slug")
        if type(短名) is str:
            正規短名 = re.sub(r"[^a-z0-9]+", "-", 短名.lower()).strip("-")[:63].rstrip("-")
            if 正規短名:
                值["suggested_slug"] = 正規短名
        工具 = 值.get("recommended_tools")
        能力 = 值.get("tool_capabilities")
        if (
            type(工具) is not list
            or any(type(名稱) is not str for 名稱 in 工具)
            or len(工具) != len(set(工具))
            or type(能力) is not dict
            or set(能力) != set(工具)
        ):
            return 原始文字
        排序工具 = sorted(工具)
        值["recommended_tools"] = 排序工具
        值["tool_capabilities"] = {名稱: 能力[名稱] for 名稱 in 排序工具}
        return 建立正規JSON(值)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        return 原始文字

@dataclass(frozen=True, slots=True)
class 決定性假規劃器:
    """提供輸出只由輸入決定的測試轉接器。

    參數／欄位：無建構參數或資料欄位。
    回傳：建構後得到不可變轉接器；``產生`` 回傳固定契約的 JSON。
    例外：不主動轉換資料處理例外。
    副作用：不讀取環境、網路或全域工具，無外部副作用。
    """

    def 產生(self, 輸入: 規劃器輸入, /) -> str:
        """依輸入決定性地建立候選輸出。

        參數：``輸入`` 是已驗證的規劃器輸入。
        回傳：符合固定欄位契約的正規 JSON 文字。
        例外：輸入缺少技能或欄位失效時可能傳遞索引、屬性或序列化例外。
        副作用：無環境、網路或持久化副作用，也不修改輸入。
        """
        技能 = [項目.名稱 for 項目 in 輸入.技能]
        工具 = [項目.名稱 for 項目 in 輸入.授權工具[:1]]
        短名 = 技能[0].lower().replace("_", "-").replace(".", "-").replace(":", "-")[:63].strip("-") or "published-api"
        回應結構 = 文字回應結構 if 輸入.回應模式 == "text" else {
            "type": "object", "properties": {"result": {"type": "string"}},
            "required": ["result"], "additionalProperties": False,
        }
        return 建立正規JSON({
            "endpoint_name": f"{技能[0]} API", "suggested_slug": 短名,
            "behavior_summary": 輸入.原始需求, "selected_skills": 技能,
            "recommended_tools": 工具,
            "tool_capabilities": {名稱: f"執行 {名稱} 能力" for 名稱 in 工具},
            "system_prompt": f"依據技能 {', '.join(技能)} 回應需求；不得超出授權能力。",
            "input_schema": None, "response_schema": 回應結構,
            "human_docs": f"此端點處理：{輸入.原始需求}",
            "rate_limit": {"endpoint_per_minute": 60, "credential_per_minute": 30},
            "warnings": [],
        })

@dataclass(frozen=True, slots=True)
class Gemini規劃器:
    """透過 Gemini 產生候選規劃，且不保存可變對話狀態。

    欄位：``產生器`` 是由組合根注入的 Gemini 結構化產生器。
    回傳：建構後得到不可變轉接器；``產生`` 回傳供應商 JSON 文字。
    例外：供應商或結果型別失敗轉為 ``規劃器不可用``。
    副作用：建構無外部副作用；產生時會呼叫外部供應商，但不保存對話。
    """
    產生器: Gemini結構化產生器

    def 產生(self, 輸入: 規劃器輸入, /) -> str:
        """將授權規劃快照送至 Gemini 並回傳候選輸出。

        參數：``輸入`` 是只含需求、已選技能與授權工具的不可變快照。
        回傳：Gemini 產生的 JSON 文字。
        例外：供應商、型別或資料處理失敗轉為 ``規劃器不可用``；控制流例外原樣傳遞。
        副作用：呼叫注入的外部供應商；不修改輸入或保存對話狀態。
        """
        技能 = [{"name": 項目.名稱, "summary": 項目.摘要} for 項目 in 輸入.技能]
        工具 = [{"name": 項目.名稱, "revision": 項目.釘選修訂} for 項目 in 輸入.授權工具]
        內容 = 建立正規JSON({
            "original_requirement_text": 輸入.原始需求,
            "response_mode": 輸入.回應模式,
            "selected_skills": 技能,
            "authorized_tools": 工具,
        })
        try:
            結果 = self.產生器.產生JSON(
                系統指令=(
                    "你是 Published API Draft Planner。只輸出指定 exact JSON DTO；不得推薦 "
                    "authorized_tools 以外的工具。recommended_tools 必須去重並依工具名稱嚴格遞增排序，"
                    "tool_capabilities 的鍵順序必須與 recommended_tools 完全一致。"
                    "response_mode 為 text 時，response_schema 必須"
                    "精確等於 {\"type\":\"object\",\"properties\":{\"answer\":{\"type\":\"string\"}},"
                    "\"required\":[\"answer\"],\"additionalProperties\":false}；response_mode 為 "
                    "structured 時，response_schema.type 必須是 \"object\" 且必須是有效 JSON Schema。"
                ),
                使用者內容=內容,
            )
            if type(結果) is not str:
                raise ValueError
            return _正規化模型輸出(結果)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            raise 規劃器不可用("規劃器暫時不可用") from None

__all__ = ["決定性假規劃器", "Gemini規劃器", "Gemini結構化產生器"]
