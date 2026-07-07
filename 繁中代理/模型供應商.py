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

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


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

    def 轉成Gemini內容(self, 訊息清單: list[dict[str, Any]]) -> list[Any]:
        """把 canonical messages 轉成 Gemini Content 清單。

        參數：
            訊息清單: OpenAI-compatible messages。

        返回值：
            google.genai.types.Content 清單。
        """
        from google.genai import types

        內容清單: list[Any] = []
        # 連續的 tool 回應要合併成「單一個」user Content，函數回應數才會等於前一個
        # model turn 的 function_call 數（平行工具呼叫時 Gemini 硬性要求）。
        待沖函數回應: list[Any] = []

        def 沖出函數回應() -> None:
            if 待沖函數回應:
                內容清單.append(types.Content(role="user", parts=待沖函數回應[:]))
                待沖函數回應.clear()

        for 訊息 in 訊息清單:
            角色 = 訊息.get("role")
            if 角色 == "tool":
                名稱 = 訊息.get("name") or "tool"
                try:
                    工具結果 = json.loads(str(訊息.get("content", "{}")))
                except Exception:
                    工具結果 = {"content": str(訊息.get("content", ""))}
                待沖函數回應.append(types.Part(function_response=types.FunctionResponse(name=名稱, response=工具結果)))
                continue
            # 非 tool 訊息：先把累積的函數回應沖成一個 Content，再處理本則
            沖出函數回應()
            if 角色 == "system":
                內容清單.append(types.Content(role="user", parts=[types.Part(text=f"[System]\n{訊息.get('content', '')}")]))
            elif 角色 == "user":
                內容清單.append(types.Content(role="user", parts=[types.Part(text=str(訊息.get("content", "")))]))
            elif 角色 == "assistant":
                if 訊息.get("tool_calls"):
                    零件清單 = []
                    for 工具呼叫 in 訊息.get("tool_calls", []):
                        函數 = 工具呼叫.get("function", {})
                        try:
                            參數 = json.loads(函數.get("arguments") or "{}")
                        except Exception:
                            參數 = {}
                        零件清單.append(types.Part(function_call=types.FunctionCall(name=函數.get("name", ""), args=參數)))
                    內容清單.append(types.Content(role="model", parts=零件清單))
                else:
                    內容清單.append(types.Content(role="model", parts=[types.Part(text=str(訊息.get("content", "")))]))
        沖出函數回應()  # 收尾：歷史以 tool 回應結束時也要沖出
        return 內容清單

    def 轉成Gemini工具(self, 工具清單: list[dict[str, Any]]) -> list[Any]:
        """把 OpenAI tool schema 轉成 Gemini function declarations。

        參數：
            工具清單: OpenAI-compatible tool schema。

        返回值：
            Gemini Tool 清單。
        """
        if not 工具清單:
            return []
        from google.genai import types

        可用欄位 = set(types.Schema.model_fields.keys())
        JSON_SCHEMA鍵對照 = {
            "additionalProperties": "additional_properties",
            "anyOf": "any_of",
            "maxItems": "max_items",
            "minItems": "min_items",
            "maxLength": "max_length",
            "minLength": "min_length",
            "maxProperties": "max_properties",
            "minProperties": "min_properties",
            "propertyOrdering": "property_ordering",
            "$ref": "ref",
            "$defs": "defs",
        }

        def 正規化Schema鍵(鍵: str) -> str | None:
            if 鍵 in 可用欄位:
                return 鍵
            對照鍵 = JSON_SCHEMA鍵對照.get(鍵)
            if 對照鍵 in 可用欄位:
                return 對照鍵
            return None

        def 清理Gemini結構(結構: Any) -> Any:
            """把 OpenAI JSON Schema 收斂成 google-genai Schema 支援的欄位。"""
            if isinstance(結構, list):
                return [清理Gemini結構(項目) for 項目 in 結構]
            if not isinstance(結構, dict):
                return 結構
            清理後: dict[str, Any] = {}
            for 鍵, 值 in 結構.items():
                正規鍵 = 正規化Schema鍵(鍵)
                if 正規鍵 is None:
                    continue
                if 正規鍵 == "properties" and isinstance(值, dict):
                    清理後[正規鍵] = {str(子鍵): 清理Gemini結構(子值) for 子鍵, 子值 in 值.items()}
                elif 正規鍵 == "additional_properties" and isinstance(值, dict):
                    清理後[正規鍵] = 清理Gemini結構(值)
                elif 正規鍵 in {"items", "any_of"}:
                    清理後[正規鍵] = 清理Gemini結構(值)
                else:
                    清理後[正規鍵] = 值
            return 清理後

        函數宣告清單 = []
        for 工具 in 工具清單:
            函數 = 工具.get("function", {})
            參數結構 = 函數.get("parameters") or {"type": "object", "properties": {}}
            if "parameters_json_schema" in types.FunctionDeclaration.model_fields:
                函數宣告清單.append(types.FunctionDeclaration(
                    name=函數.get("name"),
                    description=函數.get("description"),
                    parameters_json_schema=參數結構,
                ))
            else:
                函數宣告清單.append(types.FunctionDeclaration(
                    name=函數.get("name"),
                    description=函數.get("description"),
                    parameters=types.Schema.model_validate(清理Gemini結構(參數結構)),
                ))
        return [types.Tool(function_declarations=函數宣告清單)]

    def 轉成模型回應(self, 回應: Any) -> 模型回應:
        """把 Gemini 回應轉成模型回應。

        參數：
            回應: google-genai response 物件。

        返回值：
            模型回應。
        """
        文字片段清單: list[str] = []
        工具呼叫清單: list[dict[str, Any]] = []
        候選清單 = getattr(回應, "candidates", None) or []
        完成原因 = "stop"
        if 候選清單:
            完成原因 = str(getattr(候選清單[0], "finish_reason", "stop"))
            內容 = getattr(候選清單[0], "content", None)
            for 零件 in (getattr(內容, "parts", None) or []):
                函數呼叫 = getattr(零件, "function_call", None)
                if 函數呼叫 and getattr(函數呼叫, "name", None):
                    工具呼叫清單.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": 函數呼叫.name,
                            "arguments": json.dumps(dict(getattr(函數呼叫, "args", {}) or {}), ensure_ascii=False),
                        },
                    })
                文字 = getattr(零件, "text", None)
                if 文字:
                    文字片段清單.append(文字)
        else:
            文字片段清單.append(getattr(回應, "text", "") or "")
        使用量物件 = getattr(回應, "usage_metadata", None)
        使用量 = {}
        if 使用量物件:
            使用量 = {名稱: getattr(使用量物件, 名稱) for 名稱 in ["prompt_token_count", "candidates_token_count", "total_token_count"] if hasattr(使用量物件, 名稱)}
        return 模型回應(文字="".join(文字片段清單), 工具呼叫清單=工具呼叫清單, 完成原因=完成原因, 使用量=使用量)


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
