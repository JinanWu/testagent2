"""GOV G04 R51/R82 不可逆、原子 payload 墓碑測試。"""

import asyncio
import hashlib
import inspect
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.嚴格JSON import 計算正規JSON雜湊
from 繁中代理.發布介面.治理 import 遮蔽 as 遮蔽模組
from 繁中代理.發布介面.治理.遮蔽 import (
    SQLite不可逆遮蔽服務,
    不可逆遮蔽錯誤,
    遮蔽目標內容無效,
    遮蔽目標衝突,
)
from 繁中代理.發布介面.治理.遮蔽命令 import SQLite遮蔽命令服務, 遮蔽命令目標不存在
from 繁中代理.發布介面.治理.查詢投影 import SQLite呼叫查詢投影, 查詢投影錯誤
from 繁中代理.發布介面.治理.稽核 import SQLite稽核服務
from 繁中代理.發布介面.治理.查詢投影 import 管理員原始資料稽核閘門


目標 = {
    "invocation_input": ("endpoint_invocations", "input_json", "inv"),
    "metadata": ("endpoint_invocations", "metadata_json", "inv"),
    "output": ("endpoint_invocations", "output_json", "inv"),
    "error": ("endpoint_invocations", "error_json", "inv"),
    "run_event": ("run_events", "payload_json", "run"),
    "tool_arguments": ("endpoint_tool_calls", "arguments_json", "tool-ok"),
    "tool_result": ("endpoint_tool_calls", "result_json", "tool-ok"),
    "tool_error": ("endpoint_tool_calls", "error_json", "tool-error"),
}
原文 = '{"keep":1,"secret":{"value":"RAW_G04"}}'


