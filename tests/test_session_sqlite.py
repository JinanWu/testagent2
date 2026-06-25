"""測試 SQLite roundtrip 與 runtime 持久化。"""

from 繁中代理.工作階段庫 import 工作階段庫
from 繁中代理.代理執行階段 import 代理執行階段
from 繁中代理.模型供應商 import 假模型供應商


def test_session_sqlite_roundtrip(tmp_path):
    """確認 session 與 messages 可寫入再讀回，system prompt 不混入 transcript。"""
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    session_id = 庫.建立或讀取工作階段("abc")
    庫.更新系統提示詞(session_id, "system prompt")
    訊息 = [{"role": "user", "content": "你好"}]
    庫.寫入訊息清單(session_id, 訊息)
    assert 庫.讀取工作階段("abc")["system_prompt"] == "system prompt"
    assert 庫.讀取訊息("abc") == 訊息
    assert all(訊息項["role"] != "system" for 訊息項 in 庫.讀取訊息("abc"))


def test_runtime_早期持久化_並完成最終回答(tmp_path):
    """確認 runtime 會留下 user 與 assistant 訊息，但不持久化 system 訊息。"""
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    runtime = 代理執行階段(庫, 假模型供應商(), 模型名稱="fake", 供應商名稱="fake", 工作目錄="/Users/wujinan/Documents/testagent2")
    結果 = runtime.執行使用者訊息("你好", 工作階段識別碼="s")
    讀回訊息 = 庫.讀取訊息("s")
    assert 結果.最終回答
    assert 庫.讀取工作階段("s")["system_prompt"]
    assert any(訊息["role"] == "user" and 訊息["content"] == "你好" for 訊息 in 讀回訊息)
    assert all(訊息["role"] != "system" for 訊息 in 讀回訊息)
    assert 讀回訊息[-1]["role"] == "assistant"


def test_compression_session_split_and_lock(tmp_path):
    """確認壓縮鎖與 session split schema 行為。"""
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    舊id = 庫.建立或讀取工作階段("old")
    庫.更新系統提示詞(舊id, "system")
    庫.寫入訊息清單(舊id, [{"role": "user", "content": "原始歷史"}])
    擁有者 = 庫.取得壓縮鎖(舊id, 擁有者="tester")
    assert 擁有者 == "tester"
    assert 庫.取得壓縮鎖(舊id, 擁有者="other") is None
    庫.釋放壓縮鎖(舊id, "tester")
    新id = 庫.建立壓縮後工作階段(舊id, [{"role": "assistant", "content": "摘要", "_compressed_summary": True}], "system")
    舊 = 庫.讀取工作階段(舊id)
    新 = 庫.讀取工作階段(新id)
    assert 舊 is not None and 新 is not None
    assert 舊["end_reason"] == "compression"
    assert 新["parent_session_id"] == 舊id
    assert 庫.讀取訊息(舊id)[0]["content"] == "原始歷史"
    assert 庫.讀取訊息(新id)[0]["_compressed_summary"] is True
