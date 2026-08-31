"""測試 Gemini adapter 的轉換邊界。

真實 Gemini smoke test 由 CLI 指令執行；本檔避免在一般 pytest 中消耗雲端費用，
只驗證 OpenAI-compatible message 到 adapter 的基本轉換可被匯入。
"""

from 繁中代理.cli import 建立參數解析器, 建立執行階段, 解析上下文長度
from 繁中代理.工作階段庫 import 工作階段庫
from 繁中代理.模型供應商 import GeminiADC供應商, 正規化Gemini模型名稱, 查詢Gemini上下文長度
from 繁中代理.使用者 import 建立預設使用者上下文


def _注入隔離使用者上下文(參數, tmp_path) -> None:
    """固定使用測試目錄的 local principal，不讀取開發者 Auth File 或登入 Token。"""
    參數._resolved_user_context = 建立預設使用者上下文(tmp_path)


def test_查詢Gemini上下文長度_依模型查表():
    """確認壓縮門檻會依實際模型 context window 查表，而非 gemini 模式一律 1M。"""
    assert 查詢Gemini上下文長度("gemini-3.7-flash") == 1_048_576
    assert 查詢Gemini上下文長度("gemini-2.5-flash-lite") == 1_048_576
    assert 查詢Gemini上下文長度("gemini-flash-lite") == 1_048_576
    assert 查詢Gemini上下文長度("gemini-1.0-pro") == 32_768
    assert 查詢Gemini上下文長度("gemini-1.5-pro") == 2_097_152


def test_解析上下文長度_依執行模型名稱(monkeypatch):
    """確認 CLI 層會把已正規化的模型名稱傳入上下文長度解析。"""
    monkeypatch.delenv("AIAGENT_CONTEXT_WINDOW", raising=False)
    assert 解析上下文長度("gemini", "gemini-1.0-pro") == 32_768
    assert 解析上下文長度("gemini", "gemini-2.5-flash-lite") == 1_048_576
    assert 解析上下文長度("fake", "anything") == 32_768


def test_cli_建立runtime_上下文長度依模型(tmp_path, monkeypatch):
    """確認建立 runtime 時壓縮器會帶入模型對應的 context window。"""
    monkeypatch.delenv("AIAGENT_CONTEXT_WINDOW", raising=False)
    解析器 = 建立參數解析器()
    參數 = 解析器.parse_args(["--mode", "gemini", "--model", "gemini-1.0-pro", "hello"])
    _注入隔離使用者上下文(參數, tmp_path)
    runtime = 建立執行階段(參數, 工作階段庫(tmp_path / "sessions.sqlite3"), 解析器)
    assert runtime.上下文壓縮器物件.上下文長度 == 32_768


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
    _注入隔離使用者上下文(參數, tmp_path)
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