@pytest.fixture
def 資料庫(tmp_path):
    path = tmp_path / "published.sqlite"
    初始化發布介面資料庫(path)
    with closing(sqlite3.connect(path)) as 連線, 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES('sa',0,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,"
            "created_at,updated_at) VALUES('ep','owner','sa','slug','active',0,0)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES("
            "'ver','ep',1,'r','s','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)"
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver' WHERE id='ep'")
        連線.execute(
            "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,"
            "input_json,metadata_json,output_json,error_json,created_at) "
            "VALUES('inv','ep','ver','request','succeeded',?,?,?, ?,0)", (原文,) * 4,
        )
        連線.execute("INSERT INTO run_events VALUES('run','inv',1,'event',?,0)", (原文,))
        連線.execute(
            "INSERT INTO endpoint_tool_calls(id,invocation_id,sequence_number,tool_name,"
            "arguments_json,outcome,result_json,created_at) "
            "VALUES('tool-ok','inv',1,'tool',?,'success',?,0)", (原文, 原文),
        )
        連線.execute(
            "INSERT INTO endpoint_tool_calls(id,invocation_id,sequence_number,tool_name,"
            "arguments_json,outcome,error_json,created_at) "
            "VALUES('tool-error','inv',2,'tool',?,'error',?,0)", (原文, 原文),
        )
    return path


def _參數(類型, 路徑, *, redaction="red-1", audit="audit-1", actor="admin-1",
        request="request-1", reason="privacy request", at=123.5):
    return (True, redaction, audit, actor, request, "inv", 類型, 目標[類型][2], 路徑, reason, at)


def _查詢(path, sql, params=()):
    with closing(sqlite3.connect(path)) as 連線:
        return 連線.execute(sql, params).fetchall()


@pytest.mark.parametrize("類型", tuple(目標))
@pytest.mark.parametrize("路徑", ("", "/secret/value"))
def test_每種目標整體與巢狀路徑皆留下不可逆canonical墓碑(資料庫, 類型, 路徑):
    表格, 欄位, 列ID = 目標[類型]
    receipt = SQLite不可逆遮蔽服務(str(資料庫)).redact(*_參數(類型, 路徑))
    payload = json.loads(_查詢(資料庫, f"SELECT {欄位} FROM {表格} WHERE id=?", (列ID,))[0][0])
    tombstone = {"$tombstone": {"redaction_id": "red-1", "redacted_at": 123.5}}
    assert payload == tombstone if 路徑 == "" else payload["secret"]["value"] == tombstone
    assert "RAW_G04" not in json.dumps(payload)
    assert "RAW_G04" not in repr(_查詢(資料庫, "SELECT * FROM audit_events"))
    assert "RAW_G04" not in repr(_查詢(資料庫, "SELECT * FROM endpoint_redactions"))
    source = 原文 if 路徑 == "" else '"RAW_G04"'
    assert receipt["original_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert receipt == {"redaction_id": "red-1", "target_type": 類型, "target_row_id": 列ID,
        "json_path": 路徑, "original_sha256": receipt["original_sha256"],
        "reason": "privacy request", "actor_id": "admin-1", "audit_event_id": "audit-1",
        "is_tombstone": True, "redacted_at": 123.5}
    assert _查詢(資料庫, "SELECT action,outcome,actor_type,actor_id,resource_type,resource_id,"
        "request_id,endpoint_id,invocation_id,metadata_json,occurred_at,created_at FROM audit_events") == [
        ("audit.payload.redact", "success", "user", "admin-1", "endpoint.redaction", "red-1",
         "request-1", "ep", "inv", '{"is_tombstone":true}', 123.5, 123.5)]


@pytest.mark.parametrize("類型", tuple(目標))
def test_server_command_adapter驅動八種target且四個artifact同時可見(資料庫, 類型):
    """A20-02 public seam 對八種 target 皆由 server command 建立同一份 durable graph。"""
    命令服務 = SQLite遮蔽命令服務(
        遮蔽識別碼工廠=lambda: f"command-red-{類型}",
        稽核事件識別碼工廠=lambda: f"command-audit-{類型}",
        請求識別碼工廠=lambda: f"command-request-{類型}",
        時鐘=lambda: 234.5,
    )
    receipt = SQLite不可逆遮蔽服務(str(資料庫)).執行命令(
        命令服務,
        管理員識別碼="admin-command",
        冪等鍵=f"key-{類型}",
        端點識別碼="ep",
        呼叫識別碼="inv",
        目標類型=類型,
        目標列識別碼=目標[類型][2],
        JSON路徑="/secret/value",
        原因="privacy request",
    )
    assert receipt["redaction_id"] == f"command-red-{類型}"
    assert receipt["actor_id"] == "admin-command"
    assert _查詢(資料庫, "SELECT count(*) FROM redaction_idempotency_commands") == [(1,)]
    assert _查詢(資料庫, "SELECT count(*) FROM audit_events") == [(1,)]
    assert _查詢(資料庫, "SELECT count(*) FROM endpoint_redactions") == [(1,)]
    表格, 欄位, 列ID = 目標[類型]
    assert "RAW_G04" not in _查詢(
        資料庫, f"SELECT {欄位} FROM {表格} WHERE id=?", (列ID,)
    )[0][0]


@pytest.mark.parametrize("類型", ["metadata", "output", "error", "tool_result", "tool_error"])
def test_nullable_target在任何server_factory前422_provenance且四圖全零(資料庫, 類型):
    表格, 欄位, 列ID = 目標[類型]
    列ID = {"tool_result": "tool-error", "tool_error": "tool-ok"}.get(類型, 列ID)
    with sqlite3.connect(資料庫) as 連線:
        連線.execute(f"UPDATE {表格} SET {欄位}=NULL WHERE id=?", (列ID,))
    次數 = {"redaction": 0, "audit": 0, "request": 0, "clock": 0}

    def factory(名稱, 值):
        def call():
            次數[名稱] += 1
            return 值
        return call

    命令服務 = SQLite遮蔽命令服務(
        遮蔽識別碼工廠=factory("redaction", "red-null"),
        稽核事件識別碼工廠=factory("audit", "audit-null"),
        請求識別碼工廠=factory("request", "request-null"),
        時鐘=factory("clock", 234.5),
    )
    with pytest.raises(遮蔽目標內容無效, match="^遮蔽目標內容不存在$"):
        SQLite不可逆遮蔽服務(str(資料庫)).執行命令(
            命令服務, 管理員識別碼="admin-command", 冪等鍵=f"null-{類型}",
            端點識別碼="ep", 呼叫識別碼="inv", 目標類型=類型,
            目標列識別碼=列ID, JSON路徑="", 原因="privacy request",
        )
    assert 次數 == {"redaction": 0, "audit": 0, "request": 0, "clock": 0}
    assert _查詢(資料庫, "SELECT count(*) FROM redaction_idempotency_commands") == [(0,)]
    assert _查詢(資料庫, "SELECT count(*) FROM audit_events") == [(0,)]
    assert _查詢(資料庫, "SELECT count(*) FROM endpoint_redactions") == [(0,)]
    assert _查詢(資料庫, f"SELECT {欄位} FROM {表格} WHERE id=?", (列ID,)) == [(None,)]


def test_A20不同命令遮蔽同target_path保留專用衝突且第二筆mapping回滾(資料庫):
    """HTTP 409 provenance只來自transaction owner，不把其他ordinary failure誤分類。"""
    服務 = SQLite不可逆遮蔽服務(str(資料庫))

    def 命令服務(後綴):
        return SQLite遮蔽命令服務(
            遮蔽識別碼工廠=lambda: f"red-{後綴}",
            稽核事件識別碼工廠=lambda: f"audit-{後綴}",
            請求識別碼工廠=lambda: f"request-{後綴}",
            時鐘=lambda: 234.5,
        )

    共同 = {
        "管理員識別碼": "admin-command",
        "端點識別碼": "ep",
        "呼叫識別碼": "inv",
        "目標類型": "tool_result",
        "目標列識別碼": "tool-ok",
        "JSON路徑": "/secret/value",
        "原因": "privacy request",
    }
    服務.執行命令(命令服務("one"), 冪等鍵="key-one", **共同)

    with pytest.raises(遮蔽目標衝突, match="^遮蔽目標已由不同命令處理$") as 捕捉:
        服務.執行命令(命令服務("two"), 冪等鍵="key-two", **共同)
    assert 捕捉.value.__cause__ is 捕捉.value.__context__ is None
    assert _查詢(資料庫, "SELECT COUNT(*) FROM redaction_idempotency_commands") == [(1,)]
    assert _查詢(資料庫, "SELECT COUNT(*) FROM endpoint_redactions") == [(1,)]
    assert _查詢(資料庫, "SELECT COUNT(*) FROM audit_events") == [(1,)]


@pytest.mark.parametrize(("欄位", "值"), [
    ("request_fingerprint", "b" * 64),
    ("endpoint_id", "foreign-endpoint"),
])
def test_A20不同key同target前必須驗證完整durable_command_graph否則固定失敗(
    資料庫, 欄位, 值,
):
    服務 = SQLite不可逆遮蔽服務(str(資料庫))

    def 命令服務(後綴):
        return SQLite遮蔽命令服務(
            遮蔽識別碼工廠=lambda: f"red-corrupt-{後綴}",
            稽核事件識別碼工廠=lambda: f"audit-corrupt-{後綴}",
            請求識別碼工廠=lambda: f"request-corrupt-{後綴}",
            時鐘=lambda: 234.5,
        )

    共同 = {
        "管理員識別碼": "admin-command", "端點識別碼": "ep",
        "呼叫識別碼": "inv", "目標類型": "tool_result",
        "目標列識別碼": "tool-ok", "JSON路徑": "/secret/value",
        "原因": "privacy request",
    }
    服務.執行命令(命令服務("one"), 冪等鍵="corrupt-key-one", **共同)
    with sqlite3.connect(資料庫) as 連線:
        連線.execute(f"UPDATE redaction_idempotency_commands SET {欄位}=?", (值,))
    before = (
        _查詢(資料庫, "SELECT COUNT(*) FROM redaction_idempotency_commands"),
        _查詢(資料庫, "SELECT COUNT(*) FROM endpoint_redactions"),
        _查詢(資料庫, "SELECT COUNT(*) FROM audit_events"),
        _查詢(資料庫, "SELECT result_json FROM endpoint_tool_calls WHERE id='tool-ok'"),
    )
    with pytest.raises(不可逆遮蔽錯誤, match="^呼叫資料無法遮蔽$"):
        服務.執行命令(命令服務("two"), 冪等鍵="corrupt-key-two", **共同)
    after = (
        _查詢(資料庫, "SELECT COUNT(*) FROM redaction_idempotency_commands"),
        _查詢(資料庫, "SELECT COUNT(*) FROM endpoint_redactions"),
        _查詢(資料庫, "SELECT COUNT(*) FROM audit_events"),
        _查詢(資料庫, "SELECT result_json FROM endpoint_tool_calls WHERE id='tool-ok'"),
    )
    assert after == before


@pytest.mark.parametrize("corruption", ("C1-fingerprint", "C2-endpoint", "C3-endpoint-fingerprint"))
@pytest.mark.parametrize("relationship", ("same-exact", "same-different", "different-key"))
def test_A20_integrated完整graph先於三種request_relationship分類(
    資料庫, corruption, relationship,
):
    """Admission core 3×3：可偵測保存腐敗在任何關係下都固定internal。"""
    service = SQLite不可逆遮蔽服務(str(資料庫))
    common = {
        "管理員識別碼": "admin-command", "端點識別碼": "ep", "呼叫識別碼": "inv",
        "目標類型": "tool_result", "目標列識別碼": "tool-ok",
        "JSON路徑": "/secret/value", "原因": "privacy request",
    }
    service.執行命令(
        SQLite遮蔽命令服務(
            遮蔽識別碼工廠=lambda: "red-core", 稽核事件識別碼工廠=lambda: "audit-core",
            請求識別碼工廠=lambda: "request-core", 時鐘=lambda: 234.5,
        ), 冪等鍵="key-core", **common,
    )
    with sqlite3.connect(資料庫) as db:
        db.execute("INSERT INTO service_accounts(id,created_at) VALUES('sa-other',0)")
        db.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at) "
            "VALUES('ep-other','owner','sa-other','other','active',0,0)"
        )
        if corruption == "C1-fingerprint":
            db.execute("UPDATE redaction_idempotency_commands SET request_fingerprint=?", ("b" * 64,))
        elif corruption == "C2-endpoint":
            db.execute("UPDATE redaction_idempotency_commands SET endpoint_id='ep-other'")
        else:
            canonical = {
                "endpoint_id": "ep-other", "invocation_id": "inv", "json_path": "/secret/value",
                "reason": "privacy request", "target_row_id": "tool-ok", "target_type": "tool_result",
            }
            db.execute(
                "UPDATE redaction_idempotency_commands SET endpoint_id='ep-other',request_fingerprint=?",
                (計算正規JSON雜湊(canonical),),
            )
    before = (
        _查詢(資料庫, "SELECT * FROM redaction_idempotency_commands ORDER BY principal_id,idempotency_key"),
        _查詢(資料庫, "SELECT * FROM audit_events ORDER BY rowid"),
        _查詢(資料庫, "SELECT * FROM endpoint_redactions ORDER BY id"),
        _查詢(資料庫, "SELECT result_json FROM endpoint_tool_calls WHERE id='tool-ok'"),
    )
    calls = []
    def forbidden():
        calls.append(1)
        raise AssertionError("existing graph不得配置factory")
    incoming = dict(common)
    if relationship == "same-different":
        incoming["原因"] = "different privacy request"
    key = "key-other" if relationship == "different-key" else "key-core"
    with pytest.raises(不可逆遮蔽錯誤, match="^呼叫資料無法遮蔽$"):
        service.執行命令(
            SQLite遮蔽命令服務(
                遮蔽識別碼工廠=forbidden, 稽核事件識別碼工廠=forbidden,
                請求識別碼工廠=forbidden, 時鐘=forbidden,
            ), 冪等鍵=key, **incoming,
        )
    assert calls == []
    assert (
        _查詢(資料庫, "SELECT * FROM redaction_idempotency_commands ORDER BY principal_id,idempotency_key"),
        _查詢(資料庫, "SELECT * FROM audit_events ORDER BY rowid"),
        _查詢(資料庫, "SELECT * FROM endpoint_redactions ORDER BY id"),
        _查詢(資料庫, "SELECT result_json FROM endpoint_tool_calls WHERE id='tool-ok'"),
    ) == before


