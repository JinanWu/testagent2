"""GOV SQLite owner-safe diagnostics 的分頁、授權與資料外洩回歸測試。"""

import base64
import hashlib
import hmac
import json
import sqlite3
from contextlib import closing

import pytest

from 繁中代理.發布介面.治理.觀測供應器 import SQLite端點觀測查詢服務, 端點觀測查詢錯誤
from 繁中代理.發布介面.治理.觀測契約 import 診斷查詢成功, 端點不可見結果
from 繁中代理.發布介面.治理.遮蔽 import SQLite不可逆遮蔽服務
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫

金鑰 = b"diagnostics-cursor-key-exact-32b!"


@pytest.fixture
def 診斷資料庫(tmp_path):
    """建立同時間 keyset、raw children 與歷史非 active endpoint。"""
    路徑 = tmp_path / "diagnostics.sqlite"
    初始化發布介面資料庫(路徑)
    with closing(sqlite3.connect(路徑)) as 連線, 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        for 序號 in range(1, 4):
            連線.execute("INSERT INTO service_accounts VALUES(?,1,NULL)", (f"sa-{序號}",))
        for 序號, (擁有者, 狀態) in enumerate(
            (("owner-1", "active"), ("owner-2", "disabled"), ("owner-3", "archived")), 1
        ):
            端點, 版本 = f"ep-{序號}", f"ver-{序號}"
            連線.execute(
                "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
                (端點, 擁有者, f"sa-{序號}", f"slug-{序號}", 狀態, None, 1, 1, 60, 60),
            )
            連線.execute(
                "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (版本, 端點, 1, "需求", "系統", "[]", "[]", "{}", "rev", "{}", "{}",
                 "{}", None, "{}", 0, 擁有者, 1),
            )
            連線.execute("UPDATE published_endpoints SET current_version_id=? WHERE id=?", (版本, 端點))
        for 識別碼, 建立時間 in (("inv-c", 95.0), ("inv-b", 90.0), ("inv-a", 90.0), ("inv-old", 49.0)):
            連線.execute(
                "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,"
                "input_json,metadata_json,output_json,error_json,usage_json,latency_ms,created_at,completed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (識別碼, "ep-1", "ver-1", f"req-{識別碼}", "failed",
                 '{"input":"RAW_INPUT"}', '{"metadata":"RAW_METADATA"}',
                 '{"output":"RAW_OUTPUT"}',
                 '{"code":"safe_code","schema_path":"$.safe","internal":"RAW_ERROR"}',
                 '{"input_tokens":2,"output_tokens":3,"total_tokens":5,"estimated_cost_usd":"0"}',
                 12.5, 建立時間, 建立時間 + 1),
            )
            連線.execute(
                "INSERT INTO run_events VALUES(?,?,?,?,?,?)",
                (f"run-{識別碼}", 識別碼, 1, "model", '{"payload":"RAW_EVENT"}', 建立時間),
            )
            for 順序, 名稱 in enumerate(("z_tool", "a_tool", "z_tool"), 1):
                成功 = 順序 < 3
                連線.execute(
                    "INSERT INTO endpoint_tool_calls(id,invocation_id,sequence_number,tool_name,"
                    "arguments_json,outcome,result_json,error_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (f"tool-{識別碼}-{順序}", 識別碼, 順序, 名稱,
                     '{"arg":"RAW_ARG"}', "success" if 成功 else "error",
                     '{"result":"RAW_RESULT"}' if 成功 else None,
                     None if 成功 else '{"detail":"RAW_TOOL_ERROR"}', 建立時間),
                )
        for 端點, 版本, 時間 in (("ep-2", "ver-2", 80.0), ("ep-3", "ver-3", 81.0)):
            連線.execute(
                "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,input_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)", (f"inv-{端點}", 端點, 版本, f"req-{端點}", "succeeded", "{}", 時間),
            )
    return 路徑


def _服務(路徑, *, key=金鑰, clock=lambda: 100.0):
    return SQLite端點觀測查詢服務(str(路徑), 時鐘=clock, 游標簽章金鑰=key)


def _列出(服務, *, owner="owner-1", admin=False, endpoint="ep-1", limit=2, cursor=None, window=50):
    return 服務.列出端點診斷(
        擁有者使用者識別碼=owner, 是否管理者=admin, 端點識別碼=endpoint,
        視窗秒數=window, 數量上限=limit, 游標=cursor,
    )


