"""模型 provider adapter。

功能：
    保持 runtime 內部使用 OpenAI-compatible canonical messages，並在 provider
    邊界轉換成實際模型 SDK 需要的格式。MVP 提供兩個 adapter：
    1. 假模型 adapter：供單元測試與 tool-loop smoke test 使用。
    2. Gemini ADC adapter：使用 gcloud ADC/Vertex AI Gemini 呼叫真實模型。

注意：
    Gemini ADC 是本專案的企業環境替代方案；Hermes 內建 Gemini provider 主要
    使用 Google AI Studio API key 或 Cloud Code Assist/OAuth 路徑。本專案仍保留
    Hermes-style canonical message 與 provider adapter 邊界。
"""

from __future__ import annotations

import base64
import json
import math
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# Gemini 3 系列 function call 附帶的 thought signature 在 tool_calls dict 內的欄位名。
思考簽章欄位 = "thought_signature"


@dataclass
class 模型回應:
    """統一 provider 回應格式。

    參數：
        文字: assistant 文字內容。
        工具呼叫清單: OpenAI-compatible tool_calls。
        完成原因: provider finish reason。
        使用量: token usage 或空 dict。

    返回值：
        dataclass 實例。
    """

    文字: str = ""
    工具呼叫清單: list[dict[str, Any]] = field(default_factory=list)
    完成原因: str = "stop"
    使用量: dict[str, Any] = field(default_factory=dict)


class 模型供應商(Protocol):
    """定義 provider adapter 必須實作的介面。

    參數：無。
    返回值：Protocol 型別。
    """

    def 產生回應(self, 訊息清單: list[dict[str, Any]], 工具清單: list[dict[str, Any]]) -> 模型回應:
        """根據 messages 與 tools 產生模型回應。

        參數：
            訊息清單: OpenAI-compatible messages。
            工具清單: OpenAI-compatible tool schema。

        返回值：
            模型回應。
        """
        ...


class 假模型供應商:
    """可預測的測試用 provider。

    參數：無。
    返回值：provider 實例。
    """

    def __init__(self) -> None:
        """初始化 fake provider 狀態。

        參數：無。
        返回值：None。
        """
        self.呼叫次數 = 0

    def 產生回應(self, 訊息清單: list[dict[str, Any]], 工具清單: list[dict[str, Any]]) -> 模型回應:
        """產生 deterministic 回應。

        參數：
            訊息清單: canonical messages。
            工具清單: tool schema。

        返回值：
            若使用者要求讀取 README，第一次回傳 read_file tool call；收到 tool
            result 後回傳最終答案。
        """
        self.呼叫次數 += 1
        if 訊息清單 and 訊息清單[-1].get("role") == "tool":
            return 模型回應(文字=f"已完成工具 roundtrip；工具結果摘要：{str(訊息清單[-1].get('content', ''))[:200]}")
        最後使用者 = "\n".join(str(訊息.get("content", "")) for 訊息 in 訊息清單 if 訊息.get("role") == "user")
        if "讀取" in 最後使用者 or "README" in 最後使用者 or "tool" in 最後使用者.lower():
            呼叫識別碼 = f"call_{uuid.uuid4().hex[:8]}"
            return 模型回應(
                文字="",
                工具呼叫清單=[{
                    "id": 呼叫識別碼,
                    "type": "function",
                    "function": {"name": "read_file", "arguments": json.dumps({"path": "README.md", "limit": 20}, ensure_ascii=False)},
                }],
                完成原因="tool_calls",
            )
        return 模型回應(文字="假模型回覆：我可以運作。")


def 編碼思考簽章(簽章: Any) -> str | None:
    """把 provider 回傳的 thought signature 轉成可放進 JSON 的字串。

    Gemini 3 系列在回傳 function call 時會附帶一段不透明的 thought signature，
    下一輪把對話送回去時必須原樣帶上，否則 Vertex 會以
    `function call ... is missing a thought_signature` 拒絕整個請求。簽章是
    bytes，而 tool_calls 是以 JSON 保存的，故一律 base64 編碼後再存。
    Gemini 2.5 系列不產生簽章，此時回傳 None、行為與過去完全相同。

    參數：
        簽章: provider part 上的 thought_signature，通常是 bytes。
    返回值：base64 字串；沒有簽章或型別非預期時回傳 None。
    """
    if not isinstance(簽章, (bytes, bytearray)):
        return None
    return base64.b64encode(bytes(簽章)).decode("ascii")