@pytest.mark.parametrize(("端點", "呼叫", "類型", "列ID"), [
    ("foreign-endpoint", "inv", "invocation_input", "inv"),
    ("ep", "missing-invocation", "invocation_input", "missing-invocation"),
    ("ep", "inv", "tool_result", "missing-tool"),
])
def test_A20_missing_foreign_target保留專用not_found且mapping零mutation(
    資料庫, 端點, 呼叫, 類型, 列ID,
):
    命令 = SQLite遮蔽命令服務(
        遮蔽識別碼工廠=lambda: "red-not-found",
        稽核事件識別碼工廠=lambda: "audit-not-found",
        請求識別碼工廠=lambda: "request-not-found",
        時鐘=lambda: 234.5,
    )
    with pytest.raises(遮蔽命令目標不存在, match="^遮蔽目標不存在$") as 捕捉:
        SQLite不可逆遮蔽服務(str(資料庫)).執行命令(
            命令, 管理員識別碼="admin-command", 冪等鍵="key-not-found",
            端點識別碼=端點, 呼叫識別碼=呼叫, 目標類型=類型,
            目標列識別碼=列ID, JSON路徑="/secret/value", 原因="privacy request",
        )
    assert 捕捉.value.__cause__ is 捕捉.value.__context__ is None
    assert _查詢(資料庫, "SELECT COUNT(*) FROM redaction_idempotency_commands") == [(0,)]
    assert _查詢(資料庫, "SELECT COUNT(*) FROM endpoint_redactions") == [(0,)]
    assert _查詢(資料庫, "SELECT COUNT(*) FROM audit_events") == [(0,)]