def test_精確limit_desc_tie_order_cursor且跨頁無重複遺漏(診斷資料庫):
    服務 = _服務(診斷資料庫)
    第一頁 = _列出(服務).頁
    assert [項.invocation_id for 項 in 第一頁.items] == ["inv-c", "inv-b"]
    assert len(第一頁.items) == 2 and type(第一頁.next_cursor) is str
    第二頁 = _列出(服務, cursor=第一頁.next_cursor).頁
    assert [項.invocation_id for 項 in 第二頁.items] == ["inv-a"]
    assert 第二頁.next_cursor is None
    assert {項.invocation_id for 項 in 第一頁.items + 第二頁.items} == {"inv-a", "inv-b", "inv-c"}


def test_cursor釘選原始window不重讀clock且位置continuity(診斷資料庫):
    呼叫 = []
    def 時鐘():
        呼叫.append(1)
        return 100.0
    服務 = _服務(診斷資料庫, clock=時鐘)
    游標 = _列出(服務, limit=1).頁.next_cursor
    assert 呼叫 == [1]
    assert [項.invocation_id for 項 in _列出(服務, limit=1, cursor=游標).頁.items] == ["inv-b"]
    assert 呼叫 == [1]


@pytest.mark.parametrize(("owner", "endpoint"), (("owner-2", "ep-1"), ("owner-1", "missing")))
def test_missing與foreign為exact_typed_not_visible(診斷資料庫, owner, endpoint):
    結果 = _列出(_服務(診斷資料庫), owner=owner, endpoint=endpoint)
    assert type(結果) is 端點不可見結果


def test_admin可讀foreign且disabled_archived仍可讀歷史(診斷資料庫):
    服務 = _服務(診斷資料庫)
    管理員 = _列出(服務, owner="admin", admin=True, endpoint="ep-2")
    停用擁有者 = _列出(服務, owner="owner-2", endpoint="ep-2")
    封存擁有者 = _列出(服務, owner="owner-3", endpoint="ep-3")
    assert all(type(結果) is 診斷查詢成功 for 結果 in (管理員, 停用擁有者, 封存擁有者))
    assert [管理員.頁.items[0].invocation_id, 停用擁有者.頁.items[0].invocation_id,
            封存擁有者.頁.items[0].invocation_id] == ["inv-ep-2", "inv-ep-2", "inv-ep-3"]


def test_safe_allowlist_usage與tool_names精確(診斷資料庫):
    項 = _列出(_服務(診斷資料庫), limit=1).頁.items[0]
    assert tuple(項.__slots__) == ("invocation_id", "request_id", "endpoint_version_id", "status",
        "error_code", "schema_path", "latency_ms", "usage", "tool_names", "created_at",
        "completed_at", "redacted_fields")
    assert (項.error_code, 項.schema_path, 項.latency_ms, 項.usage.total_tokens) == (
        "safe_code", "$.safe", 12.5, 5)
    assert 項.tool_names == ("a_tool", "z_tool")


def _固定失敗(呼叫):
    with pytest.raises(端點觀測查詢錯誤) as 捕捉:
        呼叫()
    assert 捕捉.value.args == ("端點觀測不可取得",)
    assert 捕捉.value.__cause__ is 捕捉.value.__context__ is None
    return 捕捉.value


