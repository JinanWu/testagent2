"""GOV G04 R51/R82 不可逆、原子 payload 墓碑測試。"""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.治理 import 遮蔽 as 遮蔽模組
from 繁中代理.發布介面.治理.遮蔽 import SQLite不可逆遮蔽服務, 不可逆遮蔽錯誤
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


@pytest.mark.parametrize("控制型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_KISG保持exact_identity與args(monkeypatch, 資料庫, 控制型別):
    control = 控制型別("CONTROL_G04")
    monkeypatch.setattr(遮蔽模組, "_開啟既有資料庫", lambda _path: (_ for _ in ()).throw(control))
    with pytest.raises(控制型別) as error:
        SQLite不可逆遮蔽服務(str(資料庫)).redact(*_參數("invocation_input", ""))
    assert error.value is control and error.value.args == ("CONTROL_G04",)
    assert error.value.__cause__ is error.value.__context__ is None


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