@pytest.mark.parametrize("位置,值", [(1,"red-2"),(2,"audit-2"),(3,"admin-2"),(4,"request-2"),
                                      (8,"/keep"),(9,"other reason"),(10,124.0)])
def test_只有所有識別與稽核語意完全相同才是冪等重試(資料庫, 位置, 值):
    服務 = SQLite不可逆遮蔽服務(str(資料庫))
    原參數 = _參數("invocation_input", "/secret/value")
    first = 服務.redact(*原參數)
    assert 服務.redact(*原參數) == first
    衝突 = list(原參數); 衝突[位置] = 值
    with pytest.raises(不可逆遮蔽錯誤) as error:
        服務.redact(*衝突)
    assert error.value.args == ("呼叫資料無法遮蔽",)
    assert error.value.__cause__ is error.value.__context__ is None
    assert _查詢(資料庫, "SELECT count(*) FROM endpoint_redactions") == [(1,)]
    assert _查詢(資料庫, "SELECT count(*) FROM audit_events") == [(1,)]


def test_audit失敗會回滾且不改payload或建立ledger(資料庫):
    服務 = SQLite不可逆遮蔽服務(str(資料庫))
    服務.redact(*_參數("metadata", "", redaction="red-other"))
    before = _查詢(資料庫, "SELECT input_json FROM endpoint_invocations")
    with pytest.raises(不可逆遮蔽錯誤):
        服務.redact(*_參數("invocation_input", "", redaction="red-new"))
    assert _查詢(資料庫, "SELECT input_json FROM endpoint_invocations") == before == [(原文,)]
    assert _查詢(資料庫, "SELECT id FROM endpoint_redactions") == [("red-other",)]


def test_payload或ledger失敗會回滾同交易audit且既有墓碑不變(資料庫):
    服務 = SQLite不可逆遮蔽服務(str(資料庫))
    服務.redact(*_參數("invocation_input", "/secret/value"))
    before = _查詢(資料庫, "SELECT input_json FROM endpoint_invocations")
    with pytest.raises(不可逆遮蔽錯誤):
        服務.redact(*_參數("invocation_input", "/keep", redaction="red-2", audit="audit-2"))
    assert _查詢(資料庫, "SELECT input_json FROM endpoint_invocations") == before
    assert _查詢(資料庫, "SELECT id FROM audit_events") == [("audit-1",)]


@pytest.mark.parametrize("payload", ['{"a":1,"a":2}', '{"x":NaN}', "[" * 18 + "0" + "]" * 18,
                                      '"' + "x" * 1_048_577 + '"'])
def test_重複鍵非有限過深與超量JSON全部在寫入前失敗(資料庫, payload):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        連線.execute("PRAGMA ignore_check_constraints=ON")
        連線.execute("UPDATE endpoint_invocations SET input_json=? WHERE id='inv'", (payload,))
    with pytest.raises(不可逆遮蔽錯誤):
        SQLite不可逆遮蔽服務(str(資料庫)).redact(*_參數("invocation_input", ""))
    assert _查詢(資料庫, "SELECT count(*) FROM audit_events") == [(0,)]
    assert _查詢(資料庫, "SELECT count(*) FROM endpoint_redactions") == [(0,)]


@pytest.mark.parametrize("改動", [
    "DROP TRIGGER redacted_invocation_payload_no_update",
    "DROP INDEX idx_endpoint_redactions_audit",
    "ALTER TABLE endpoint_redactions ADD COLUMN drift TEXT",
])
def test_schema索引觸發器或欄位漂移皆失敗關閉(資料庫, 改動):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        連線.execute(改動)
    with pytest.raises(不可逆遮蔽錯誤):
        SQLite不可逆遮蔽服務(str(資料庫)).redact(*_參數("invocation_input", ""))
    assert _查詢(資料庫, "SELECT input_json FROM endpoint_invocations") == [(原文,)]