def _簽署(payload, key=金鑰):
    內容 = json.dumps(payload, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(內容 + hmac.new(key, 內容, hashlib.sha256).digest()).rstrip(b"=").decode()


def _簽署原始JSON(內容, key=金鑰):
    return base64.urlsafe_b64encode(
        內容 + hmac.new(key, 內容, hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")


def test_cursor拒絕相同解碼bytes的非canonical未使用pad_bits別名(診斷資料庫):
    游標 = _簽署([1, "ep-1", 50, 50.0, 100.0, 95.0, "inv-cx"])
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    assert len(游標) % 4 == 2
    別名 = 游標[:-1] + alphabet[alphabet.index(游標[-1]) + 1]
    補齊 = lambda token: token + "=" * (-len(token) % 4)
    assert 別名 != 游標
    assert base64.urlsafe_b64decode(補齊(別名)) == base64.urlsafe_b64decode(補齊(游標))
    _固定失敗(lambda: _列出(_服務(診斷資料庫), cursor=別名, limit=1))


def test_cursor拒絕正確簽章但非canonical_JSON且canonical_roundtrip成功(診斷資料庫):
    服務 = _服務(診斷資料庫)
    canonical = _列出(服務, limit=1).頁.next_cursor
    assert [項.invocation_id for 項 in _列出(服務, cursor=canonical, limit=1).頁.items] == ["inv-b"]
    非canonical內容 = b'[1, "ep-1", 50, 5e1, 1e2, 9.5e1, "inv-c"]'
    非canonical = _簽署原始JSON(非canonical內容)
    _固定失敗(lambda: _列出(服務, cursor=非canonical, limit=1))


@pytest.mark.parametrize(("payload", "window"), (
    ([1, "ep-1", 50.0, 50.0, 100.0, 95.0, "inv-c"], 50),
    ([1, "ep-1", True, 99.0, 100.0, 95.0, "inv-c"], 1),
    ([1.0, "ep-1", 50, 50.0, 100.0, 95.0, "inv-c"], 50),
    ([True, "ep-1", 50, 50.0, 100.0, 95.0, "inv-c"], 50),
    ([1, "ep-1", 50, 50.0, 100.0, 95, "inv-c"], 50),
))
def test_cursor拒絕正確簽章JSON的數值與bool純量別名(診斷資料庫, payload, window):
    壞游標 = _簽署(payload)
    _固定失敗(lambda: _列出(_服務(診斷資料庫), cursor=壞游標, limit=1, window=window))


@pytest.mark.parametrize("破壞", ("tamper", "wrong-key", "bad-base64", "oversize", "endpoint", "window", "position"))
def test_cursor完整scope簽章格式與大小驗證皆固定失敗且無raw(診斷資料庫, 破壞):
    服務 = _服務(診斷資料庫)
    游標 = _列出(服務, limit=1).頁.next_cursor
    if 破壞 == "tamper":
        壞游標 = 游標[:-1] + ("A" if 游標[-1] != "A" else "B")
    elif 破壞 == "wrong-key":
        壞游標 = 游標
        服務 = _服務(診斷資料庫, key=b"wrong-signing-key-exactly-32bytes")
    elif 破壞 == "bad-base64":
        壞游標 = "***"
    elif 破壞 == "oversize":
        壞游標 = "A" * 1025
    elif 破壞 == "endpoint":
        壞游標 = 游標
        error = _固定失敗(lambda: _列出(服務, endpoint="ep-2", owner="owner-2", cursor=壞游標, limit=1))
        assert "RAW_" not in repr(error)
        return
    elif 破壞 == "window":
        壞游標 = 游標
        error = _固定失敗(lambda: _列出(服務, cursor=壞游標, limit=1, window=49))
        assert "RAW_" not in repr(error)
        return
    else:
        壞游標 = _簽署([1, "ep-1", 50, 50.0, 100.0, -1.0, "inv-c"])
    error = _固定失敗(lambda: _列出(服務, cursor=壞游標, limit=1))
    assert "RAW_" not in repr(error)


def test_raw來源全部不進DTO_repr_exception或cursor(診斷資料庫):
    結果 = _列出(_服務(診斷資料庫), limit=1)
    公開 = repr(結果)
    assert 結果.頁.next_cursor is not None
    for marker in ("RAW_INPUT", "RAW_METADATA", "RAW_OUTPUT", "RAW_ERROR", "RAW_EVENT",
                   "RAW_ARG", "RAW_RESULT", "RAW_TOOL_ERROR"):
        assert marker not in 公開 and marker not in 結果.頁.next_cursor
    with closing(sqlite3.connect(診斷資料庫)) as 連線, 連線:
        連線.execute("PRAGMA ignore_check_constraints=ON")
        連線.execute("UPDATE endpoint_invocations SET latency_ms='RAW_FAILURE' WHERE id='inv-c'")
    error = _固定失敗(lambda: _列出(_服務(診斷資料庫), limit=1))
    assert "RAW_FAILURE" not in repr(error)


@pytest.mark.parametrize(("sql", "params"), (
    ("UPDATE endpoint_invocations SET status=x'6661696c6564' WHERE id='inv-c'", ()),
    ("UPDATE endpoint_invocations SET latency_ms=-1 WHERE id='inv-c'", ()),
    ("UPDATE endpoint_invocations SET completed_at=? WHERE id='inv-c'", (float("inf"),)),
    ("UPDATE endpoint_invocations SET usage_json='{\"input_tokens\":2,\"output_tokens\":3,\"total_tokens\":4,\"estimated_cost_usd\":\"0\"}' WHERE id='inv-c'", ()),
    ("UPDATE endpoint_invocations SET error_json='[]' WHERE id='inv-c'", ()),
    ("UPDATE endpoint_invocations SET error_json='{\"code\":7}' WHERE id='inv-c'", ()),
    ("UPDATE endpoint_tool_calls SET tool_name=x'5241575f544f4f4c' WHERE invocation_id='inv-c'", ()),
    ("UPDATE run_events SET payload_json='{\"x\":NaN}' WHERE invocation_id='inv-c'", ()),
))
def test_malformed_DB_types_nonfinite_negative_usage_custom_rows固定operational(診斷資料庫, sql, params):
    with closing(sqlite3.connect(診斷資料庫)) as 連線, 連線:
        連線.execute("PRAGMA ignore_check_constraints=ON")
        連線.execute(sql, params)
    _固定失敗(lambda: _列出(_服務(診斷資料庫), limit=1))


def _遮蔽錯誤欄位(路徑, *, redaction, audit, at, database):
    return SQLite不可逆遮蔽服務(str(database)).遮蔽(
        True, redaction, audit, "admin", f"req-{audit}", "inv-c", "error", "inv-c",
        路徑, "privacy", at,
    )


def test_error墓碑只發布固定redacted_fields且code_path皆空(診斷資料庫):
    _遮蔽錯誤欄位("", redaction="red-error", audit="audit-error", at=123.0, database=診斷資料庫)
    項 = _列出(_服務(診斷資料庫), limit=1).頁.items[0]
    assert (項.error_code, 項.schema_path, 項.redacted_fields) == (
        None, None, ("error_code", "schema_path"))
    assert "red-error" not in repr(項) and "privacy" not in repr(項)


@pytest.mark.parametrize("破壞", ("missing-ledger", "corrupt-audit"))
def test_墓碑ledger遺失或腐敗固定operational且不回partial(診斷資料庫, 破壞):
    _遮蔽錯誤欄位("", redaction="red-error", audit="audit-error", at=123.0, database=診斷資料庫)
    with closing(sqlite3.connect(診斷資料庫)) as 連線, 連線:
        if 破壞 == "missing-ledger":
            名稱 = "endpoint_redactions_no_delete"
            sql = 連線.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (名稱,)).fetchone()[0]
            連線.execute(f'DROP TRIGGER "{名稱}"')
            連線.execute("DELETE FROM endpoint_redactions")
        else:
            名稱 = "audit_events_no_update"
            sql = 連線.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (名稱,)).fetchone()[0]
            連線.execute(f'DROP TRIGGER "{名稱}"')
            連線.execute("UPDATE audit_events SET endpoint_id='ep-2'")
        連線.execute(sql)
    error = _固定失敗(lambda: _列出(_服務(診斷資料庫), limit=1))
    assert "ep-2" not in repr(error) and "red-error" not in repr(error)


@pytest.mark.parametrize("key", (b"short", bytearray(b"x" * 32), "x" * 32, None))
def test_游標金鑰只接受至少32個exact_bytes(診斷資料庫, key):
    _固定失敗(lambda: _服務(診斷資料庫, key=key))


def test_游標金鑰未持久化且不在服務repr(診斷資料庫):
    marker = b"KEY_PRIVATE_EXACT_32_BYTES_MARK!!"
    服務 = _服務(診斷資料庫, key=marker)
    _列出(服務, limit=1)
    assert marker.decode() not in repr(服務)
    assert marker not in 診斷資料庫.read_bytes()


def _assert框架locals無標記(error, marker):
    見到 = False
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") in (
            "繁中代理.發布介面.治理.觀測供應器", "繁中代理.發布介面.治理.觀測診斷"
        ):
            見到 = True
            assert marker not in repr(tuple(frame.f_locals.values()))
        traceback = traceback.tb_next
    assert 見到