def 解碼思考簽章(簽章文字: Any) -> bytes | None:
    """把保存的 base64 簽章還原成送回 provider 所需的 bytes。

    參數：
        簽章文字: 先前由 `編碼思考簽章` 產生的字串。
    返回值：bytes；缺少或無法解碼時回傳 None，讓請求退回無簽章行為。
    """
    if not isinstance(簽章文字, str) or not 簽章文字:
        return None
    try:
        return base64.b64decode(簽章文字, validate=True)
    except (ValueError, TypeError):
        return None


def 正規化Gemini模型名稱(模型名稱: str) -> str:
    """把口語模型別名轉成 Vertex AI 可用模型 ID。

    參數：
        模型名稱: 使用者或環境變數提供的模型名稱。

    返回值：
        Vertex AI Gemini publisher model id。
    """
    別名表 = {
        "gemini-flash-lite": "gemini-2.5-flash-lite",
        "flash-lite": "gemini-2.5-flash-lite",
    }
    return 別名表.get(模型名稱, 模型名稱)


Gemini模型上下文長度表: dict[str, int] = {
    "gemini-2.5-flash-lite": 1_048_576,
    "gemini-2.5-flash": 1_048_576,
    "gemini-2.5-pro": 1_048_576,
    "gemini-2.0-flash": 1_048_576,
    "gemini-2.0-flash-lite": 1_048_576,
    "gemini-1.5-flash": 1_048_576,
    "gemini-1.5-flash-8b": 1_048_576,
    "gemini-1.5-flash-002": 1_048_576,
    "gemini-1.5-pro": 2_097_152,
    "gemini-1.5-pro-002": 2_097_152,
    "gemini-1.0-pro": 32_768,
    "gemini-1.0-pro-001": 32_768,
    "gemini-1.0-pro-002": 32_768,
    "gemini-pro": 32_768,
}
Gemini預設上下文長度 = 1_048_576


def 查詢Gemini上下文長度(模型名稱: str) -> int:
    """依 Gemini 模型 ID 查詢 context window 長度。

    參數：
        模型名稱: 使用者或環境變數提供的模型名稱，會先經別名正規化。

    返回值：
        int，context window token 數。
    """
    正式名稱 = 正規化Gemini模型名稱(模型名稱)
    if 正式名稱 in Gemini模型上下文長度表:
        return Gemini模型上下文長度表[正式名稱]
    底線名稱 = 正式名稱.replace(".", "_")
    if 底線名稱 in Gemini模型上下文長度表:
        return Gemini模型上下文長度表[底線名稱]
    小寫 = 正式名稱.lower()
    if "1.0" in 小寫 or 小寫 == "gemini-pro":
        return 32_768
    if "1.5-pro" in 小寫:
        return 2_097_152
    if any(標記 in 小寫 for 標記 in ("1.5", "2.0", "2.5")):
        return 1_048_576
    return Gemini預設上下文長度