def test_DB觸發器禁止還原payload以及更新或刪除墓碑(資料庫):
    SQLite不可逆遮蔽服務(str(資料庫)).redact(*_參數("invocation_input", ""))
    for sql in ("UPDATE endpoint_invocations SET input_json='{}' WHERE id='inv'",
                "UPDATE endpoint_redactions SET reason='x'", "DELETE FROM endpoint_redactions"):
        with closing(sqlite3.connect(資料庫)) as 連線, pytest.raises(sqlite3.IntegrityError), 連線:
            連線.execute(sql)


@pytest.mark.parametrize("類型", ("run_event", "tool_arguments", "tool_result", "tool_error"))
def test_已遮蔽child禁止刪除與身分重用且帳本稽核不變(資料庫, 類型):
    表格, 欄位, 列ID = 目標[類型]
    SQLite不可逆遮蔽服務(str(資料庫)).redact(*_參數(類型, ""))
    before = (_查詢(資料庫, "SELECT * FROM endpoint_redactions"),
              _查詢(資料庫, "SELECT * FROM audit_events"))
    with closing(sqlite3.connect(資料庫)) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError), 連線:
            連線.execute(f"DELETE FROM {表格} WHERE id=?", (列ID,))
        with pytest.raises(sqlite3.IntegrityError), 連線:
            連線.execute(f"UPDATE {表格} SET {欄位}='{{}}' WHERE id=?", (列ID,))
        with pytest.raises(sqlite3.IntegrityError), 連線:
            if 表格 == "run_events":
                連線.execute("INSERT INTO run_events VALUES(?,?,?,?,?,?)",
                           (列ID, "inv", 99, "reuse", "{}", 9))
            else:
                連線.execute("INSERT INTO endpoint_tool_calls(id,invocation_id,sequence_number,"
                           "tool_name,arguments_json,outcome,created_at) VALUES(?,?,?,?,?,?,?)",
                           (列ID, "inv", 99, "reuse", "{}", "success", 9))
    assert (_查詢(資料庫, "SELECT * FROM endpoint_redactions"),
            _查詢(資料庫, "SELECT * FROM audit_events")) == before


def _投影payload(結果, 類型):
    if 類型 == "invocation_input": return 結果["input"]
    if 類型 == "metadata": return 結果["metadata"]
    if 類型 == "output": return 結果["output"]
    if 類型 == "error": return 結果["error"]
    if 類型 == "run_event": return 結果["run_events"][0]["payload"]
    工具 = next(項 for 項 in 結果["tool_calls"] if 項["id"] == 目標[類型][2])
    return 工具[{"tool_arguments": "arguments", "tool_result": "result", "tool_error": "error"}[類型]]


@pytest.mark.parametrize("類型", tuple(目標))
@pytest.mark.parametrize("路徑", ("", "/secret/value"))
def test_遮蔽後管理員raw只回傳權威墓碑且無原文(資料庫, 類型, 路徑):
    SQLite不可逆遮蔽服務(str(資料庫)).redact(*_參數(類型, 路徑))
    結果 = SQLite呼叫查詢投影(str(資料庫)).查詢管理員原始資料(True, "ep", "inv")
    payload = _投影payload(結果, 類型)
    tombstone = {"$tombstone": {"redaction_id": "red-1", "redacted_at": 123.5}}
    assert payload == tombstone if 路徑 == "" else payload["secret"]["value"] == tombstone
    assert "RAW_G04" not in json.dumps(payload)


def _暫停防護並竄改(資料庫, trigger, sql, params=()):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        trigger_sql = 連線.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (trigger,)
        ).fetchone()[0]
        連線.execute(f'DROP TRIGGER "{trigger}"')
        連線.execute(sql, params)
        連線.execute(trigger_sql)


完整graph腐敗類別 = (
    *(f"mapping:{field}" for field in (
        "redaction_id", "audit_event_id", "request_id", "endpoint_id", "invocation_id",
        "target_type", "target_row_id", "json_path", "reason", "principal_id", "first_seen_at",
    )),
    *(f"ledger:{field}" for field in (
        "id", "invocation_id", "target_type", "target_row_id", "json_path", "reason",
        "actor_type", "actor_id", "audit_event_id", "is_tombstone", "redacted_at",
    )),
    *(f"audit:{field}" for field in (
        "event_id", "occurred_at", "action", "outcome", "actor_type", "actor_id",
        "resource_type", "resource_id", "request_id", "endpoint_id", "invocation_id",
        "metadata_json", "created_at",
    )),
    "payload:redaction_id", "payload:redacted_at", "payload:shape",
)


