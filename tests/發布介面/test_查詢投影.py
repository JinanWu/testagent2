"""GOV G02 擁有者安全與管理員原始呼叫投影測試。"""

import sqlite3
from contextlib import closing
import traceback

import pytest

from 繁中代理.發布介面.治理.查詢投影 import 查詢投影錯誤, SQLite呼叫查詢投影
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
    with pytest.raises(查詢投影錯誤) as error:
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢擁有者診斷(
            owner_id, endpoint_id, invocation_id
        )
    assert type(error.value) is 查詢投影錯誤
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
    with pytest.raises(查詢投影錯誤) as error:
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
        with pytest.raises(查詢投影錯誤):
            SQLite呼叫查詢投影(str(path)).查詢擁有者診斷("owner-1", "ep-1", "inv-1")
    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        連線.execute("DROP INDEX idx_endpoint_invocations_status_created")
    for 呼叫 in (
        lambda: SQLite呼叫查詢投影(str(呼叫資料庫)).查詢擁有者診斷("owner-1", "ep-1", "inv-1"),
        lambda: SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(True, "ep-1", "inv-1"),
    ):
        with pytest.raises(查詢投影錯誤):
            呼叫()


def test_同名欄位與索引但缺少全域呼叫主鍵時兩種投影皆在payload前失敗(
    monkeypatch, 呼叫資料庫,
):
    from 繁中代理.發布介面.治理 import 查詢投影

    with closing(sqlite3.connect(呼叫資料庫)) as 連線:
        連線.execute("PRAGMA foreign_keys=OFF")
        建表語句 = 連線.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='endpoint_invocations'"
        ).fetchone()[0]
        with 連線:
            連線.execute("DROP TABLE endpoint_tool_calls")
            連線.execute("DROP TABLE run_events")
            連線.execute("ALTER TABLE endpoint_invocations RENAME TO genuine_invocations")
            欄位 = ",".join(列[1] for 列 in 連線.execute(
                "PRAGMA table_info(genuine_invocations)"
            ))
            連線.execute(建表語句.replace("id TEXT PRIMARY KEY", "id TEXT", 1))
            連線.execute(
                f"INSERT INTO endpoint_invocations({欄位}) SELECT {欄位} FROM genuine_invocations"
            )
            連線.execute("DROP TABLE genuine_invocations")
            連線.execute("CREATE INDEX idx_endpoint_invocations_endpoint_created ON endpoint_invocations(endpoint_id,created_at)")
            連線.execute("CREATE INDEX idx_endpoint_invocations_status_created ON endpoint_invocations(status,created_at)")
            連線.execute("CREATE INDEX idx_endpoint_invocations_credential_created ON endpoint_invocations(credential_id,created_at)")
    解析次數 = []
    原解析 = 查詢投影._解析可空JSON

    def 記錄解析(*引數, **關鍵字):
        解析次數.append(1)
        return 原解析(*引數, **關鍵字)

    monkeypatch.setattr(查詢投影, "_解析可空JSON", 記錄解析)
    服務 = SQLite呼叫查詢投影(str(呼叫資料庫))
    for 呼叫 in (
        lambda: 服務.查詢擁有者診斷("owner-1", "ep-1", "inv-1"),
        lambda: 服務.查詢管理員原始資料(True, "ep-1", "inv-1"),
    ):
        with pytest.raises(查詢投影錯誤) as 錯誤:
            呼叫()
        assert 錯誤.value.args == ("呼叫紀錄不可取得",)
    assert 解析次數 == []


def test_非管理員在任何資料庫callback前拒絕(monkeypatch, 呼叫資料庫):
    from 繁中代理.發布介面.治理 import 查詢投影

    calls = []
    monkeypatch.setattr(查詢投影, "_建立連線", lambda *a, **k: calls.append(1))
    with pytest.raises(查詢投影錯誤):
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
    with pytest.raises(查詢投影錯誤) as fixed:
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
    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢擁有者診斷("owner-1", "ep-1", "inv-1")


def test_投影原始碼不得使用select星號():
    import inspect
    from 繁中代理.發布介面.治理 import 查詢投影

    assert "SELECT *" not in inspect.getsource(查詢投影).upper()


class _清理連線代理:
    def __init__(self, 連線, *, 主要=None, 回滾=None, 關閉=None):
        self.連線, self.主要, self.回滾, self.關閉 = 連線, 主要, 回滾, 關閉
        self.順序 = []

    def execute(self, sql, *args):
        if self.主要 is not None and sql.startswith("SELECT version,name"):
            raise self.主要
        return self.連線.execute(sql, *args)

    def commit(self):
        return self.連線.commit()

    def rollback(self):
        self.順序.append("rollback")
        if self.回滾 is not None:
            raise self.回滾
        return self.連線.rollback()

    def close(self):
        self.順序.append("close")
        self.連線.close()
        if self.關閉 is not None:
            raise self.關閉


def _assert投影框架無標記(error, marker):
    for frame, _ in traceback.walk_tb(error.__traceback__):
        if frame.f_globals.get("__name__") == "繁中代理.發布介面.治理.查詢投影":
            assert marker not in repr(tuple(frame.f_locals.values()))