class GeminiADC供應商:
    """使用 gcloud ADC/Vertex AI Gemini 的 provider adapter。

    參數：
        模型名稱: Gemini 模型名稱，例如 gemini-2.5-flash-lite。
        專案識別碼: GCP project id。
        位置: Vertex AI location，常見為 global、us-central1、asia-east1。

    返回值：
        provider 實例。
    """

    def __init__(self, 模型名稱: str, 專案識別碼: str, 位置: str = "global") -> None:
        """初始化 Gemini ADC adapter。

        參數：
            模型名稱: Gemini 模型名稱。
            專案識別碼: GCP project id。
            位置: Vertex AI location。

        返回值：None。
        """
        self.模型名稱 = 正規化Gemini模型名稱(模型名稱)
        self.專案識別碼 = 專案識別碼
        self.位置 = 位置

    def 產生回應(self, 訊息清單: list[dict[str, Any]], 工具清單: list[dict[str, Any]]) -> 模型回應:
        """呼叫 Gemini 並轉回 canonical 模型回應。

        參數：
            訊息清單: OpenAI-compatible messages。
            工具清單: OpenAI-compatible tool schema。

        返回值：
            模型回應，包含文字或 tool_calls。
        """
        from google import genai
        from google.genai import types

        客戶端 = genai.Client(vertexai=True, project=self.專案識別碼, location=self.位置)
        內容清單 = self.轉成Gemini內容(訊息清單)
        工具宣告清單 = self.轉成Gemini工具(工具清單)
        設定 = types.GenerateContentConfig(tools=工具宣告清單 if 工具宣告清單 else None)
        回應 = 客戶端.models.generate_content(model=self.模型名稱, contents=內容清單, config=設定)
        return self.轉成模型回應(回應)

    def 產生發布回應(
        self, *, model, temperature, max_tokens, timeout_seconds,
        structured_output, schema_retry_count, messages, tools, response_schema,
    ):
        """以釘選參數呼叫 Vertex Gemini，且不讀取 instance 的 live model。"""
        from google import genai
        from google.genai import types
        import httpx
        from .發布介面.執行期.模型契約 import (
            供應商逾時, 控制流程, 模型回應快照, 複製JSON,
        )

        專案 = 位置 = 訊息原值 = 工具原值 = 結構原值 = None
        訊息 = 工具 = 結構 = 內容 = 工具宣告 = 設定參數 = 設定 = 客戶端 = 回應 = 舊回應 = None
        文字 = 原因 = 使用量原值 = 呼叫原值 = 使用量 = 呼叫 = 結果 = 錯誤 = None
        毫秒 = None
        逾時 = False
        try:
            專案 = object.__getattribute__(self, "專案識別碼")
            位置 = object.__getattribute__(self, "位置")
            訊息原值 = messages
            工具原值 = tools
            結構原值 = response_schema
            if type(model) is not str or not model or len(model) > 128:
                raise ValueError
            if type(temperature) is not float or not math.isfinite(temperature) or not 0 <= temperature <= 2:
                raise ValueError
            if type(max_tokens) is not int or not 1 <= max_tokens <= 1_000_000:
                raise ValueError
            if type(timeout_seconds) is not float or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 900:
                raise ValueError
            if type(structured_output) is not bool or type(schema_retry_count) is not int or schema_retry_count != 1:
                raise ValueError
            if type(專案) is not str or type(位置) is not str or not 專案 or not 位置:
                raise ValueError
            訊息 = 複製JSON(訊息原值, 1_000_000)
            工具 = 複製JSON(工具原值, 1_000_000)
            if type(訊息) is not list or type(工具) is not list:
                raise ValueError
            if structured_output:
                if type(結構原值) is not dict:
                    raise ValueError
                結構 = 複製JSON(結構原值, 500_000)
            elif 結構原值 is not None:
                raise ValueError
            內容 = self.轉成Gemini內容(訊息)
            工具宣告 = self.轉成Gemini工具(工具)
            設定參數 = {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
                "tools": 工具宣告 if 工具宣告 else None,
            }
            if structured_output:
                設定參數["response_mime_type"] = "application/json"
                設定參數["response_json_schema"] = 結構
            設定 = types.GenerateContentConfig(**設定參數)
            毫秒 = math.ceil(timeout_seconds * 1000)
            if not 1 <= 毫秒 <= 900_000:
                raise ValueError
            客戶端 = genai.Client(
                vertexai=True, project=專案, location=位置,
                http_options=types.HttpOptions(timeout=毫秒),
            )
            try:
                回應 = 客戶端.models.generate_content(model=model, contents=內容, config=設定)
            except (TimeoutError, httpx.TimeoutException) as 錯誤:
                if type(錯誤) is TimeoutError or isinstance(錯誤, httpx.TimeoutException):
                    逾時 = True
                else:
                    raise
            if 逾時:
                self = model = temperature = max_tokens = timeout_seconds = structured_output = schema_retry_count = None
                messages = tools = response_schema = 專案 = 位置 = 訊息原值 = 工具原值 = 結構原值 = None
                訊息 = 工具 = 結構 = 內容 = 工具宣告 = 設定參數 = 設定 = 客戶端 = 回應 = 舊回應 = None
                文字 = 原因 = 使用量原值 = 呼叫原值 = 使用量 = 呼叫 = 結果 = 錯誤 = None
                毫秒 = None
                raise 供應商逾時("Gemini 供應商逾時") from None
            舊回應 = self.轉成模型回應(回應)
            文字 = object.__getattribute__(舊回應, "文字")
            呼叫原值 = object.__getattribute__(舊回應, "工具呼叫清單")
            原因 = object.__getattribute__(舊回應, "完成原因")
            使用量原值 = object.__getattribute__(舊回應, "使用量")
            if type(文字) is not str or type(原因) is not str:
                raise ValueError
            使用量 = 複製JSON(使用量原值, 500_000)
            呼叫 = 複製JSON(呼叫原值, 1_000_000)
            if type(使用量) is not dict or type(呼叫) is not list:
                raise ValueError
            結果 = 模型回應快照(文字, 原因, 使用量, 呼叫)
            return 結果
        except 控制流程 as 錯誤:
            self = model = temperature = max_tokens = timeout_seconds = structured_output = schema_retry_count = None
            messages = tools = response_schema = 專案 = 位置 = 訊息原值 = 工具原值 = 結構原值 = None
            訊息 = 工具 = 結構 = 內容 = 工具宣告 = 設定參數 = 設定 = 客戶端 = 回應 = 舊回應 = None
            文字 = 原因 = 使用量原值 = 呼叫原值 = 使用量 = 呼叫 = 結果 = 錯誤 = None
            毫秒 = None
            raise
        except BaseException:
            self = model = temperature = max_tokens = timeout_seconds = structured_output = schema_retry_count = None
            messages = tools = response_schema = 專案 = 位置 = 訊息原值 = 工具原值 = 結構原值 = None
            訊息 = 工具 = 結構 = 內容 = 工具宣告 = 設定參數 = 設定 = 客戶端 = 回應 = 舊回應 = None
            文字 = 原因 = 使用量原值 = 呼叫原值 = 使用量 = 呼叫 = 結果 = 錯誤 = None
            毫秒 = None
            raise
        raise AssertionError

    def 轉成Gemini內容(self, 訊息清單: list[dict[str, Any]]) -> list[Any]:
        """把 canonical messages 轉成 Gemini Content 清單。

        參數：
            訊息清單: OpenAI-compatible messages。

        返回值：
            google.genai.types.Content 清單。
        """
        from google.genai import types

        內容清單 = 待沖函數回應 = 訊息 = 角色 = 名稱 = 工具結果 = None
        零件 = 零件清單 = 工具呼叫們 = 工具呼叫 = 函數 = 參數 = 函數回應 = 函數呼叫 = None
        try:
            內容清單 = []
            待沖函數回應 = []
            for 訊息 in 訊息清單:
                角色 = 訊息.get("role")
                if 角色 == "tool":
                    名稱 = 訊息.get("name") or "tool"
                    try:
                        工具結果 = json.loads(str(訊息.get("content", "{}")))
                    except Exception:
                        工具結果 = {"content": str(訊息.get("content", ""))}
                    函數回應 = types.FunctionResponse(name=名稱, response=工具結果)
                    零件 = types.Part(function_response=函數回應)
                    待沖函數回應.append(零件)
                    continue
                if 待沖函數回應:
                    零件清單 = list(待沖函數回應)
                    內容清單.append(types.Content(role="user", parts=零件清單))
                    待沖函數回應.clear()
                if 角色 in ("system", "user"):
                    名稱 = f"[System]\n{訊息.get('content', '')}" if 角色 == "system" else str(訊息.get("content", ""))
                    零件 = types.Part(text=名稱)
                    內容清單.append(types.Content(role="user", parts=[零件]))
                elif 角色 == "assistant":
                    工具呼叫們 = 訊息.get("tool_calls")
                    if 工具呼叫們:
                        零件清單 = []
                        for 工具呼叫 in 工具呼叫們:
                            函數 = 工具呼叫.get("function", {})
                            try:
                                參數 = json.loads(函數.get("arguments") or "{}")
                            except Exception:
                                參數 = {}
                            函數呼叫 = types.FunctionCall(name=函數.get("name", ""), args=參數)
                            簽章 = 解碼思考簽章(工具呼叫.get(思考簽章欄位))
                            if 簽章 is None:
                                零件 = types.Part(function_call=函數呼叫)
                            else:
                                零件 = types.Part(function_call=函數呼叫, thought_signature=簽章)
                            零件清單.append(零件)
                        內容清單.append(types.Content(role="model", parts=零件清單))
                    else:
                        零件 = types.Part(text=str(訊息.get("content", "")))
                        內容清單.append(types.Content(role="model", parts=[零件]))
            if 待沖函數回應:
                零件清單 = list(待沖函數回應)
                內容清單.append(types.Content(role="user", parts=零件清單))
                待沖函數回應.clear()
            return 內容清單
        except BaseException:
            self = 訊息清單 = types = 內容清單 = 待沖函數回應 = 訊息 = 角色 = 名稱 = 工具結果 = None
            零件 = 零件清單 = 工具呼叫們 = 工具呼叫 = 函數 = 參數 = 函數回應 = 函數呼叫 = None
            raise

    def 轉成Gemini工具(self, 工具清單: list[dict[str, Any]]) -> list[Any]:
        """把 OpenAI tool schema 轉成 Gemini function declarations。

        參數：
            工具清單: OpenAI-compatible tool schema。

        返回值：
            Gemini Tool 清單。
        """
        from google.genai import types

        可用欄位 = 鍵對照 = 函數宣告清單 = 工具 = 函數 = 參數結構 = 清理結構 = 宣告 = 結果 = None

        def 正規化Schema鍵(鍵: str) -> str | None:
            對照鍵 = 結果鍵 = None
            try:
                if 鍵 in 可用欄位:
                    結果鍵 = 鍵
                else:
                    對照鍵 = 鍵對照.get(鍵)
                    if 對照鍵 in 可用欄位:
                        結果鍵 = 對照鍵
                return 結果鍵
            except BaseException:
                鍵 = 對照鍵 = 結果鍵 = None
                raise

        def 清理Gemini結構(結構: Any) -> Any:
            """把 OpenAI JSON Schema 收斂成 google-genai Schema 支援的欄位。"""
            清理後 = 項目 = 子結果 = 鍵 = 值 = 正規鍵 = 子鍵 = 子值 = None
            try:
                if isinstance(結構, list):
                    清理後 = []
                    for 項目 in 結構:
                        子結果 = 清理Gemini結構(項目)
                        清理後.append(子結果)
                    return 清理後
                if not isinstance(結構, dict):
                    return 結構
                清理後 = {}
                for 鍵, 值 in 結構.items():
                    正規鍵 = 正規化Schema鍵(鍵)
                    if 正規鍵 is None:
                        continue
                    if 正規鍵 == "properties" and isinstance(值, dict):
                        子結果 = {}
                        for 子鍵, 子值 in 值.items():
                            子結果[str(子鍵)] = 清理Gemini結構(子值)
                        清理後[正規鍵] = 子結果
                    elif 正規鍵 == "additional_properties" and isinstance(值, dict):
                        清理後[正規鍵] = 清理Gemini結構(值)
                    elif 正規鍵 in {"items", "any_of"}:
                        清理後[正規鍵] = 清理Gemini結構(值)
                    else:
                        清理後[正規鍵] = 值
                return 清理後
            except BaseException:
                結構 = 清理後 = 項目 = 子結果 = 鍵 = 值 = 正規鍵 = 子鍵 = 子值 = None
                raise

        try:
            if not 工具清單:
                return []
            可用欄位 = set(types.Schema.model_fields.keys())
            鍵對照 = {
                "additionalProperties": "additional_properties", "anyOf": "any_of",
                "maxItems": "max_items", "minItems": "min_items", "maxLength": "max_length",
                "minLength": "min_length", "maxProperties": "max_properties",
                "minProperties": "min_properties", "propertyOrdering": "property_ordering",
                "$ref": "ref", "$defs": "defs",
            }
            函數宣告清單 = []
            for 工具 in 工具清單:
                函數 = 工具.get("function", {})
                參數結構 = 函數.get("parameters") or {"type": "object", "properties": {}}
                if "parameters_json_schema" in types.FunctionDeclaration.model_fields:
                    宣告 = types.FunctionDeclaration(
                        name=函數.get("name"), description=函數.get("description"),
                        parameters_json_schema=參數結構,
                    )
                else:
                    清理結構 = 清理Gemini結構(參數結構)
                    宣告 = types.FunctionDeclaration(
                        name=函數.get("name"), description=函數.get("description"),
                        parameters=types.Schema.model_validate(清理結構),
                    )
                函數宣告清單.append(宣告)
            結果 = types.Tool(function_declarations=函數宣告清單)
            return [結果]
        except BaseException:
            self = 工具清單 = types = 可用欄位 = 鍵對照 = 函數宣告清單 = None
            工具 = 函數 = 參數結構 = 清理結構 = 宣告 = 結果 = None
            正規化Schema鍵 = 清理Gemini結構 = None
            raise

    def 轉成模型回應(self, 回應: Any) -> 模型回應:
        """把 Gemini 回應轉成模型回應。

        參數：
            回應: google-genai response 物件。

        返回值：
            模型回應。
        """
        文字片段清單 = 工具呼叫清單 = 候選清單 = 候選 = 內容 = 零件們 = 零件 = None
        函數呼叫 = 函數名稱 = 函數參數 = 呼叫資料 = 文字 = 使用量物件 = 使用量 = 名稱 = 欄位值 = 結果 = None
        完成原因 = "stop"
        try:
            文字片段清單 = []
            工具呼叫清單 = []
            候選清單 = getattr(回應, "candidates", None) or []
            if 候選清單:
                候選 = 候選清單[0]
                完成原因 = str(getattr(候選, "finish_reason", "stop"))
                內容 = getattr(候選, "content", None)
                零件們 = getattr(內容, "parts", None) or []
                for 零件 in 零件們:
                    函數呼叫 = getattr(零件, "function_call", None)
                    函數名稱 = getattr(函數呼叫, "name", None) if 函數呼叫 else None
                    if 函數名稱:
                        函數參數 = dict(getattr(函數呼叫, "args", {}) or {})
                        呼叫資料 = {
                            "id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                            "function": {"name": 函數名稱, "arguments": json.dumps(函數參數, ensure_ascii=False)},
                        }
                        簽章 = 編碼思考簽章(getattr(零件, "thought_signature", None))
                        if 簽章:
                            呼叫資料[思考簽章欄位] = 簽章
                        工具呼叫清單.append(呼叫資料)
                    文字 = getattr(零件, "text", None)
                    if 文字:
                        文字片段清單.append(文字)
            else:
                文字 = getattr(回應, "text", "") or ""
                文字片段清單.append(文字)
            使用量物件 = getattr(回應, "usage_metadata", None)
            使用量 = {}
            if 使用量物件:
                for 名稱 in ("prompt_token_count", "candidates_token_count", "total_token_count"):
                    try:
                        欄位值 = getattr(使用量物件, 名稱)
                    except AttributeError:
                        continue
                    使用量[名稱] = 欄位值
            結果 = 模型回應(
                文字="".join(文字片段清單), 工具呼叫清單=工具呼叫清單,
                完成原因=完成原因, 使用量=使用量,
            )
            return 結果
        except BaseException:
            self = 回應 = 文字片段清單 = 工具呼叫清單 = 候選清單 = 候選 = 內容 = 零件們 = 零件 = None
            函數呼叫 = 函數名稱 = 函數參數 = 呼叫資料 = 文字 = 使用量物件 = 使用量 = 名稱 = 欄位值 = 結果 = None
            完成原因 = None
            raise


def 建立模型供應商(模式: str, 模型名稱: str) -> 模型供應商:
    """依環境設定建立 provider。

    參數：
        模式: fake 或 gemini。
        模型名稱: 模型名稱。

    返回值：
        模型供應商實例。
    """
    if 模式 == "fake":
        return 假模型供應商()
    專案識別碼 = os.getenv("AIAGENT_GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT") or "lab-cola-rd"
    位置 = os.getenv("AIAGENT_GCP_LOCATION", "global")
    return GeminiADC供應商(模型名稱=模型名稱, 專案識別碼=專案識別碼, 位置=位置)