def _竄改完整graph類別(資料庫, descriptor):
    family, field = descriptor.split(":", 1)
    if family == "mapping":
        canonical = {
            "endpoint_id": "ep", "invocation_id": "inv", "json_path": "/secret/value",
            "reason": "privacy request", "target_row_id": "tool-ok", "target_type": "tool_result",
        }
        values = {
            "redaction_id": "red-forged", "audit_event_id": "audit-forged",
            "request_id": "request-forged", "endpoint_id": "ep-forged",
            "invocation_id": "inv-forged", "target_type": "tool_arguments",
            "target_row_id": "tool-error", "json_path": "/secret",
            "reason": "forged privacy request", "principal_id": "admin-forged",
            "first_seen_at": 999.0,
        }
        with sqlite3.connect(資料庫) as db:
            if field in canonical:
                canonical[field] = values[field]
                db.execute(
                    f"UPDATE redaction_idempotency_commands SET {field}=?,request_fingerprint=?",
                    (values[field], 計算正規JSON雜湊(canonical)),
                )
            else:
                db.execute(f"UPDATE redaction_idempotency_commands SET {field}=?", (values[field],))
        return
    if family == "ledger":
        values = {
            "id": "red-forged", "invocation_id": "inv-forged", "target_type": "tool_arguments",
            "target_row_id": "tool-error", "json_path": "/secret", "reason": "forged privacy request",
            "actor_type": "user", "actor_id": "admin-forged", "audit_event_id": "audit-forged",
            "is_tombstone": 0, "redacted_at": 999.0,
        }
        _暫停防護並竄改(
            資料庫, "endpoint_redactions_no_update",
            f"UPDATE endpoint_redactions SET {field}=?", (values[field],),
        )
        return
    if family == "audit":
        values = {
            "event_id": "audit-forged", "occurred_at": 999.0, "action": "audit.forged",
            "outcome": "failed", "actor_type": "admin", "actor_id": "admin-forged",
            "resource_type": "endpoint.forged", "resource_id": "red-forged",
            "request_id": "request-forged", "endpoint_id": "ep-forged",
            "invocation_id": "inv-forged", "metadata_json": "{}", "created_at": 999.0,
        }
        _暫停防護並竄改(
            資料庫, "audit_events_no_update",
            f"UPDATE audit_events SET {field}=?", (values[field],),
        )
        return
    tombstone = {
        "redaction_id": "red-semantic", "redacted_at": 234.5,
    }
    if field == "redaction_id":
        tombstone["redaction_id"] = "red-forged"
        value = {"keep": 1, "secret": {"value": {"$tombstone": tombstone}}}
    elif field == "redacted_at":
        tombstone["redacted_at"] = 999.0
        value = {"keep": 1, "secret": {"value": {"$tombstone": tombstone}}}
    else:
        value = {"keep": 1, "secret": {"value": {"restored": "CONTROLLED"}}}
    _暫停防護並竄改(
        資料庫, "redacted_tool_call_no_update",
        "UPDATE endpoint_tool_calls SET result_json=? WHERE id='tool-ok'",
        (json.dumps(value, sort_keys=True, separators=(",", ":")),),
    )


@pytest.mark.parametrize("descriptor", 完整graph腐敗類別)
@pytest.mark.parametrize("relationship", ("same-exact", "same-different", "different-key"))
def test_A20完整semantic_graph_38類別交叉三種request關係固定internal(
    資料庫, descriptor, relationship,
):
    """38×3=114列，所有可偵測冗餘binding都只由同一transaction validator裁決。"""
    service = SQLite不可逆遮蔽服務(str(資料庫))
    common = {
        "管理員識別碼": "admin-semantic", "端點識別碼": "ep", "呼叫識別碼": "inv",
        "目標類型": "tool_result", "目標列識別碼": "tool-ok",
        "JSON路徑": "/secret/value", "原因": "privacy request",
    }
    service.執行命令(
        SQLite遮蔽命令服務(
            遮蔽識別碼工廠=lambda: "red-semantic",
            稽核事件識別碼工廠=lambda: "audit-semantic",
            請求識別碼工廠=lambda: "request-semantic", 時鐘=lambda: 234.5,
        ), 冪等鍵="key-semantic", **common,
    )
    _竄改完整graph類別(資料庫, descriptor)
    before = (
        _查詢(資料庫, "SELECT * FROM redaction_idempotency_commands ORDER BY principal_id,idempotency_key"),
        _查詢(資料庫, "SELECT * FROM audit_events ORDER BY rowid"),
        _查詢(資料庫, "SELECT * FROM endpoint_redactions ORDER BY id"),
        _查詢(資料庫, "SELECT result_json FROM endpoint_tool_calls WHERE id='tool-ok'"),
    )
    calls = []
    def forbidden():
        calls.append(1)
        raise AssertionError("corrupt existing graph不得配置factory")
    incoming = dict(common)
    if relationship == "same-different":
        incoming["原因"] = "different privacy request"
    key = "key-other" if relationship == "different-key" else "key-semantic"
    with pytest.raises(不可逆遮蔽錯誤, match="^呼叫資料無法遮蔽$"):
        service.執行命令(
            SQLite遮蔽命令服務(
                遮蔽識別碼工廠=forbidden, 稽核事件識別碼工廠=forbidden,
                請求識別碼工廠=forbidden, 時鐘=forbidden,
            ), 冪等鍵=key, **incoming,
        )
    assert calls == []
    assert (
        _查詢(資料庫, "SELECT * FROM redaction_idempotency_commands ORDER BY principal_id,idempotency_key"),
        _查詢(資料庫, "SELECT * FROM audit_events ORDER BY rowid"),
        _查詢(資料庫, "SELECT * FROM endpoint_redactions ORDER BY id"),
        _查詢(資料庫, "SELECT result_json FROM endpoint_tool_calls WHERE id='tool-ok'"),
    ) == before
    assert _查詢(
        資料庫, "SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN "
        "('endpoint_redactions_no_update','audit_events_no_update','redacted_tool_call_no_update')"
    ) == [(3,)]


