"""測試 context compression 與 tool-call loop。"""

from 繁中代理.上下文壓縮器 import 上下文壓縮器, 粗估訊息Token數
from 繁中代理.工作階段庫 import 工作階段庫
from 繁中代理.代理執行階段 import 代理執行階段
from 繁中代理.模型供應商 import 假模型供應商, 模型回應


def test_context_compression_threshold_floor():
    """確認 threshold 是 max(context_length * ratio, minimum floor)。"""
    壓縮器 = 上下文壓縮器(上下文長度=10000, 觸發比例=0.5)
    assert 壓縮器.門檻Token數 == 8192
    assert 壓縮器.是否需要壓縮(5000) is False
    assert 壓縮器.是否需要壓縮(8192) is True


def test_context_compression_trigger():
    """確認超過門檻後保留頭尾並插入 reference-only 摘要。"""
    壓縮器 = 上下文壓縮器(上下文長度=8192, 觸發比例=0.5, 保留開頭數=1, 保留尾端數=2)
    訊息清單 = [{"role": "user", "content": "開頭"}]
    for i in range(30):
        訊息清單.append({"role": "assistant", "content": "中段" + str(i) + "x" * 1200})
    訊息清單.append({"role": "user", "content": "最新問題"})
    結果 = 壓縮器.壓縮訊息(訊息清單, 系統提示詞="s" * 1000)
    assert 結果.是否已壓縮 is True
    assert 結果.訊息清單[0]["content"] == "開頭"
    assert any(訊息.get("_compressed_summary") is True for 訊息 in 結果.訊息清單)
    assert "REFERENCE ONLY" in "\n".join(str(訊息.get("content", "")) for 訊息 in 結果.訊息清單)
    assert 結果.訊息清單[-1]["content"] == "最新問題"
    assert 結果.壓縮後Token數 < 結果.壓縮前Token數


def test_provider_usage_drives_compression_even_when_rough_estimate_low():
    """確認 post-response usage 使用 provider prompt token，不再只看 rough estimate。"""
    壓縮器 = 上下文壓縮器(上下文長度=10000, 觸發比例=0.5, 保留開頭數=1, 保留尾端數=2)
    訊息清單 = [{"role": "user", "content": f"訊息 {i}"} for i in range(12)]
    assert 粗估訊息Token數(訊息清單) < 壓縮器.門檻Token數
    結果 = 壓縮器.壓縮訊息(訊息清單, provider提示Token數=9000)
    assert 結果.是否已壓縮 is True


def test_tool_result_pruning_and_tool_pair_sanitize():
    """確認舊大型 tool result 會被修剪，orphan tool result 會被移除。"""
    壓縮器 = 上下文壓縮器(上下文長度=8192, 保留開頭數=1, 保留尾端數=2)
    訊息清單 = [
        {"role": "user", "content": "開始"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_old", "type": "function", "function": {"name": "terminal", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_old", "name": "terminal", "content": "x" * 5000},
        {"role": "tool", "tool_call_id": "orphan", "name": "read_file", "content": "孤兒結果"},
        *({"role": "user", "content": "中段" + str(i) + "y" * 1000} for i in range(20)),
        {"role": "user", "content": "最新"},
    ]
    結果 = 壓縮器.壓縮訊息(訊息清單, 強制=True)
    文字 = "\n".join(str(訊息.get("content", "")) for 訊息 in 結果.訊息清單)
    assert "Pruned old terminal tool result" in 文字 or "terminal" in 文字
    assert all(訊息.get("tool_call_id") != "orphan" for 訊息 in 結果.訊息清單)


def test_iterative_summary_update_uses_existing_summary():
    """確認再次壓縮會納入既有 summary，而非把 prefix 重複堆疊。"""
    壓縮器 = 上下文壓縮器(上下文長度=8192, 保留開頭數=1, 保留尾端數=2)
    訊息清單 = [{"role": "user", "content": "開頭"}] + [{"role": "assistant", "content": "第一輪" + "x" * 1000} for _ in range(20)] + [{"role": "user", "content": "尾端"}]
    第一次 = 壓縮器.壓縮訊息(訊息清單, 強制=True)
    第二次訊息 = [*第一次.訊息清單, *({"role": "assistant", "content": "第二輪" + "y" * 1000} for _ in range(16)), {"role": "user", "content": "新問題"}]
    第二次 = 壓縮器.壓縮訊息(第二次訊息, 強制=True)
    摘要文字 = "\n".join(str(訊息.get("content", "")) for 訊息 in 第二次.訊息清單 if 訊息.get("_compressed_summary"))
    assert 摘要文字.count("[CONTEXT COMPACTION") == 1
    assert "Previous summary retained" in 摘要文字 or "第一輪" in 摘要文字


def test_tool_call_loop_roundtrip(tmp_path):
    """確認 fake model 會觸發真實 read_file 工具並回到模型產生最終回答。"""
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    runtime = 代理執行階段(庫, 假模型供應商(), 模型名稱="fake", 供應商名稱="fake", 工作目錄="/Users/wujinan/Documents/testagent2")
    結果 = runtime.執行使用者訊息("請讀取 README 並回答", 工作階段識別碼="tool-loop")
    assert 結果.工具呼叫次數 == 1
    assert 結果.模型呼叫次數 == 2
    assert "工具 roundtrip" in 結果.最終回答
    assert any(訊息.get("role") == "tool" and "# testagent2" in 訊息.get("content", "") for 訊息 in 結果.訊息清單)


class 使用量假模型供應商:
    """回傳高 provider usage 以觸發 post-response compression split。"""

    def 產生回應(self, 訊息清單, 工具清單):
        """回傳含 prompt_token_count 的最終回答。"""
        return 模型回應(文字="完成", 使用量={"prompt_token_count": 9000})


def test_runtime_provider_usage_creates_compression_split(tmp_path):
    """確認 runtime 使用 provider usage 觸發壓縮並建立新 session。"""
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    舊id = 庫.建立或讀取工作階段("usage-s")
    歷史 = [{"role": "user", "content": f"歷史 {i}"} for i in range(36)]
    庫.寫入訊息清單(舊id, 歷史)
    runtime = 代理執行階段(庫, 使用量假模型供應商(), 模型名稱="fake", 供應商名稱="fake", 工作目錄="/Users/wujinan/Documents/testagent2", 上下文長度=10000)
    結果 = runtime.執行使用者訊息("最新", 工作階段識別碼=舊id)
    assert 結果.是否已壓縮 is True
    assert 結果.工作階段識別碼 != 舊id
    assert 庫.讀取工作階段(舊id)["end_reason"] == "compression"
    目前 = 庫.讀取工作階段(結果.工作階段識別碼)
    父鏈 = []
    while 目前 and 目前.get("parent_session_id"):
        父鏈.append(目前["parent_session_id"])
        目前 = 庫.讀取工作階段(目前["parent_session_id"])
    assert 舊id in 父鏈