def test_traceback_scanner_positive_control():
    class 假錯誤:
        __traceback__ = None
    with pytest.raises(AssertionError):
        _assert框架locals無標記(假錯誤(), "KNOWN_LEAK")


class 敵對Base(BaseException):
    pass


@pytest.mark.parametrize("error", (敵對Base("RAW_PROVIDER_FAILURE"), RuntimeError("RAW_PROVIDER_FAILURE")))
def test_provider普通與custom_Base失敗皆固定且無raw(monkeypatch, 診斷資料庫, error):
    from 繁中代理.發布介面.治理 import 觀測診斷
    def 失敗(_path):
        raise error
    monkeypatch.setattr(觀測診斷, "_開啟唯讀快照", 失敗)
    fixed = _固定失敗(lambda: _列出(_服務(診斷資料庫), limit=1))
    assert "RAW_PROVIDER_FAILURE" not in repr(fixed)


@pytest.mark.parametrize("控制型別", (KeyboardInterrupt, SystemExit, GeneratorExit))
def test_KISG_exact傳播且金鑰自所有診斷traceback_locals清除(monkeypatch, 診斷資料庫, 控制型別):
    from 繁中代理.發布介面.治理 import 觀測診斷
    key = b"KEY_TRACEBACK_PRIVATE_32_BYTES!!"
    control = 控制型別("CONTROL_EXACT")
    def 失敗(_path):
        raise control
    monkeypatch.setattr(觀測診斷, "_開啟唯讀快照", 失敗)
    with pytest.raises(控制型別) as 捕捉:
        _列出(_服務(診斷資料庫, key=key), limit=1)
    assert 捕捉.value is control and 捕捉.value.args == ("CONTROL_EXACT",)
    assert 捕捉.value.__cause__ is 捕捉.value.__context__ is None
    _assert框架locals無標記(捕捉.value, repr(key))