def test_恢復exact觸發器後raw已還原仍使投影與稽核閘門固定失敗(資料庫):
    SQLite不可逆遮蔽服務(str(資料庫)).redact(*_參數("invocation_input", ""))
    _暫停防護並竄改(資料庫, "redacted_invocation_payload_no_update",
                 "UPDATE endpoint_invocations SET input_json=? WHERE id='inv'", (原文,))
    投影 = SQLite呼叫查詢投影(str(資料庫))
    with pytest.raises(查詢投影錯誤):
        投影.查詢管理員原始資料(True, "ep", "inv")
    callback輸出 = []
    def detail(endpoint_id, invocation_id):
        結果 = 投影.查詢管理員原始資料(True, endpoint_id, invocation_id)
        callback輸出.append(結果)
        return 結果
    閘門 = 管理員原始資料稽核閘門(
        SQLite稽核服務(str(資料庫)), detail, lambda _端點, _呼叫: True,
    )
    with pytest.raises(查詢投影錯誤):
        閘門.查詢管理員原始資料(True, "admin", "request-view", "audit-view", 200,
                           "ep", "inv")
    assert callback輸出 == []


def test_墓碑仍在但ledger遭刪除並恢復exact觸發器時投影失敗關閉(資料庫):
    SQLite不可逆遮蔽服務(str(資料庫)).redact(*_參數("invocation_input", ""))
    _暫停防護並竄改(資料庫, "endpoint_redactions_no_delete",
                 "DELETE FROM endpoint_redactions")
    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(資料庫)).查詢管理員原始資料(True, "ep", "inv")


def test_稽核動態型別在任何payload實體化前失敗關閉(monkeypatch, 資料庫):
    from 繁中代理.發布介面.治理 import 查詢投影

    SQLite不可逆遮蔽服務(str(資料庫)).redact(*_參數("invocation_input", ""))
    _暫停防護並竄改(資料庫, "audit_events_no_update",
                 "UPDATE audit_events SET request_id=x'5241575F474F34'")
    解析次數 = []
    monkeypatch.setattr(查詢投影, "_解析可空JSON", lambda *_a, **_k: 解析次數.append(1))
    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(資料庫)).查詢管理員原始資料(True, "ep", "inv")
    assert 解析次數 == []


@pytest.mark.parametrize(("trigger", "sql"), [
    ("endpoint_redactions_no_update", "UPDATE endpoint_redactions SET actor_id='forged-admin'"),
    ("audit_events_no_update", "UPDATE audit_events SET actor_id='forged-admin'"),
])
def test_A20恢復exact觸發器後ledger與audit_actor不一致在materialize前失敗關閉(
    monkeypatch, 資料庫, trigger, sql,
):
    from 繁中代理.發布介面.治理 import 查詢投影

    SQLite不可逆遮蔽服務(str(資料庫)).redact(*_參數("invocation_input", ""))
    _暫停防護並竄改(資料庫, trigger, sql)
    解析次數 = []
    monkeypatch.setattr(查詢投影, "_解析可空JSON", lambda *_a, **_k: 解析次數.append(1))

    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(資料庫)).查詢管理員原始資料(True, "ep", "inv")
    assert 解析次數 == []


@pytest.mark.parametrize("欄位,值", [
    ("json_path", "/forged"), ("id", "forged-redaction"), ("redacted_at", 999.0),
])
def test_恢復exact帳本觸發器後偽造路徑身分時間仍失敗關閉(資料庫, 欄位, 值):
    SQLite不可逆遮蔽服務(str(資料庫)).redact(*_參數("invocation_input", ""))
    _暫停防護並竄改(資料庫, "endpoint_redactions_no_update",
                 f"UPDATE endpoint_redactions SET {欄位}=?", (值,))
    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(資料庫)).查詢管理員原始資料(True, "ep", "inv")


@pytest.mark.parametrize("改動", [
    "DROP TRIGGER redacted_run_event_no_delete",
    "DROP INDEX idx_endpoint_redactions_audit",
])
def test_管理員raw遇遮蔽觸發器或索引缺失皆失敗關閉(資料庫, 改動):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        連線.execute(改動)
    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(資料庫)).查詢管理員原始資料(True, "ep", "inv")


