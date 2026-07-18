"""GOV G02 擁有者安全與管理員原始呼叫投影測試。"""

import sqlite3
from contextlib import closing

import pytest

from 繁中代理.發布介面.治理.查詢投影 import ProjectionAccessError, SQLite呼叫查詢投影
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


@pytest.fixture
def 呼叫資料庫(tmp_path):
    路徑 = tmp_path / "published.sqlite"
    初始化發布介面資料庫(路徑)
    with closing(sqlite3.connect(路徑)) as 連線, 連線:
        連線.execute("INSERT INTO service_accounts VALUES('sa-1',1,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("ep-1", "owner-1", "sa-1", "safe-endpoint", "active", None, 1, 1, 60, 60),
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ver-1", "ep-1", 1, "需求", "系統", "[]", "[]", "{}", "rev-1", "{}", "{}", "{}", None, "{}", 0, "owner-1", 1),
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver-1' WHERE id='ep-1'")
        連線.execute(
            "INSERT INTO endpoint_invocations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "inv-1", "ep-1", "ver-1", None, "req-1", "session-1", "message-1",
                "failed", '{"raw_input":"INPUT_SECRET"}',
                '{"raw_metadata":"METADATA_SECRET"}', '{"raw_output":"OUTPUT_SECRET"}',
                '{"code":"schema_invalid","schema_path":"$.answer","internal":"ERROR_SECRET"}',
                '{"total_tokens":17,"provider_detail":"USAGE_SECRET"}', 99,
                "a" * 64, 12.5, "price-v1", 10, 11,
            ),
        )
        連線.execute(
            "INSERT INTO run_events VALUES(?,?,?,?,?,?)",
            ("run-1", "inv-1", 1, "model.output", '{"raw":"RUN_SECRET"}', 10.25),
        )
        連線.execute(
            "INSERT INTO endpoint_tool_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "tool-1", "inv-1", "run-1", 1, "safe_tool", '{"token":"ARG_SECRET"}',
                "success", '{"secret":"RESULT_SECRET"}', None, 1.5, None, 10.5,
            ),
        )
    return 路徑

def test_擁有者診斷只回固定安全欄位與摘要(呼叫資料庫):
    結果 = SQLite呼叫查詢投影(str(呼叫資料庫)).查詢擁有者診斷(
        "owner-1", "ep-1", "inv-1"
    )

    assert 結果 == {
        "invocation": {"id": "inv-1", "request_id": "req-1", "session_id": "session-1"},
        "endpoint_version_id": "ver-1",
        "status": "failed",
        "error_code": "schema_invalid",
        "schema_path": "$.answer",
        "latency_ms": 12.5,
        "usage": {"total_tokens": 17},
        "tool_names": ["safe_tool"],
    }
    serialized = repr(結果)
    for marker in ("INPUT_SECRET", "METADATA_SECRET", "OUTPUT_SECRET", "ERROR_SECRET", "USAGE_SECRET", "ARG_SECRET", "RESULT_SECRET", "a" * 64):
        assert marker not in serialized

@pytest.mark.parametrize(
    ("owner_id", "endpoint_id", "invocation_id"),
    [
        ("owner-2", "ep-1", "inv-1"),
        ("owner-1", "ep-missing", "inv-1"),
        ("owner-1", "ep-1", "inv-missing"),
    ],
)
def test_擁有者外來與缺少直接識別碼統一失敗關閉(
    呼叫資料庫, owner_id, endpoint_id, invocation_id
):
    with pytest.raises(ProjectionAccessError) as error:
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢擁有者診斷(
            owner_id, endpoint_id, invocation_id
        )
    assert type(error.value) is ProjectionAccessError
    assert error.value.args == ("呼叫紀錄不可取得",)
    assert error.value.__cause__ is error.value.__context__ is None

def test_管理員原始投影保留權威payload且transport_neutral(呼叫資料庫):
    結果 = SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(
        True, "ep-1", "inv-1"
    )

    assert 結果["invocation"] == {
        "id": "inv-1", "request_id": "req-1", "session_id": "session-1"
    }
    assert 結果["endpoint_id"] == "ep-1"
    assert 結果["endpoint_version_id"] == "ver-1"
    assert 結果["message_id"] == "message-1"
    assert 結果["input"] == {"raw_input": "INPUT_SECRET"}
    assert 結果["metadata"] == {"raw_metadata": "METADATA_SECRET"}
    assert 結果["output"] == {"raw_output": "OUTPUT_SECRET"}
    assert 結果["error"]["internal"] == "ERROR_SECRET"
    assert 結果["usage"]["provider_detail"] == "USAGE_SECRET"
    assert 結果["run_events"][0]["payload"] == {"raw": "RUN_SECRET"}
    assert 結果["tool_calls"][0]["arguments"] == {"token": "ARG_SECRET"}
    assert 結果["tool_calls"][0]["result"] == {"secret": "RESULT_SECRET"}
    assert not ({"http_status", "headers", "body"} & 結果.keys())

