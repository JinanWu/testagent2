"""測試 Gemini tool-call 名稱正規化。"""

import json
from pathlib import Path

from 繁中代理.代理執行階段 import 代理執行階段
from 繁中代理.工作階段庫 import 工作階段庫
from 繁中代理.模型供應商 import 模型回應
專案根目錄 = Path(__file__).resolve().parents[1]


class 前綴工具名稱模型:
    """先回傳 Gemini 常見的 default_api 前綴工具名，再回傳最終答案。

    參數：無。
    返回值：測試用 provider。
    """

    def __init__(self):
        """初始化呼叫計數。

        參數：無。
        返回值：None。
        """
        self.呼叫次數 = 0

    def 產生回應(self, 訊息清單, 工具清單):
        """依呼叫次數產生 tool call 或 final answer。

        參數：
            訊息清單: canonical messages。
            工具清單: tool schemas。

        返回值：
            模型回應。
        """
        self.呼叫次數 += 1
        if self.呼叫次數 == 1:
            return 模型回應(工具呼叫清單=[{
                "id": "call_prefixed",
                "type": "function",
                "function": {"name": "default_api.read_file", "arguments": json.dumps({"path": str(專案根目錄 / "README.md"), "limit": 1})},
            }])
        return 模型回應(文字="完成")


def test_runtime_接受_gemini_default_api_工具前綴(tmp_path):
    """確認 runtime 會把 default_api.read_file 正規化為 read_file。"""
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    runtime = 代理執行階段(庫, 前綴工具名稱模型(), 模型名稱="fake", 供應商名稱="fake", 工作目錄=str(專案根目錄))
    結果 = runtime.執行使用者訊息("請讀檔", 工作階段識別碼="prefixed")
    assert 結果.工具呼叫次數 == 1
    assert any(訊息.get("role") == "tool" and 訊息.get("name") == "read_file" and "# testagent2" in 訊息.get("content", "") for 訊息 in 結果.訊息清單)