def test_並行相同請求只建立一份audit與墓碑(資料庫):
    def run(_):
        return SQLite不可逆遮蔽服務(str(資料庫)).redact(
            *_參數("invocation_input", "/secret/value"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(run, range(4)))
    assert len({item["original_sha256"] for item in receipts}) == 1
    assert _查詢(資料庫, "SELECT count(*) FROM audit_events") == [(1,)]
    assert _查詢(資料庫, "SELECT count(*) FROM endpoint_redactions") == [(1,)]


@pytest.mark.parametrize("控制型別", [asyncio.CancelledError, KeyboardInterrupt, SystemExit, GeneratorExit])
def test_cancellation與KISG保持exact_identity與args(monkeypatch, 資料庫, 控制型別):
    control = 控制型別("CONTROL_G04")
    monkeypatch.setattr(遮蔽模組, "_開啟既有資料庫", lambda _path: (_ for _ in ()).throw(control))
    with pytest.raises(控制型別) as error:
        SQLite不可逆遮蔽服務(str(資料庫)).redact(*_參數("invocation_input", ""))
    assert error.value is control and error.value.args == ("CONTROL_G04",)
    assert error.value.__cause__ is error.value.__context__ is None


@pytest.mark.parametrize(
    "錯誤型別", [RuntimeError, asyncio.CancelledError, KeyboardInterrupt, SystemExit, GeneratorExit],
)
def test_existing_graph_validator讀取墓碑後失敗不讓production_frames保留raw且控制同一物件(
    monkeypatch, 資料庫, 錯誤型別,
):
    """Observer在validator已materialize payload後檢查frames，避免只測fresh factory邊界。"""
    marker = "SIBLING_MARKER_A20_04"
    with sqlite3.connect(資料庫) as connection:
        changed = connection.execute(
            "UPDATE endpoint_tool_calls SET result_json=? WHERE id='tool-ok'",
            (json.dumps({"secret": {"value": "RAW_G04"}, "sibling": marker}),),
        )
        assert changed.rowcount == 1
    service = SQLite不可逆遮蔽服務(str(資料庫))
    command = SQLite遮蔽命令服務(
        遮蔽識別碼工廠=lambda: "red-frame", 稽核事件識別碼工廠=lambda: "audit-frame",
        請求識別碼工廠=lambda: "request-frame", 時鐘=lambda: 234.5,
    )
    params = {
        "管理員識別碼": "admin-frame", "冪等鍵": "key-frame", "端點識別碼": "ep",
        "呼叫識別碼": "inv", "目標類型": "tool_result", "目標列識別碼": "tool-ok",
        "JSON路徑": "/secret/value", "原因": "privacy request",
    }
    service.執行命令(command, **params)
    before = (
        _查詢(資料庫, "SELECT * FROM redaction_idempotency_commands ORDER BY principal_id,idempotency_key"),
        _查詢(資料庫, "SELECT * FROM audit_events ORDER BY rowid"),
        _查詢(資料庫, "SELECT * FROM endpoint_redactions ORDER BY id"),
        _查詢(資料庫, "SELECT result_json FROM endpoint_tool_calls WHERE id='tool-ok'"),
    )
    assert marker in before[3][0][0]
    injected = 錯誤型別("CONTROLLED_VALIDATOR_FAILURE")
    observed = {"frames": [], "violations": []}

    def contains_marker(value, seen):
        if id(value) in seen:
            return False
        seen.add(id(value))
        if type(value) is str:
            return marker in value
        if type(value) in (tuple, list, set, frozenset):
            return any(contains_marker(item, seen) for item in value)
        if type(value) is dict:
            return any(contains_marker(key, seen) or contains_marker(item, seen)
                       for key, item in value.items())
        for name in getattr(type(value), "__slots__", ()):
            if hasattr(value, name) and contains_marker(getattr(value, name), seen):
                return True
        closure = getattr(value, "__closure__", None)
        if type(closure) is tuple:
            for cell in closure:
                try:
                    cell_value = cell.cell_contents
                except ValueError:
                    continue
                if contains_marker(cell_value, seen):
                    return True
        return False

    def inspect_propagated(error):
        frames = []
        traceback = error.__traceback__
        while traceback is not None:
            frame = traceback.tb_frame
            if "/繁中代理/" in frame.f_code.co_filename:
                frames.append(frame.f_code.co_name)
                for value in tuple(frame.f_locals.values()):
                    assert not contains_marker(value, set())
            traceback = traceback.tb_next
        for linked in (error.__cause__, error.__context__):
            if linked is not None:
                assert not contains_marker(linked, set())
        return frames

    def observe_then_fail(*_args):
        frame = inspect.currentframe()
        assert frame is not None
        frame = frame.f_back
        while frame is not None:
            if "/繁中代理/" in frame.f_code.co_filename:
                observed["frames"].append(frame.f_code.co_name)
                for value in tuple(frame.f_locals.values()):
                    if contains_marker(value, set()):
                        observed["violations"].append(frame.f_code.co_name)
            frame = frame.f_back
        raise injected

    monkeypatch.setattr(遮蔽模組, "_確認墓碑", observe_then_fail)
    if 錯誤型別 is RuntimeError:
        with pytest.raises(不可逆遮蔽錯誤, match="^呼叫資料無法遮蔽$") as caught:
            service.執行命令(command, **params)
    else:
        with pytest.raises(錯誤型別) as caught:
            service.執行命令(command, **params)
        assert caught.value is injected
    propagated_frames = inspect_propagated(caught.value)
    assert "執行命令" in propagated_frames
    assert "_驗證完整既有命令圖" in observed["frames"]
    assert "執行命令" in observed["frames"]
    assert observed["violations"] == []
    assert (
        _查詢(資料庫, "SELECT * FROM redaction_idempotency_commands ORDER BY principal_id,idempotency_key"),
        _查詢(資料庫, "SELECT * FROM audit_events ORDER BY rowid"),
        _查詢(資料庫, "SELECT * FROM endpoint_redactions ORDER BY id"),
        _查詢(資料庫, "SELECT result_json FROM endpoint_tool_calls WHERE id='tool-ok'"),
    ) == before


class StrSubclass(str):
    pass


@pytest.mark.parametrize("位置,值", [(0,1),(1,StrSubclass("red")),(8,"bad~2path"),
                                      (9,"sk_secret"),(10,float("inf"))])
def test_授權型別路徑原因時間敵對輸入固定拒絕且無副作用(資料庫, 位置, 值):
    args = list(_參數("invocation_input", "")); args[位置] = 值
    with pytest.raises(不可逆遮蔽錯誤) as error:
        SQLite不可逆遮蔽服務(str(資料庫)).redact(*args)
    assert error.value.args == ("呼叫資料無法遮蔽",)
    assert error.value.__cause__ is error.value.__context__ is None
    assert _查詢(資料庫, "SELECT count(*) FROM audit_events") == [(0,)]