@pytest.mark.parametrize(
    ("authorized", "endpoint_id", "invocation_id"),
    [
        (False, "ep-1", "inv-1"),
        (1, "ep-1", "inv-1"),
        ("admin", "ep-1", "inv-1"),
        (True, "ep-other", "inv-1"),
        (True, "ep-1", "inv-missing"),
    ],
)
def test_非精確管理員授權與缺少或錯誤範圍統一失敗關閉(
    呼叫資料庫, authorized, endpoint_id, invocation_id
):
    with pytest.raises(ProjectionAccessError) as error:
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(
            authorized, endpoint_id, invocation_id
        )
    assert error.value.args == ("呼叫紀錄不可取得",)
    assert error.value.__cause__ is error.value.__context__ is None


def test_缺少空檔symlink與schema漂移都失敗關閉(tmp_path, 呼叫資料庫):
    empty = tmp_path / "empty.sqlite"
    empty.touch()
    link = tmp_path / "link.sqlite"
    link.symlink_to(呼叫資料庫)
    for path in (tmp_path / "missing.sqlite", empty, link):
        with pytest.raises(ProjectionAccessError):
            SQLite呼叫查詢投影(str(path)).查詢擁有者診斷("owner-1", "ep-1", "inv-1")
    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        連線.execute("DROP INDEX idx_endpoint_invocations_status_created")
    for 呼叫 in (
        lambda: SQLite呼叫查詢投影(str(呼叫資料庫)).查詢擁有者診斷("owner-1", "ep-1", "inv-1"),
        lambda: SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(True, "ep-1", "inv-1"),
    ):
        with pytest.raises(ProjectionAccessError):
            呼叫()


def test_非管理員在任何資料庫callback前拒絕(monkeypatch, 呼叫資料庫):
    from 繁中代理.發布介面.治理 import 查詢投影

    calls = []
    monkeypatch.setattr(查詢投影, "_建立連線", lambda *a, **k: calls.append(1))
    with pytest.raises(ProjectionAccessError):
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(False, "ep-1", "inv-1")
    assert calls == []


@pytest.mark.parametrize("method", ["owner", "admin"])
def test_敵對自訂base固定失敗且控制流程保留identity(monkeypatch, 呼叫資料庫, method):
    from 繁中代理.發布介面.治理 import 查詢投影

    class 敵對Base(BaseException):
        pass

    def 執行():
        服務 = SQLite呼叫查詢投影(str(呼叫資料庫))
        if method == "owner":
            return 服務.查詢擁有者診斷("owner-1", "ep-1", "inv-1")
        return 服務.查詢管理員原始資料(True, "ep-1", "inv-1")

    def 丟出(error):
        def connect(*_args, **_kwargs):
            raise error
        monkeypatch.setattr(查詢投影, "_建立連線", connect)

    丟出(敵對Base("PRIVATE_MARKER"))
    with pytest.raises(ProjectionAccessError) as fixed:
        執行()
    assert fixed.value.args == ("呼叫紀錄不可取得",)
    assert fixed.value.__cause__ is fixed.value.__context__ is None
    for 類型 in (KeyboardInterrupt, SystemExit, GeneratorExit):
        control = 類型("CONTROL_MARKER")
        丟出(control)
        with pytest.raises(類型) as caught:
            執行()
        assert caught.value is control
        assert caught.value.args == ("CONTROL_MARKER",)
        assert caught.value.__cause__ is caught.value.__context__ is None


def test_擁有者動態儲存類型失敗而不輸出blob(呼叫資料庫):
    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        連線.execute("PRAGMA ignore_check_constraints=ON")
        連線.execute("UPDATE endpoint_invocations SET status=x'534543524554' WHERE id='inv-1'")
    with pytest.raises(ProjectionAccessError):
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢擁有者診斷("owner-1", "ep-1", "inv-1")


def test_投影原始碼不得使用select星號():
    import inspect
    from 繁中代理.發布介面.治理 import 查詢投影

    assert "SELECT *" not in inspect.getsource(查詢投影).upper()
