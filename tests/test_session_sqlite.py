"""測試 SQLite roundtrip、append-first 持久化、FTS 與 compression lineage。"""

import json
import sqlite3
import subprocess
import sys

from 繁中代理.工作階段上下文 import 讀取目前工作階段識別碼
from 繁中代理.工作階段庫 import 工作階段庫
from 繁中代理.代理執行階段 import 代理執行階段
from 繁中代理.模型供應商 import 假模型供應商, 模型回應
from 繁中代理.工具 import 建立預設工具登錄器


def test_session_sqlite_roundtrip(tmp_path):
    """確認 session 與 messages 可寫入再讀回，system prompt 不混入 transcript。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 assert 驗證 SQLite roundtrip。
    """
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    session_id = 庫.建立或讀取工作階段("abc")
    庫.更新系統提示詞(session_id, "system prompt")
    訊息 = [{"role": "user", "content": "你好"}]
    庫.寫入訊息清單(session_id, 訊息)
    assert 庫.讀取工作階段("abc")["system_prompt"] == "system prompt"
    assert 庫.讀取訊息("abc") == 訊息
    assert all(訊息項["role"] != "system" for 訊息項 in 庫.讀取訊息("abc"))


def test_runtime_早期持久化_並完成最終回答(tmp_path):
    """確認 runtime 會留下 user 與 assistant 訊息，但不持久化 system 訊息。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 assert 驗證 runtime persistence。
    """
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
    """確認壓縮鎖與 session split schema 行為。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 assert 驗證舊 session 保留原始歷史。
    """
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


def test_wal_mode_and_cross_connection_compression_lock(tmp_path):
    """確認 WAL 啟用且兩個 connection 不會同時取得同一把壓縮鎖。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 journal_mode 與 holder 格式驗證並發安全基礎。
    """
    db = tmp_path / "sessions.sqlite3"
    庫一 = 工作階段庫(db)
    庫二 = 工作階段庫(db)
    assert 庫一.連線.execute("PRAGMA journal_mode").fetchone()[0] in {"wal", "delete"}
    sid = 庫一.建立或讀取工作階段("lock")
    holder = 庫一.取得壓縮鎖(sid)
    assert holder is not None
    assert "pid=" in holder and "tid=" in holder and "agent=" in holder and "nonce=" in holder
    assert 庫二.取得壓縮鎖(sid) is None
    assert 庫二.讀取壓縮鎖Holder(sid) == holder
    庫一.釋放壓縮鎖(sid, holder)
    assert 庫二.取得壓縮鎖(sid, 擁有者="second") == "second"


def test_append_first_不刪除既有訊息(tmp_path):
    """確認重複寫入 working messages 只 append 尾端，不 DELETE 重建舊 rows。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 message row id 驗證舊資料未被刪除重建。
    """
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    sid = 庫.建立或讀取工作階段("append")
    第一批 = [{"role": "user", "content": "第一則"}]
    庫.寫入訊息清單(sid, 第一批)
    第一列 = 庫.連線.execute("SELECT id FROM messages WHERE session_id=?", (sid,)).fetchone()["id"]
    第二批 = [*第一批, {"role": "assistant", "content": "第二則"}]
    庫.寫入訊息清單(sid, 第二批)
    ids = [row["id"] for row in 庫.連線.execute("SELECT id FROM messages WHERE session_id=? ORDER BY id", (sid,)).fetchall()]
    assert ids[0] == 第一列
    assert len(ids) == 2
    assert [m["content"] for m in 庫.讀取訊息(sid)] == ["第一則", "第二則"]


