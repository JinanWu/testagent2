"""測試 Gemini adapter 的轉換邊界。

真實 Gemini smoke test 由 CLI 指令執行；本檔避免在一般 pytest 中消耗雲端費用，
只驗證 OpenAI-compatible message 到 adapter 的基本轉換可被匯入。
"""

import os

from 繁中代理.模型供應商 import GeminiADC供應商, 正規化Gemini模型名稱


def test_gemini_adapter_可建立並保留設定():
    """確認 Gemini ADC adapter 可建立並保存 project/location/model。"""
    provider = GeminiADC供應商("gemini-2.5-flash-lite", "lab-cola-rd", "global")
    assert provider.模型名稱 == "gemini-2.5-flash-lite"
    assert provider.專案識別碼 == "lab-cola-rd"
    assert provider.位置 == "global"


def test_gemini_adapter_模型別名_正規化為低成本模型():
    """確認使用者輸入 gemini-flash-lite 時會轉成 Vertex AI 可用 ID。"""
    assert 正規化Gemini模型名稱("gemini-flash-lite") == "gemini-2.5-flash-lite"
    provider = GeminiADC供應商("gemini-flash-lite", "trade-397602", "global")
    assert provider.模型名稱 == "gemini-2.5-flash-lite"


def test_gemini_adapter_轉換工具_schema():
    """確認 OpenAI tool schema 可轉為 Gemini Tool 物件。"""
    provider = GeminiADC供應商("gemini-2.5-flash-lite", "lab-cola-rd", "global")
    tools = provider.轉成Gemini工具([{"type": "function", "function": {"name": "read_file", "description": "read", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}])
    assert len(tools) == 1
    assert tools[0].function_declarations[0].name == "read_file"
