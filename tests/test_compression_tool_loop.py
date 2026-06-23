"""測試 context compression 與 tool-call loop。"""

from 繁中代理.上下文壓縮器 import 上下文壓縮器, 粗估訊息Token數
from 繁中代理.工作階段庫 import 工作階段庫
from 繁中代理.代理執行階段 import 代理執行階段
from 繁中代理.模型供應商 import 假模型供應商


def test_context_compression_trigger():
    """確認超過 50% context window 後保留頭尾並插入摘要。"""
    壓縮器 = 上下文壓縮器(上下文長度=8192, 觸發比例=0.5, 保留開頭數=1, 保留尾端數=2)
    訊息清單 = [{"role": "user", "content": "開頭"}]
    for i in range(20):
        訊息清單.append({"role": "assistant", "content": "中段" + str(i) + "x" * 1200})
    訊息清單.append({"role": "user", "content": "最新問題"})
    結果 = 壓縮器.壓縮訊息(訊息清單, 系統提示詞="s" * 1000)
    assert 結果.是否已壓縮 is True
    assert 結果.訊息清單[0]["content"] == "開頭"
    assert 結果.訊息清單[1].get("_compressed_summary") is True
    assert "REFERENCE ONLY" in 結果.訊息清單[1]["content"]
    assert 結果.訊息清單[-1]["content"] == "最新問題"
    assert 結果.壓縮後Token數 < 結果.壓縮前Token數


def test_tool_call_loop_roundtrip(tmp_path):
    """確認 fake model 會觸發真實 read_file 工具並回到模型產生最終回答。"""
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    runtime = 代理執行階段(庫, 假模型供應商(), 模型名稱="fake", 供應商名稱="fake", 工作目錄="/Users/wujinan/Documents/testagent2")
    結果 = runtime.執行使用者訊息("請讀取 README 並回答", 工作階段識別碼="tool-loop")
    assert 結果.工具呼叫次數 == 1
    assert 結果.模型呼叫次數 == 2
    assert "工具 roundtrip" in 結果.最終回答
    assert any(訊息.get("role") == "tool" and "# testagent2" in 訊息.get("content", "") for 訊息 in 結果.訊息清單)