@pytest.mark.parametrize("method", ["owner", "admin"])
def test_主要自訂base仍依序rollback與close(monkeypatch, 呼叫資料庫, method):
    from 繁中代理.發布介面.治理 import 查詢投影

    class 敵對Base(BaseException):
        pass

    真實 = sqlite3.connect(呼叫資料庫, isolation_level=None)
    代理 = _清理連線代理(
        真實, 主要=敵對Base("PRIMARY_PRIVATE"),
        回滾=敵對Base("ROLLBACK_PRIVATE"), 關閉=敵對Base("CLOSE_PRIVATE"),
    )
    monkeypatch.setattr(查詢投影, "_建立連線", lambda *_a, **_k: 代理)
    服務 = SQLite呼叫查詢投影(str(呼叫資料庫))
    with pytest.raises(查詢投影錯誤):
        (服務.查詢擁有者診斷("owner-1", "ep-1", "inv-1") if method == "owner"
         else 服務.查詢管理員原始資料(True, "ep-1", "inv-1"))
    assert 代理.順序 == ["rollback", "close"]


@pytest.mark.parametrize("method", ["owner", "admin"])
def test_postcommit_close控制保留identity且清空helper敏感locals(
    monkeypatch, 呼叫資料庫, method,
):
    from 繁中代理.發布介面.治理 import 查詢投影

    marker = "OWNER_PRIVATE_MARKER" if method == "owner" else "INPUT_SECRET"
    if method == "owner":
        with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
            連線.execute("UPDATE published_endpoints SET owner_user_id=?", (marker,))
    control = KeyboardInterrupt(marker)
    代理 = _清理連線代理(sqlite3.connect(呼叫資料庫, isolation_level=None), 關閉=control)
    monkeypatch.setattr(查詢投影, "_建立連線", lambda *_a, **_k: 代理)
    服務 = SQLite呼叫查詢投影(str(呼叫資料庫))
    with pytest.raises(KeyboardInterrupt) as caught:
        (服務.查詢擁有者診斷(marker, "ep-1", "inv-1") if method == "owner"
         else 服務.查詢管理員原始資料(True, "ep-1", "inv-1"))
    assert caught.value is control
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert 代理.順序 == ["close"]
    _assert投影框架無標記(caught.value, marker)


@pytest.mark.parametrize("payload", ['{"x":NaN}', '{"x":1e999}', '{"x":1,"x":2}'])
def test_管理員原始JSON拒絕非有限與重複鍵(呼叫資料庫, payload):
    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        連線.execute("PRAGMA ignore_check_constraints=ON")
        連線.execute("UPDATE endpoint_invocations SET input_json=?", (payload,))
    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(True, "ep-1", "inv-1")


def test_管理員全部動態欄位與child列數都失敗關閉(monkeypatch, 呼叫資料庫):
    from 繁中代理.發布介面.治理 import 查詢投影

    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        連線.execute("PRAGMA ignore_check_constraints=ON")
        連線.execute("UPDATE endpoint_invocations SET message_id=x'534543524554'")
    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(True, "ep-1", "inv-1")
    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        連線.execute("UPDATE endpoint_invocations SET message_id='message-1'")
    monkeypatch.setattr(查詢投影, "_最大子列", 0)
    for 呼叫 in (
        lambda: SQLite呼叫查詢投影(str(呼叫資料庫)).查詢擁有者診斷("owner-1", "ep-1", "inv-1"),
        lambda: SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(True, "ep-1", "inv-1"),
    ):
        with pytest.raises(查詢投影錯誤):
            呼叫()


@pytest.mark.parametrize(("預算名稱", "預算值"), [("_最大JSON位元組", 120), ("_最大JSON節點", 12)])
def test_管理員跨多列聚合JSON預算超限會失敗並關閉真實資源(
    monkeypatch, 呼叫資料庫, 預算名稱, 預算值,
):
    from 繁中代理.發布介面.治理 import 查詢投影

    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        連線.execute("DELETE FROM endpoint_tool_calls")
        連線.execute("DELETE FROM run_events")
        連線.execute(
            "UPDATE endpoint_invocations SET input_json='{}',metadata_json=NULL,output_json=NULL,"
            "error_json=NULL,usage_json=NULL"
        )
        for 順序 in range(1, 10):
            連線.execute(
                "INSERT INTO run_events VALUES(?,?,?,?,?,?)",
                (f"run-{順序}", "inv-1", 順序, "chunk", '{"chunk":"abcdefghij"}', 20 + 順序),
            )
    真建立 = 查詢投影._建立連線
    代理列 = []

    def 建立代理(*引數, **關鍵字):
        代理 = _清理連線代理(真建立(*引數, **關鍵字))
        代理列.append(代理)
        return 代理

    monkeypatch.setattr(查詢投影, 預算名稱, 預算值)
    monkeypatch.setattr(查詢投影, "_建立連線", 建立代理)
    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(True, "ep-1", "inv-1")
    assert len(代理列) == 1
    assert 代理列[0].順序 == ["rollback", "close"]
    with pytest.raises(sqlite3.ProgrammingError):
        代理列[0].連線.execute("SELECT 1")
