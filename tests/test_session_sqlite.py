"""測試 SQLite roundtrip 與 runtime 持久化。"""

from 繁中代理.工作階段庫 import 工作階段庫
from 繁中代理.代理執行階段 import 代理執行階段
from 繁中代理.模型供應商 import 假模型供應商


def test_session_sqlite_roundtrip(tmp_path):
    """確認 session 與 messages 可寫入再讀回。"""
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    session_id = 庫.建立或讀取工作階段("abc")
    庫.更新系統提示詞(session_id, "system prompt")
    訊息 = [{"role": "system", "content": "system prompt"}, {"role": "user", "content": "你好"}]
    庫.寫入訊息清單(session_id, 訊息)
    assert 庫.讀取工作階段("abc")["system_prompt"] == "system prompt"
    assert 庫.讀取訊息("abc") == 訊息


def test_runtime_早期持久化_並完成最終回答(tmp_path):
    """確認 runtime 會留下 user 與 assistant 訊息。"""
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    runtime = 代理執行階段(庫, 假模型供應商(), 模型名稱="fake", 供應商名稱="fake", 工作目錄="/Users/wujinan/Documents/testagent2")
    結果 = runtime.執行使用者訊息("你好", 工作階段識別碼="s")
    讀回訊息 = 庫.讀取訊息("s")
    assert 結果.最終回答
    assert any(訊息["role"] == "user" and 訊息["content"] == "你好" for 訊息 in 讀回訊息)
    assert 讀回訊息[-1]["role"] == "assistant"