def test_late_KISG時raw_payload亦自建立項目traceback清除(monkeypatch, 診斷資料庫):
    from 繁中代理.發布介面.治理 import 觀測診斷
    control = KeyboardInterrupt("CONTROL_LATE")
    def 失敗(*_args):
        raise control
    monkeypatch.setattr(觀測診斷, "_核對投影墓碑", 失敗)
    with pytest.raises(KeyboardInterrupt) as 捕捉:
        _列出(_服務(診斷資料庫), limit=1)
    assert 捕捉.value is control
    for marker in ("RAW_INPUT", "RAW_METADATA", "RAW_OUTPUT", "RAW_ERROR", "RAW_EVENT",
                   "RAW_ARG", "RAW_RESULT", "RAW_TOOL_ERROR"):
        _assert框架locals無標記(捕捉.value, marker)


@pytest.mark.parametrize("位置", ("invocation", "child"))
def test_超大JSON在任何payload_SELECT與fetchone前固定失敗(monkeypatch, 診斷資料庫, 位置):
    from 繁中代理.發布介面.治理 import 查詢投影
    with closing(sqlite3.connect(診斷資料庫)) as 連線, 連線:
        if 位置 == "invocation":
            連線.execute("UPDATE endpoint_invocations SET error_json="
                       "'{\"x\":\"' || printf('%.*c',1048576,'x') || '\"}' WHERE id='inv-c'")
        else:
            連線.execute("UPDATE run_events SET payload_json="
                       "'{\"x\":\"' || printf('%.*c',1048576,'x') || '\"}' WHERE id='run-inv-c'")
    原始 = 查詢投影._建立連線
    payload查詢 = []
    payload讀取 = []
    class 記錄游標:
        def __init__(self, 游標, 是payload): self._游標, self._是payload = 游標, 是payload
        def fetchone(self):
            if self._是payload: payload讀取.append(1)
            return self._游標.fetchone()
        def __getattr__(self, 名稱): return getattr(self._游標, 名稱)
    class 記錄連線:
        def __init__(self, 連線): self._連線 = 連線
        def execute(self, SQL, *參數):
            是payload = (")),error_json,usage_json" in SQL
                       or ")),payload_json FROM run_events" in SQL)
            if 是payload: payload查詢.append(SQL)
            return 記錄游標(self._連線.execute(SQL, *參數), 是payload)
        def __getattr__(self, 名稱): return getattr(self._連線, 名稱)
    monkeypatch.setattr(查詢投影, "_建立連線", lambda *參數, **關鍵字: 記錄連線(原始(*參數, **關鍵字)))
    error = _固定失敗(lambda: _列出(_服務(診斷資料庫), limit=1))
    assert error.args == ("端點觀測不可取得",)
    assert payload查詢 == payload讀取 == []