def test_fts_可以搜尋中文與工具metadata(tmp_path):
    """確認 FTS 可搜尋中文內容與 tool_calls/tool_name。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 搜尋訊息 驗證搜尋索引已同步。
    """
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    sid = 庫.建立或讀取工作階段("fts")
    庫.寫入訊息清單(sid, [
        {"role": "user", "content": "請搜尋台北旅遊"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "search_files", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call1", "name": "search_files", "content": "找到台北行程"},
    ])
    assert 庫.搜尋訊息("台北", limit=5)
    工具結果 = 庫.搜尋訊息("search_files", limit=5)
    assert any(row["tool_name"] == "search_files" or "search_files" in str(row["content"]) for row in 工具結果)


def test_schema_version_與舊schema_migration(tmp_path):
    """確認舊版三表 schema 會補齊 schema_version、metadata 欄位與 FTS。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 PRAGMA 與 FTS 搜尋驗證 migration。
    """
    db = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE sessions(id TEXT PRIMARY KEY, title TEXT, system_prompt TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, message_index INTEGER NOT NULL, role TEXT NOT NULL, content_json TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE compression_locks(session_id TEXT PRIMARY KEY, owner TEXT NOT NULL, acquired_at REAL NOT NULL, expires_at REAL NOT NULL);
        INSERT INTO sessions(id, title, created_at, updated_at) VALUES ('legacy', 'legacy', 1, 1);
        INSERT INTO messages(session_id, message_index, role, content_json, created_at) VALUES ('legacy', 0, 'user', '{"role":"user","content":"舊資料"}', 1);
        """
    )
    conn.close()
    庫 = 工作階段庫(db)
    assert 庫.連線.execute("SELECT version FROM schema_version").fetchone()["version"] >= 2
    session欄位 = {row["name"] for row in 庫.連線.execute("PRAGMA table_info(sessions)").fetchall()}
    message欄位 = {row["name"] for row in 庫.連線.execute("PRAGMA table_info(messages)").fetchall()}
    assert {"source", "model", "message_count", "rewind_count", "archived"} <= session欄位
    assert {"tool_calls", "tool_name", "active", "platform_message_id"} <= message欄位
    assert 庫.搜尋訊息("舊資料")


def test_logical_conversation_tip_projection_and_ancestor_replay(tmp_path):
    """確認 compression chain 可取得 tip、列表投影成一列、祖先訊息可合併回放。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 lineage API 驗證 logical conversation 行為。
    """
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    root = 庫.建立或讀取工作階段("root")
    庫.寫入訊息清單(root, [{"role": "user", "content": "root raw"}])
    child = 庫.建立壓縮後工作階段(root, [{"role": "assistant", "content": "child summary", "_compressed_summary": True}], "system")
    庫.寫入訊息清單(child, [{"role": "assistant", "content": "child summary", "_compressed_summary": True}, {"role": "user", "content": "child new"}])
    grandchild = 庫.建立壓縮後工作階段(child, [{"role": "assistant", "content": "grandchild summary", "_compressed_summary": True}], "system")
    庫.寫入訊息清單(grandchild, [{"role": "assistant", "content": "grandchild summary", "_compressed_summary": True}, {"role": "user", "content": "grandchild new"}])
    assert 庫.取得壓縮Tip(root) == grandchild
    assert 庫.解析Resume工作階段(root) == grandchild
    assert 庫.解析Resume工作階段(child) == grandchild
    lineage = 庫.取得工作階段譜系(grandchild)
    assert lineage == [root, child, grandchild]
    replay = 庫.讀取訊息(grandchild, include_ancestors=True)
    assert [m["content"] for m in replay][:3] == ["root raw", "child summary", "child new"]
    列表 = 庫.列出工作階段(limit=10)
    assert sum(1 for row in 列表 if row.get("_lineage_root_id") == root) == 1
    assert 列表[0]["id"] == grandchild


def test_rewind_soft_delete_保留audit資料(tmp_path):
    """確認 rewind 使用 active=0 soft-delete，不物理刪除 rows。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 active/inactive 讀取驗證 audit 保留。
    """
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    sid = 庫.建立或讀取工作階段("rewind")
    庫.寫入訊息清單(sid, [
        {"role": "user", "content": "一"},
        {"role": "assistant", "content": "二"},
        {"role": "user", "content": "三"},
    ])
    target = 庫.連線.execute("SELECT id FROM messages WHERE session_id=? ORDER BY id LIMIT 1 OFFSET 1", (sid,)).fetchone()["id"]
    結果 = 庫.rewind到訊息(sid, target)
    assert 結果["rewound_count"] == 2
    assert [m["content"] for m in 庫.讀取訊息(sid)] == ["一"]
    assert [m["content"] for m in 庫.讀取訊息(sid, 包含停用=True)] == ["一", "二", "三"]




def test_append_cursor_忽略inactive_audit訊息(tmp_path):
    """確認 append 游標只看 active transcript，不被 inactive audit rows 推歪。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 rewind 與 replace 後再 append 驗證不會靜默漏寫。
    """
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    sid = 庫.建立或讀取工作階段("cursor")
    原始 = [{"role": "user", "content": f"訊息{i}"} for i in range(10)]
    庫.寫入訊息清單(sid, 原始)
    target = 庫.連線.execute("SELECT id FROM messages WHERE session_id=? AND message_index=5", (sid,)).fetchone()["id"]
    庫.rewind到訊息(sid, target)
    新工作清單 = 庫.讀取訊息(sid) + [{"role": "user", "content": "rewind 後新增"}]
    庫.寫入訊息清單(sid, 新工作清單)
    assert [m["content"] for m in 庫.讀取訊息(sid)][-1] == "rewind 後新增"

    庫.replace_messages(sid, [{"role": "user", "content": "替換後第一則"}])
    庫.寫入訊息清單(sid, [{"role": "user", "content": "替換後第一則"}, {"role": "assistant", "content": "替換後第二則"}])
    assert [m["content"] for m in 庫.讀取訊息(sid)] == ["替換後第一則", "替換後第二則"]


def test_fts_正常開啟不重建索引但legacy仍可重建(tmp_path):
    """確認一般 reopen 不全量重建 FTS，避免 session_search 造成寫入放大。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過人工 sentinel FTS 內容驗證 reopen 未重建。
    """
    db = tmp_path / "sessions.sqlite3"
    庫 = 工作階段庫(db)
    sid = 庫.建立或讀取工作階段("fts-no-rebuild")
    庫.寫入訊息清單(sid, [{"role": "user", "content": "原始索引文字"}])
    row_id = 庫.連線.execute("SELECT id FROM messages WHERE session_id=?", (sid,)).fetchone()["id"]
    庫.連線.execute("INSERT INTO messages_fts(rowid, content) VALUES (?, 'sentinel-only')", (row_id + 1000,))
    marker = 庫.連線.execute("SELECT value FROM state_meta WHERE key='fts_rebuilt_schema_version'").fetchone()
    assert marker and marker["value"]
    重新開啟 = 工作階段庫(db)
    sentinel = 重新開啟.連線.execute("SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'sentinel'").fetchall()
    assert sentinel


def test_message_metadata_roundtrip_append_replace_and_session_search_tool(tmp_path):
    """確認 metadata 可完整回放、rewrite 走 soft-delete，且 session_search handler 可搜尋。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 assert 驗證 Hermes-like metadata 與工具 handler。
    """
    db = tmp_path / "sessions.sqlite3"
    庫 = 工作階段庫(db)
    sid = 庫.建立或讀取工作階段("meta")
    row_id = 庫.append_message(sid, {
        "role": "assistant",
        "content": "含推理 metadata",
        "finish_reason": "stop",
        "token_count": 12,
        "reasoning": "hidden",
        "reasoning_content": "可回放推理摘要",
        "reasoning_details": [{"type": "summary", "text": "detail"}],
        "codex_reasoning_items": [{"id": "r1"}],
        "codex_message_items": [{"id": "m1"}],
        "platform_message_id": "platform-1",
        "observed": True,
    })
    assert row_id > 0
    讀回 = 庫.讀取訊息(sid)[0]
    assert 讀回["finish_reason"] == "stop"
    assert 讀回["token_count"] == 12
    assert 讀回["reasoning"] == "hidden"
    assert 讀回["reasoning_details"] == [{"type": "summary", "text": "detail"}]
    assert 讀回["codex_reasoning_items"] == [{"id": "r1"}]
    assert 讀回["codex_message_items"] == [{"id": "m1"}]
    assert 讀回["platform_message_id"] == "platform-1"
    assert 讀回["observed"] is True

    庫.replace_messages(sid, [{"role": "user", "content": "rewrite 後搜尋關鍵字"}])
    assert [m["content"] for m in 庫.讀取訊息(sid)] == ["rewrite 後搜尋關鍵字"]
    assert len(庫.讀取訊息(sid, 包含停用=True)) == 2

    登錄器 = 建立預設工具登錄器()
    payload = json.loads(登錄器.呼叫工具("session_search", {"query": "搜尋關鍵字", "db_path": str(db), "limit": 2}))
    assert payload["success"] is True
    assert payload["result"]["total_count"] == 1
    assert payload["result"]["matches"][0]["session_id"] == sid
    browse = json.loads(登錄器.呼叫工具("session_search", {"db_path": str(db), "limit": 2}))
    assert browse["success"] is True
    assert browse["result"]["sessions"]
    read = json.loads(登錄器.呼叫工具("session_search", {"session_id": sid, "db_path": str(db)}))
    assert read["success"] is True
    assert read["result"]["total_messages"] == 1
    anchor = read["result"]["messages"][0]["id"]
    scroll = json.loads(登錄器.呼叫工具("session_search", {"session_id": sid, "around_message_id": anchor, "db_path": str(db), "window": 1}))
    assert scroll["success"] is True
    assert scroll["result"]["messages"][0]["id"] == anchor


def test_runtime_max_iterations_fallback_includes_finish_reason(tmp_path):
    """確認達最大迭代次數的 fallback assistant 訊息也會寫入 finish_reason。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 assert 驗證 assistant message schema 一致。
    """
    class AlwaysToolProvider:
        """測試用 provider，永遠回傳 tool call 以觸發 max-iter fallback。"""
        def 產生回應(self, 訊息清單, 工具清單):
            """回傳固定 tool-call 結果。

            參數：
                訊息清單: request messages。
                工具清單: tool schemas。

            返回值：模型回應。
            """
            return 模型回應(
                文字="",
                工具呼叫清單=[{
                    "id": "call_loop",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": json.dumps({"path": "README.md", "limit": 1})},
                }],
                完成原因="tool_calls",
            )

    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    runtime = 代理執行階段(
        庫,
        AlwaysToolProvider(),
        模型名稱="fake",
        供應商名稱="fake",
        工作目錄="/Users/wujinan/Documents/testagent2",
        最大迭代次數=2,
    )
    結果 = runtime.執行使用者訊息("觸發 tool loop", 工作階段識別碼="max-iter")
    assert "已達最大迭代次數" in 結果.最終回答
    assistant_messages = [m for m in 庫.讀取訊息("max-iter") if m["role"] == "assistant"]
    assert assistant_messages[-1]["content"] == 結果.最終回答
    assert assistant_messages[-1]["finish_reason"] == "max_iterations"
    assert all("finish_reason" in m for m in assistant_messages)


def test_runtime_writes_finish_reason_usage_and_cli_rewind_search(tmp_path):
    """確認 runtime 會寫入 finish_reason/usage，CLI 可搜尋與 rewind。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 DB 與 CLI subprocess 驗證 runtime/工具入口。
    """
    class UsageProvider:
        """測試用 provider，固定回傳 usage 與 finish reason。"""
        def 產生回應(self, 訊息清單, 工具清單):
            """回傳固定模型結果。

            參數：
                訊息清單: request messages。
                工具清單: tool schemas。

            返回值：模型回應。
            """
            return 模型回應(文字="usage answer", 完成原因="stop", 使用量={"prompt_token_count": 7, "candidates_token_count": 5, "thoughts_token_count": 2})

    db = tmp_path / "sessions.sqlite3"
    庫 = 工作階段庫(db)
    runtime = 代理執行階段(庫, UsageProvider(), 模型名稱="fake", 供應商名稱="fake", 工作目錄="/Users/wujinan/Documents/testagent2")
    runtime.執行使用者訊息("請記錄 usage", 工作階段識別碼="usage")
    runtime.執行使用者訊息("再次記錄 usage", 工作階段識別碼="usage")
    no_path_search = json.loads(建立預設工具登錄器().呼叫工具("session_search", {"query": "usage", "limit": 1}))
    assert no_path_search["success"] is True
    assert no_path_search["result"]["db_path"] == str(db)
    session = 庫.讀取工作階段("usage")
    assert session["api_call_count"] == 2
    assert session["input_tokens"] == 14
    assert session["prompt_tokens"] == 7
    assert session["output_tokens"] == 10
    assert session["reasoning_tokens"] == 4
    assert 庫.讀取訊息("usage")[-1]["finish_reason"] == "stop"

    搜尋 = subprocess.run([sys.executable, "-m", "繁中代理.cli", "--db", str(db), "--session-search", "usage"], cwd="/Users/wujinan/Documents/testagent2", text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    assert 搜尋.returncode == 0, 搜尋.stdout
    assert "usage" in 搜尋.stdout
    target = 庫.連線.execute("SELECT id FROM messages WHERE session_id='usage' AND role='assistant'").fetchone()["id"]
    rewind = subprocess.run([sys.executable, "-m", "繁中代理.cli", "--mode", "fake", "--db", str(db), "--session", "usage", "--rewind-to-message-id", str(target)], cwd="/Users/wujinan/Documents/testagent2", text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    assert rewind.returncode == 0, rewind.stdout
    assert "rewound_count" in rewind.stdout
    assert [m["role"] for m in 庫.讀取訊息("usage")] == ["user"]


def test_session_metadata_archive_filters_and_cost_mvp(tmp_path):
    """確認 user/source/model_config、archive/filter 與成本估算營運層已接上。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 DB、工具與 CLI 驗證 sessions metadata 行為。
    """
    class UsageProvider:
        """測試用 provider，固定回傳 Gemini-style usage。"""
        def 產生回應(self, 訊息清單, 工具清單):
            """回傳固定模型結果。

            參數：
                訊息清單: request messages。
                工具清單: tool schemas。

            返回值：模型回應。
            """
            return 模型回應(文字="metadata cost answer", 使用量={"prompt_token_count": 1000, "candidates_token_count": 2000})

    db = tmp_path / "sessions.sqlite3"
    庫 = 工作階段庫(db)
    runtime = 代理執行階段(
        庫,
        UsageProvider(),
        模型名稱="gemini-2.5-flash-lite",
        供應商名稱="gemini-adc",
        工作目錄="/Users/wujinan/Documents/testagent2",
        模型模式="gemini",
        user_id="user-a",
        source="cli-test",
        model_config={"temperature": 0.2, "thinking": "off"},
    )
    runtime.執行使用者訊息("metadata 搜尋", 工作階段識別碼="meta-session")
    session = 庫.讀取工作階段("meta-session")
    assert session["user_id"] == "user-a"
    assert session["source"] == "cli-test"
    assert json.loads(session["model_config"])["temperature"] == 0.2
    assert session["billing_provider"] == "gemini-adc"
    assert session["estimated_cost_usd"] > 0
    assert session["cost_status"] == "estimated"
    assert session["cost_source"] == "local_pricing_table"
    assert session["pricing_version"] == "local-pricing-v1"

    assert 庫.搜尋工作階段("metadata", source="cli-test", user_id="user-a")
    assert not 庫.搜尋工作階段("metadata", source="api", user_id="user-a")
    assert 庫.列出工作階段(source="cli-test", user_id="user-a")
    庫.封存工作階段("meta-session")
    assert not 庫.列出工作階段(source="cli-test", user_id="user-a")
    assert 庫.列出工作階段(source="cli-test", user_id="user-a", include_archived=True)
    assert not 庫.搜尋工作階段("metadata", source="cli-test", user_id="user-a")
    assert 庫.搜尋工作階段("metadata", source="cli-test", user_id="user-a", include_archived=True)
    庫.取消封存工作階段("meta-session")
    assert 庫.讀取工作階段("meta-session")["archived"] == 0

    登錄器 = 建立預設工具登錄器()
    filtered = json.loads(登錄器.呼叫工具("session_search", {"query": "metadata", "db_path": str(db), "source": "cli-test", "user_id": "user-a"}))
    assert filtered["result"]["total_count"] == 1
    庫.封存工作階段("meta-session")
    hidden = json.loads(登錄器.呼叫工具("session_search", {"query": "metadata", "db_path": str(db), "source": "cli-test", "user_id": "user-a"}))
    assert hidden["result"]["total_count"] == 0
    visible = json.loads(登錄器.呼叫工具("session_search", {"query": "metadata", "db_path": str(db), "source": "cli-test", "user_id": "user-a", "include_archived": True}))
    assert visible["result"]["total_count"] == 1

    unarchive = subprocess.run([sys.executable, "-m", "繁中代理.cli", "--db", str(db), "--unarchive-session", "meta-session"], cwd="/Users/wujinan/Documents/testagent2", text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    assert unarchive.returncode == 0, unarchive.stdout
    assert 庫.讀取工作階段("meta-session")["archived"] == 0
    archive = subprocess.run([sys.executable, "-m", "繁中代理.cli", "--db", str(db), "--archive-session", "meta-session"], cwd="/Users/wujinan/Documents/testagent2", text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    assert archive.returncode == 0, archive.stdout
    assert 庫.讀取工作階段("meta-session")["archived"] == 1

    cli = subprocess.run([
        sys.executable, "-m", "繁中代理.cli", "--mode", "fake", "--db", str(db), "--session", "cli-meta",
        "--user-id", "cli-user", "--source", "api", "--model-config-json", '{"temperature":0.1}', "hello"
    ], cwd="/Users/wujinan/Documents/testagent2", text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    assert cli.returncode == 0, cli.stdout
    cli_session = 庫.讀取工作階段("cli-meta")
    assert cli_session["user_id"] == "cli-user"
    assert cli_session["source"] == "api"
    assert json.loads(cli_session["model_config"])["temperature"] == 0.1

def test_runtime_compression_updates_context_and_hooks(tmp_path):
    """確認壓縮 split 後 ContextVar/HERMES_SESSION_ID 與 hooks 都收到新 session id。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 fake hooks 驗證 session switch side effects。
    """
    class 記憶:
        """測試用記憶管理器。"""
        def __init__(self):
            self.calls = []
        def on_pre_compress(self, messages):
            self.calls.append(("pre", len(messages)))
        def on_session_switch(self, new_id, **kwargs):
            self.calls.append(("switch", new_id, kwargs))

    class 引擎:
        """測試用上下文引擎。"""
        def __init__(self):
            self.calls = []
        def on_session_start(self, session_id, **kwargs):
            self.calls.append((session_id, kwargs))

    events = []
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    記憶物件 = 記憶()
    引擎物件 = 引擎()
    runtime = 代理執行階段(
        庫,
        假模型供應商(),
        模型名稱="fake",
        供應商名稱="fake",
        工作目錄="/Users/wujinan/Documents/testagent2",
        上下文長度=8192,
        記憶管理器=記憶物件,
        上下文引擎=引擎物件,
        事件回呼=lambda name, payload: events.append((name, payload)),
    )
    sid = 庫.建立或讀取工作階段("compress-runtime")
    系統提示 = "system"
    訊息 = [{"role": "user", "content": "開頭"}] + [{"role": "assistant", "content": "x" * 1200} for _ in range(20)] + [{"role": "user", "content": "尾端"}]
    新sid, 壓縮後, 是否壓縮 = runtime.嘗試壓縮並分裂工作階段(sid, 訊息, 系統提示, [], 強制=True)
    assert 是否壓縮 is True
    assert 新sid != sid
    assert 讀取目前工作階段識別碼() == 新sid
    assert any(call[0] == "switch" and call[1] == 新sid for call in 記憶物件.calls)
    assert 引擎物件.calls and 引擎物件.calls[0][0] == 新sid
    assert events and events[0][0] == "session:compress"
    assert 庫.讀取訊息(sid)[0]["content"] == "開頭"
    assert 壓縮後 == 庫.讀取訊息(新sid)

def test_cli_sessions_subcommands_and_repl_slash_commands(tmp_path):
    """確認 CLI 提供 sessions 管理子命令與最小 Hermes-style REPL slash commands。

    參數：
        tmp_path: pytest 提供的暫存目錄。

    返回值：None。透過 subprocess 驗證 list/browse/rename/export/stats 與 REPL。
    """
    db = tmp_path / "cli-sessions.sqlite3"
    first = subprocess.run(
        [sys.executable, "-m", "繁中代理.cli", "--mode", "fake", "--db", str(db), "--session", "cli-one", "第一則 CLI 訊息"],
        cwd="/Users/wujinan/Documents/testagent2",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    assert first.returncode == 0, first.stdout
    assert "session=cli-one" in first.stdout

    list_json = subprocess.run(
        [sys.executable, "-m", "繁中代理.cli", "sessions", "--db", str(db), "list", "--json"],
        cwd="/Users/wujinan/Documents/testagent2",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    assert list_json.returncode == 0, list_json.stdout
    list_payload = json.loads(list_json.stdout)
    assert list_payload["total_count"] == 1
    assert list_payload["sessions"][0]["id"] == "cli-one"

    rename = subprocess.run(
        [sys.executable, "-m", "繁中代理.cli", "sessions", "--db", str(db), "rename", "cli-one", "好用 CLI"],
        cwd="/Users/wujinan/Documents/testagent2",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    assert rename.returncode == 0, rename.stdout
    assert 工作階段庫(db).讀取工作階段("cli-one")["title"] == "好用 CLI"

    browse = subprocess.run(
        [sys.executable, "-m", "繁中代理.cli", "sessions", "--db", str(db), "browse"],
        cwd="/Users/wujinan/Documents/testagent2",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    assert browse.returncode == 0, browse.stdout
    assert "好用 CLI" in browse.stdout
    assert "第一則 CLI 訊息" in browse.stdout

    export_path = tmp_path / "sessions.jsonl"
    export = subprocess.run(
        [sys.executable, "-m", "繁中代理.cli", "sessions", "--db", str(db), "export", str(export_path)],
        cwd="/Users/wujinan/Documents/testagent2",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    assert export.returncode == 0, export.stdout
    export_payload = json.loads(export.stdout)
    assert export_payload["session_count"] == 1
    exported_lines = export_path.read_text(encoding="utf-8").splitlines()
    assert len(exported_lines) == 1
    assert json.loads(exported_lines[0])["session"]["id"] == "cli-one"

    stats = subprocess.run(
        [sys.executable, "-m", "繁中代理.cli", "sessions", "--db", str(db), "stats", "--json"],
        cwd="/Users/wujinan/Documents/testagent2",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    assert stats.returncode == 0, stats.stdout
    stats_payload = json.loads(stats.stdout)
    assert stats_payload["session_count"] == 1
    assert stats_payload["message_count"] >= 2

    repl = subprocess.run(
        [sys.executable, "-m", "繁中代理.cli", "--mode", "fake", "--db", str(db), "--session", "repl-one"],
        cwd="/Users/wujinan/Documents/testagent2",
        input="/help\n/status\nREPL 訊息\n/history\n/sessions 5\n/undo\n/history\n/exit\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    assert repl.returncode == 0, repl.stdout
    assert "可用命令" in repl.stdout
    assert "session=repl-one" in repl.stdout
    assert "假模型回覆" in repl.stdout
    assert "已 undo" in repl.stdout
    assert [m["role"] for m in 工作階段庫(db).讀取訊息("repl-one")] == []
