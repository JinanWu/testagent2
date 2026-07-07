"""測試 Gemini adapter 的轉換邊界。

真實 Gemini smoke test 由 CLI 指令執行；本檔避免在一般 pytest 中消耗雲端費用，
只驗證 OpenAI-compatible message 到 adapter 的基本轉換可被匯入。
"""

from 繁中代理.cli import 建立參數解析器, 建立執行階段
from 繁中代理.工作階段庫 import 工作階段庫
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


def test_gemini_adapter_轉換工具_schema_保留_json_schema_約束():
    """確認 camelCase JSON Schema 約束不會在 parameters_json_schema 路徑被誤刪。"""
    provider = GeminiADC供應商("gemini-2.5-flash-lite", "lab-cola-rd", "global")
    參數 = {
        "type": "object",
        "properties": {
            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5, "minItems": 1},
            "meta": {"type": "object", "additionalProperties": True},
        },
        "required": ["tags"],
    }
    tools = provider.轉成Gemini工具([{"type": "function", "function": {"name": "tag_tool", "description": "tag", "parameters": 參數}}])
    schema = tools[0].function_declarations[0].parameters_json_schema
    assert schema["properties"]["tags"]["maxItems"] == 5
    assert schema["properties"]["tags"]["minItems"] == 1
    assert schema["properties"]["meta"]["additionalProperties"] is True


def test_cli_建立runtime前正規化模型名稱(tmp_path):
    """確認 CLI 模型別名會在 session/runtime 層先正規化。"""
    解析器 = 建立參數解析器()
    參數 = 解析器.parse_args(["--mode", "gemini", "--model", "gemini-flash-lite", "hello"])
    runtime = 建立執行階段(參數, 工作階段庫(tmp_path / "sessions.sqlite3"), 解析器)
    assert runtime.模型名稱 == "gemini-2.5-flash-lite"
    assert runtime.model_config["requested_model"] == "gemini-flash-lite"
    assert runtime.model_config["resolved_model"] == "gemini-2.5-flash-lite"


def test_gemini_adapter_平行工具呼叫的函數回應合併為單一Content():
    """平行 tool_calls：多個 tool 回應要合併成單一 user Content，函數回應數需等於
    前一個 model turn 的 function_call 數(Gemini 硬性要求,否則回 400)。"""
    provider = GeminiADC供應商("gemini-2.5-flash-lite", "lab-cola-rd", "global")
    訊息清單 = [
        {"role": "user", "content": "建立技能"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "skill_manage", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "clarify", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "name": "skill_manage", "content": '{"success": true}'},
        {"role": "tool", "tool_call_id": "c2", "name": "clarify", "content": '{"success": true}'},
    ]
    contents = provider.轉成Gemini內容(訊息清單)
    # 找到 model turn 的 function_call 數,與其後 user turn 的 function_response 數
    model_fc = next(sum(1 for p in c.parts if getattr(p, "function_call", None)) for c in contents if c.role == "model")
    fr_contents = [c for c in contents if any(getattr(p, "function_response", None) for p in c.parts)]
    assert model_fc == 2
    assert len(fr_contents) == 1, "兩個函數回應應合併成單一 Content,而非兩個"
    assert sum(1 for p in fr_contents[0].parts if getattr(p, "function_response", None)) == 2
